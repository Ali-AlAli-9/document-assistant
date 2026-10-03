"""Annotations that only some Python versions evaluate.

The container runs ``python:3.12-slim``; the development venv here is 3.14.
On 3.14 (PEP 649) annotations are evaluated lazily, so a module-level
annotation that names a lazily-imported class costs nothing. On 3.12 it is
evaluated while the module is imported and raises ``NameError``.

That divergence is invisible locally — ``manage.py check`` is clean and 209
tests pass — and it is only ever discovered by a 20-minute Render build, twice
already:

    NameError: name 'AskQuestionHandler' is not defined
      application/di.py:190  _ask_handler_cache: dict[str, AskQuestionHandler] = {}

``manage.py check`` cannot catch it (the local interpreter is the one that
forgives it) and neither can a test that merely imports the module. So this
reads the source instead, with ``ast``, and applies the rule the interpreter
applies: an annotation is evaluated at import time when it appears in a module
body, in a class body, or on a ``def`` — and the names it uses must already be
bound at that point in the file.

Deliberately not checked, because the interpreter does not evaluate them:
annotations of *local* variables (``x: T = ...`` inside a function), and
everything in a file that opted out with ``from __future__ import annotations``.
Those are covered here too, in the sense that opting out is a one-line, visible
decision rather than an accident of which Python happens to be installed.
"""

import ast
import builtins
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Directories that are not ours to lint: dependencies, build output, vendored
# data. The model's own snapshot under ./models is another Python project.
# _local holds throwaway measurement scripts: it is gitignored, it never enters
# the image, and it must not be able to fail this gate. Without it here, a
# scratch file changes the test count and could block a deploy over code that
# is never shipped.
SKIP_DIRS = {
    'venv', 'node_modules', 'frontend', 'staticfiles', 'media', 'models',
    '__pycache__', '.git', 'dist', 'build', '_local',
}


def _python_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob('*.py')
        if not SKIP_DIRS & set(path.relative_to(ROOT).parts)
    )


def _files() -> list[Path]:
    return [p for p in _python_files() if p.name != 'test_annotation_evaluation.py']


def _deferred(tree: ast.Module) -> bool:
    """Whether ``from __future__ import annotations`` is in effect."""
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == '__future__'
        and any(alias.name == 'annotations' for alias in node.names)
        for node in tree.body
    )


def _is_type_checking_guard(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.If) and isinstance(stmt.test, ast.Name) and (
        stmt.test.id == 'TYPE_CHECKING'
    )


def _target_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for element in target.elts:
            names |= _target_names(element)
        return names
    return set()


def _names_bound_by(stmt: ast.stmt) -> set[str]:
    """Names a single statement binds at runtime, ignoring if-TYPE_CHECKING."""
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        bound = set()
        for alias in stmt.names:
            if alias.name == '*':
                return {'*'}
            bound.add(alias.asname or alias.name.split('.')[0])
        return bound
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.name}
    if isinstance(stmt, ast.Assign):
        return set().union(*(_target_names(t) for t in stmt.targets)) or set()
    if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
        return _target_names(stmt.target)
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        return _target_names(stmt.target)
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return set().union(
            *(_target_names(item.optional_vars) for item in stmt.items
              if item.optional_vars is not None)
        ) if stmt.items else set()
    if isinstance(stmt, (ast.Global, ast.Nonlocal)):
        return set(stmt.names)
    if isinstance(stmt, ast.Try):
        return {h.name for h in stmt.handlers if h.name}
    if isinstance(stmt, ast.TypeAlias):  # `type X = ...` (3.12+)
        return {stmt.name.id} if isinstance(stmt.name, ast.Name) else set()
    return set()


def _annotation_nodes(stmt: ast.stmt) -> list[ast.expr]:
    """The annotations of ``stmt`` that the interpreter evaluates eagerly."""
    if isinstance(stmt, ast.AnnAssign):
        return [stmt.annotation] if stmt.annotation else []
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = stmt.args
        collected = [
            arg.annotation
            for arg in [
                *args.posonlyargs, *args.args, *args.kwonlyargs,
                args.vararg, args.kwarg,
            ]
            if arg is not None and arg.annotation is not None
        ]
        if stmt.returns is not None:
            collected.append(stmt.returns)
        return collected
    return []


