"""The deployment files, checked by tests rather than by Render.

Why this file exists (two failed Render builds, ~40 minutes of build time):

1. ``Dockerfile`` baked the model with ``ARG EMBEDDING_MODEL``, and
   ``render.yaml`` set ``EMBEDDING_MODEL=all-MiniLM-L6-v2``. Render passes a
   service's env vars as build args, and a build arg overrides an ``ARG``
   default, so the correct repo id was replaced by a bare model name and the
   build died at step 8/11 with ``401 RepositoryNotFoundError``.

2. ``collectstatic`` needs Django's settings, ``SECRET_KEY`` is a required
   setting, and Render's env vars do NOT reach a ``RUN`` unless the Dockerfile
   declares a matching ``ARG``. The build died at step 11/11 with
   ``ValidationError: SECRET_KEY Field required``.

Both failures are silent until a 20-minute build: nothing local loads the
Dockerfile, and ``manage.py check`` passes happily with a ``.env`` on disk.
So the invariants are pinned here, where they cost a second.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
RENDER_YAML = (ROOT / 'render.yaml').read_text(encoding='utf-8')


def _env_keys() -> set[str]:
    return set(re.findall(r'^\s+- key:\s*(\S+)', RENDER_YAML, re.MULTILINE))


def _arg_names() -> set[str]:
    # Capture the NAME only: ``ARG EMBEDDING_REPO=intfloat/multilingual-e5-small``
    # declares the variable EMBEDDING_REPO with that default.
    return set(re.findall(r'^\s*ARG\s+([A-Z_0-9]+)', DOCKERFILE, re.MULTILINE))


def _env_names() -> set[str]:
    return set(re.findall(r'^\s*ENV\s+([A-Z_0-9]+)', DOCKERFILE, re.MULTILINE))


def _lines_containing(needle: str) -> list[str]:
    """Instruction lines only.

    Without the comment filter this matches the prose in this Dockerfile that
    NAMES the step it explains — so a test asserting "one collectstatic" would
    count its own documentation.
    """
    return [
        ln.strip()
        for ln in DOCKERFILE.splitlines()
        if needle in ln and not ln.strip().startswith('#')
    ]


class TestEmbeddingModel:
    def test_no_service_env_var_can_override_the_build_repo_id(self):
        """The original defect, stated as an invariant: a build-only argument
        must not share a name with anything a service can set."""
        assert 'EMBEDDING_REPO' in _arg_names()
        assert 'EMBEDDING_REPO' not in _env_keys(), (
            'a service env var named EMBEDDING_REPO would override the baked '
            'repo id — rename one of them'
        )
        assert 'EMBEDDING_MODEL' not in _arg_names(), (
            'EMBEDDING_MODEL is a runtime setting; reusing it as a build arg is '
            'how the first build failed'
        )

    def test_the_snapshot_uses_the_build_only_variable(self):
        snapshot = _lines_containing('snapshot_download')
        assert len(snapshot) == 1, 'expected exactly one snapshot_download step'
        assert 'EMBEDDING_REPO' in snapshot[0]
        assert 'split(' in snapshot[0], (
            'the destination folder must be derived from the repo id so it '
            'cannot drift from what the adapter looks for at runtime'
        )

    def test_the_snapshot_is_pinned_to_an_exact_revision(self):
        """``snapshot_download`` without a revision takes whatever ``main``
        points at on the day the build runs. An upstream push would then change
        what the assistant answers — no line of our code changes, no test fails,
        and the change only shows up in a demo, in front of a company.

        The pin is asserted in three places because any one of them alone is
        easy to break: the value's shape, its use in the download, and the fact
        that nothing outside the Dockerfile can override it.
        """
        assert 'EMBEDDING_REVISION' in _arg_names(), (
            'the embedding model revision is not pinned as a build arg'
        )
        revision = re.search(r'ARG EMBEDDING_REVISION=([^\s]+)', DOCKERFILE)
        assert revision, 'ARG EMBEDDING_REVISION has no default'
        assert re.fullmatch(r'[0-9a-f]{40}', revision.group(1)), (
            f'EMBEDDING_REVISION={revision.group(1)!r} is not a full 40-character '
            'commit sha — a branch name or short sha would drift again'
        )
        snapshot = _lines_containing('snapshot_download')
        assert 'EMBEDDING_REVISION' in snapshot[0], (
            'the download step does not pass the pinned revision to '
            'snapshot_download, so pinning it changes nothing'
        )
        assert 'EMBEDDING_REVISION' not in _env_keys(), (
            'a service env var named EMBEDDING_REVISION would silently unpin '
            'the model — rename one of them'
        )

    def test_only_the_files_the_runtime_loads_are_downloaded(self):
        """The allow list must name exactly the files this checkpoint publishes.

        The repository also carries weights for runtimes this app never loads, and
        a bare ``snapshot_download`` takes all of them. Measured for the current
        model: the allow list bakes 482MB, nearly all of it model.safetensors.

        The set is tokenizer-specific, which is what makes it worth pinning.
        multilingual-e5-small is a XLM-R checkpoint using SentencePiece, so it
        ships ``sentencepiece.bpe.model`` and no ``vocab.txt``; the previous
        English-only model was WordPiece and had it the other way round. Carrying
        an entry the checkpoint does not publish is not merely wasted space — the
        Dockerfile's own guard fails the BUILD on any listed file that was not
        written, so one stale entry takes deploys down.
        """
        snapshot = _lines_containing('snapshot_download')[0]
        assert 'allow_patterns=' in snapshot, (
            'the download still takes the whole repository'
        )
        listed = re.findall(r"'([^']+)'", re.search(r'wanted=\[(.*?)\]', snapshot).group(1))
        assert listed, 'could not read the allow list out of the Dockerfile'

        # Every part of this model directory that the loader actually reads. A
        # typo in one path is a runtime failure, not a typo.
        required = {
            'config.json',                            # architecture
            'model.safetensors',                      # the weights
            'tokenizer_config.json', 'tokenizer.json',
            'sentencepiece.bpe.model',                # tokenization (SentencePiece)
            'special_tokens_map.json',
            'modules.json', 'sentence_bert_config.json',   # module order
            '1_Pooling/config.json',                  # pooling mode
        }
        assert required <= set(listed), (
            f'allow list is missing {sorted(required - set(listed))}'
        )
        # The other tokenizer's files. Carrying either of these is a guaranteed
        # failed build against this checkpoint, not a stale-but-working image.
        assert not {'vocab.txt', 'config_sentence_transformers.json'} & set(listed), (
            'the allow list still names files this XLM-R checkpoint does not '
            'publish; the build guard will fail on them'
        )
        assert [name for name in listed if name.endswith('.safetensors')] == [
            'model.safetensors'
        ], 'exactly one weight format should be baked, not one per runtime'
        for never_loaded in ('onnx', 'openvino', 'tf_model', 'rust_model',
                             'pytorch_model.bin', 'data_config.json'):
            assert never_loaded not in snapshot, (
                f'{never_loaded!r} would add tens of megabytes the app never reads'
            )

    def test_the_guard_checks_every_downloaded_file_not_just_the_config(self):
        """allow_patterns is what turns a typo into a failed BUILD.

        The previous guard only asked for config.json, which a truncated
        download satisfies: the build goes green, the image ships, and the
        model fails to load at the first question — during a demo. The guard now
        verifies the same list that was requested.
        """
        snapshot = _lines_containing('snapshot_download')[0]
        assert 'missing=' in snapshot and 'os.path.isfile' in snapshot, (
            'the build does not verify that every requested file was written'
        )
        assert 'looks incomplete' not in snapshot, (
            'the single-file config.json guard is back — a partial download '
            'would pass the build and fail in front of a company'
        )
        assert snapshot.count('wanted') >= 4, (
            'the guard should be driven by the same list the download used, so '
            'a new entry cannot be requested and not verified'
        )

    def test_the_configured_value_is_a_valid_huggingface_repo_id(self):
        """A bare model name is NOT a repository: huggingface.co answers
        401 RepositoryNotFoundError for it. This is the exact string that
        killed build 1, so it is worth a permanent assertion."""
        match = re.search(r'- key: EMBEDDING_MODEL\s*\n\s*value:\s*(\S+)', RENDER_YAML)
        assert match, 'EMBEDDING_MODEL missing from render.yaml'
        value = match.group(1).strip('\'"')
        assert '/' in value, (
            f'EMBEDDING_MODEL={value!r} has no org prefix, so snapshot_download '
            'will get 401 RepositoryNotFoundError'
        )
        assert value.startswith('intfloat/'), (
            f'unexpected embedding model repo: {value!r}'
        )

    def test_the_runtime_value_matches_the_baked_one(self):
        """Two files name the model, and nothing forces them to agree.

        ``render.yaml`` sets ``EMBEDDING_MODEL`` (what the process reads) and the
        Dockerfile bakes ``ARG EMBEDDING_REPO`` (what the image contains). They are
        separate files with no shared source, so a change to one alone leaves an
        image whose baked snapshot nothing looks for: ``resolve_local_model_path``
        misses ``models/``, falls through to the offline cache, and the embedder
        is dead on a host with ``HF_HUB_OFFLINE=1``. Same dimension or not, the
        collection then holds vectors from a model the app no longer loads.
        """
        repo = re.search(r'ARG EMBEDDING_REPO=(\S+)', DOCKERFILE)
        assert repo, 'ARG EMBEDDING_REPO has no default'
        configured = re.search(
            r'- key: EMBEDDING_MODEL\s*\n\s*value:\s*(\S+)', RENDER_YAML
        )
        assert configured, 'EMBEDDING_MODEL missing from render.yaml'
        runtime = configured.group(1).strip('\'"')
        assert repo.group(1) == runtime, (
            f'Dockerfile bakes {repo.group(1)!r} but render.yaml asks the running '
            f'process for {runtime!r}'
        )

    def test_building_the_image_offline_is_declared_not_implicit(self):
        """HF_HUB_OFFLINE=1 must come AFTER the download, or the download
        itself is attempted in offline mode and fails.

        Matched on the directives, never on the prose. This Dockerfile explains
        both steps in comments that name them, and an earlier version of this
        test searched the raw text: it compared two COMMENT positions and passed
        or failed according to how the surrounding documentation happened to be
        worded — it had never once looked at the ENV or the RUN.
        """
        def _directive_index(needle: str) -> int | None:
            for i, ln in enumerate(DOCKERFILE.splitlines()):
                stripped = ln.strip()
                if stripped.startswith('#'):
                    continue
                if stripped.startswith(('ENV', 'RUN')) and needle in stripped:
                    return i
            return None

        offline = _directive_index('HF_HUB_OFFLINE')
        download = _directive_index('snapshot_download')
        assert offline is not None and download is not None
        assert offline > download, (
            'HF_HUB_OFFLINE is set before the model is downloaded, so the '
            'download itself runs offline and the build fails'
        )

    def test_the_offline_switch_covers_the_tokenizer_too(self):
        """sentence-transformers is not the only thing that reaches the hub.

        The tokenizer comes from the same bake, and a download attempt there
        fails just as loudly. With only ``HF_HUB_OFFLINE`` set, transformers can
        still try to phone home for a tokenizer it believes is missing.

        The two are declared in ONE ``ENV`` spread over a backslash continuation,
        so this unfolds the instruction before looking — matching a single line
        would read the first half and call the pair missing.
        """
        instruction = next(
            (
                ln
                for ln in re.sub(
                    r'\\\s*\n', ' ', DOCKERFILE
                ).splitlines()
                if ln.strip().startswith('ENV') and 'HF_HUB_OFFLINE' in ln
            ),
            None,
        )
        assert instruction, 'no instruction sets HF_HUB_OFFLINE'
        assert 'TRANSFORMERS_OFFLINE' in instruction, (
            'transformers has no offline switch; a tokenizer lookup can still '
            'hang a request until it times out'
        )


class TestSecretKeyAtBuildTime:
    def test_collectstatic_runs_with_a_placeholder_key(self):
        """Render's env vars do not reach RUN, so this step must supply its
        own SECRET_KEY or the build stops at 11/11 with a ValidationError."""
        steps = _lines_containing('collectstatic')
        assert len(steps) == 1, 'expected exactly one collectstatic step'
        assert 'SECRET_KEY=' in steps[0], (
            'collectstatic has no SECRET_KEY in its command environment'
        )

    def test_no_secret_key_is_persisted_in_the_image(self):
        """A placeholder scoped to one command is invisible in the image's
        environment; declaring ENV or ARG SECRET_KEY would bake it in — and a
        real one, if anyone ever wired it up, would ship inside the image."""
        assert 'SECRET_KEY' not in _env_names()
        assert 'SECRET_KEY' not in _arg_names()

    def test_the_placeholder_is_obviously_not_a_secret(self):
        steps = _lines_containing('collectstatic')
        value = re.search(r'SECRET_KEY=(\S+)', steps[0]).group(1)
        assert 'build' in value.lower()
        assert len(value) >= 16, 'a too-short placeholder could be mistaken for a real key'


class TestGunicornTimeoutOutlastsASynchronousIngest:
    """Ingestion runs inside the upload request, so gunicorn's timeout bounds it.

    With multilingual-e5-small the work is measured in minutes. The old
    ``--timeout 180`` therefore killed the worker part-way through a legitimate
    upload: gunicorn sends SIGKILL on expiry, the row stays ``processing``, and
    ``Document.is_processable`` accepts only ``pending``/``failed`` — so nothing
    in the UI can retry it and the upload is simply gone.

    The number is asserted against the staleness threshold that recovers such
    rows (10 minutes in ``application.services.reconcile``). Below it, a slow but
    successful ingest and a crashed one are indistinguishable until boot.
    """

    TIMEOUT_PATTERN = re.compile(r'--timeout\s+(\d+)')

    def _timeouts(self) -> list[int]:
        found = []
        for ln in DOCKERFILE.splitlines() + (ROOT / 'docker-compose.yml').read_text(
            encoding='utf-8'
        ).splitlines():
            if ln.strip().startswith('#'):
                continue
            found += [int(n) for n in self.TIMEOUT_PATTERN.findall(ln)]
        return found

    def test_both_deploy_paths_declare_a_timeout(self):
        assert self._timeouts(), 'no --timeout found in Dockerfile or compose'

    def test_the_timeout_outlasts_the_stale_processing_window(self):
        from application.services.reconcile import STALE_PROCESSING_MINUTES

        for timeout in self._timeouts():
            assert timeout >= STALE_PROCESSING_MINUTES * 60, (
                f'--timeout {timeout} is below the {STALE_PROCESSING_MINUTES}-minute '
                'window after which a row counts as interrupted: gunicorn will kill '
                'healthy slow ingests and strand them where no button can retry them'
            )

    def test_the_two_deploy_paths_agree(self):
        """A timeout raised in the Dockerfile but not in compose leaves local
        docker runs failing exactly where production stopped failing — the worst
        place to discover the difference."""
        per_file = {}
        for name, text in (
            ('Dockerfile', DOCKERFILE),
            ('docker-compose.yml', (ROOT / 'docker-compose.yml').read_text(encoding='utf-8')),
        ):
            per_file[name] = {
                int(n)
                for ln in text.splitlines()
                if not ln.strip().startswith('#')
                for n in self.TIMEOUT_PATTERN.findall(ln)
            }
        assert len(set(map(frozenset, per_file.values()))) == 1, (
            f'the two deploy paths disagree about --timeout: {per_file}'
        )


class TestBuildArgsAndEnvVarsDoNotCollide:
    @pytest.mark.parametrize('name', sorted(_arg_names() & _env_names()))
    def test_arg_redeclared_as_env_is_deliberate(self, name):
        """A name may be both ARG and ENV (that is how ARG defaults reach RUN),
        but only when nothing outside the Dockerfile can change it."""
        if name == 'EMBEDDING_MODEL_DIR':
            # Shared on purpose: the image bakes the model to /app/models and
            # the runtime must look there too, and both say the same thing.
            configured = re.search(
                r'- key: EMBEDDING_MODEL_DIR\s*\n\s*value:\s*(\S+)', RENDER_YAML
            )
            assert configured, 'EMBEDDING_MODEL_DIR should be pinned in render.yaml'
            assert configured.group(1).strip('\'"') == '/app/models'
            return
        pytest.fail(f'{name} is both ARG and ENV and is not a known-safe pair')


class TestBaseImage:
    def test_the_python_version_is_pinned_not_floating(self):
        """``FROM python:latest`` is how the deployment silently drifts away
        from the interpreter the code was tested on.

        Today that gap is 3.12 (container) versus 3.14 (local venv), and it is
        not hypothetical: PEP 649 made 3.14 evaluate annotations lazily, which
        hid a module-level ``NameError`` that crashed every build. The pin is
        what makes the gap visible — the test suite is what closes it, in
        ``test_annotation_evaluation.py``.
        """
        froms = [
            ln.strip() for ln in DOCKERFILE.splitlines()
            if ln.strip().startswith('FROM ') and 'AS ' not in ln
        ]
        assert len(froms) == 1, f'expected one base image, found {froms}'
        match = re.match(r'FROM python:(\d+\.\d+)', froms[0])
        assert match, (
            f'{froms[0]!r} does not pin a python:X.Y version — an unpinned tag '
            'can change the interpreter under the same commit'
        )
        assert match.group(1) == '3.12', (
            f'the container is on {match.group(1)}; if this moved, re-check the '
            'eager-annotation contract in tests/core/test_annotation_evaluation.py'
        )


class TestRuntimeCommand:
    def test_migrations_run_before_the_server_and_worker_count_is_one(self):
        """One gthread worker: four sync workers would load four copies of the
        embedding model into a 512MB free instance."""
        cmd = _lines_containing('gunicorn')
        assert len(cmd) == 1
        assert 'manage.py migrate' in cmd[0]
        assert '--workers 1' in cmd[0]
        assert 'gthread' in cmd[0]

    def test_cleanup_on_exit_is_disabled_so_sleeping_does_not_wipe_data(self):
        cmd = _lines_containing('gunicorn')[0]
        assert 'RAG_CLEANUP_ON_EXIT' not in cmd
        assert '- key: RAG_CLEANUP_ON_EXIT' in RENDER_YAML
        assert re.search(
            r'- key: RAG_CLEANUP_ON_EXIT\s*\n\s*value:\s*"?0"?', RENDER_YAML
        ), 'RAG_CLEANUP_ON_EXIT must be 0 in production'