def _referenced_names(annotation: ast.expr) -> set[str]:
    """Names an annotation expression reads.

    Attribute and subscript bases are included because they are reads too
    (``models.Document`` reads ``models``), while the attribute *name* is not a
    reference and literal subscripts are read as constants.
    """
    return {
        node.id
        for node in ast.walk(annotation)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _violations(tree: ast.Module) -> list[tuple[int, str]]:
    """(line, message) for each annotation that would raise NameError on 3.12."""
    if _deferred(tree):
        return []
    found: list[tuple[int, str]] = []
    # Names are collected in source order: an annotation may only use what is
    # already bound ABOVE it, exactly as at import time.
    bound: set[str] = set(dir(builtins))

    def scan(body: list[ast.stmt], scope: set[str]) -> None:
        for stmt in body:
            for annotation in _annotation_nodes(stmt):
                missing = sorted(_referenced_names(annotation) - scope)
                if missing:
                    found.append((
                        stmt.lineno,
                        f'annotation uses {", ".join(missing)} which is not '
                        f'bound above line {stmt.lineno} (NameError on 3.12)',
                    ))
            if _is_type_checking_guard(stmt):
                # ``if TYPE_CHECKING:`` is a promise to a type checker, not a
                # runtime binding: nothing inside it exists when the module is
                # imported, which is exactly why a name imported there and then
                # used in an annotation is a 3.12 NameError. The else branch,
                # though, is ordinary code.
                for case_ in stmt.orelse:
                    scan([case_], scope)
            elif isinstance(stmt, ast.ClassDef):
                # A class body runs at import time, but its names stay inside
                # it: pass a copy so they cannot make a later module-level
                # annotation look valid.
                scan(stmt.body, set(scope))
            elif not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # A function body runs when the function is CALLED, so an
                # import inside it binds nothing at import time. Only the
                # annotations of the def itself were evaluated above.
                for field in ('body', 'orelse', 'finalbody'):
                    inner = getattr(stmt, field, None)
                    if isinstance(inner, list):
                        scan(inner, scope)
                for case in getattr(stmt, 'cases', []) or []:
                    scan(case.body, scope)
                for handler in getattr(stmt, 'handlers', []) or []:
                    scan(handler.body, scope)
            # update() mutates; ``|=`` would rebind and make ``scope`` local to
            # scan(), which then cannot read the set it is supposed to extend.
            scope.update(_names_bound_by(stmt))

    scan(tree.body, bound)
    return found


def _check(path: Path) -> None:
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source, filename=str(path))
    if any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        and any(alias.name == '*' for alias in node.names)
        for node in ast.walk(tree)
    ):
        pytest.skip(f'{path.name}: star import, names cannot be resolved statically')
    problems = _violations(tree)
    assert not problems, (
        f'{path.relative_to(ROOT).as_posix()} would fail to import on Python 3.12 '
        '(the container), where annotations are evaluated eagerly:\n  '
        + '\n  '.join(f'line {line}: {message}' for line, message in problems)
    )


@pytest.mark.parametrize('path', _files(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_annotations_are_bound_before_they_are_evaluated(path: Path):
    _check(path)


def test_the_scan_catches_the_exact_line_that_broke_the_render_build():
    """The guard is checked against the real file, not a synthetic sample.

    ``di.py`` reached production with ``dict[str, AskQuestionHandler]`` while
    the handler is imported inside ``get_ask_handler``. Undo the fix in memory
    and the scan must report it — otherwise a passing scan of this project
    would mean nothing, because a scanner that has never been shown the real
    bug is just an assumption.
    """
    source = (ROOT / 'application' / 'di.py').read_text(encoding='utf-8')
    assert not _violations(ast.parse(source)), 'the current di.py is not clean'

    pre_fix = source.replace(
        "_ask_handler_cache: dict[str, 'AskQuestionHandler'] = {}",
        '_ask_handler_cache: dict[str, AskQuestionHandler] = {}',
    )
    assert pre_fix != source, 'the fix is gone; update this test to match'
    problems = _violations(ast.parse(pre_fix))
    assert problems, 'the scan no longer detects the bug it was written for'
    assert 'AskQuestionHandler' in problems[0][1]


def test_the_scan_actually_finds_this_class_of_bug():
    """Guard the guard.

    A checker that cannot fail is worse than none: it would report a clean
    project no matter what. These two samples are the exact shapes seen in the
    wild — a lazily imported name, and a name used above its definition.
    """
    lazy_import = ast.parse(
        'def get():\n'
        '    from app.handlers import Handler\n'
        '    return Handler\n'
        'cache: dict[str, Handler] = {}\n'
    )
    assert _violations(lazy_import), 'a lazily imported name must be reported'

    forward_reference = ast.parse(
        'def build() -> Service:\n'
        '    ...\n'
        'class Service:\n'
        '    ...\n'
    )
    assert _violations(forward_reference), 'a name used above its def must be reported'

    assert not _violations(ast.parse('import os\npath: str = os.sep\n'))
    assert not _violations(
        ast.parse('from __future__ import annotations\ndef f() -> Later: ...\n')
    )
