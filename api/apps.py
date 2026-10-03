import atexit
import logging
import os
import sys
import threading
import time

from django.apps import AppConfig

logger = logging.getLogger(__name__)

_CONVERSATION_JANITOR_INTERVAL_SECONDS = 60
_CLEANUP_ENV_OVERRIDE = 'RAG_CLEANUP_ON_EXIT'

_TRUTHY = {'1', 'true', 'yes', 'on'}


def _is_running_under_pytest() -> bool:
    """True when this process is a pytest run.

    Destructive startup/exit work (embedder preload, atexit data cleanup) must
    never touch developer data during the test suite; the production server
    keeps its full behavior unchanged.
    """
    return 'PYTEST_CURRENT_TEST' in os.environ or 'pytest' in sys.modules


def _is_server_process() -> bool:
    """True when this process actually runs a server.

    Management commands (migrate, shell, check, ...) return False. This is a
    statement about the *process*, not about intent — whether destructive
    cleanup is wanted at all is a separate question, answered by
    ``_is_cleanup_armed``.
    """
    argv0 = os.path.basename(sys.argv[0] or '').lower()
    if argv0 in {'gunicorn', 'uvicorn', 'daphne', 'hypercorn'}:
        return True
    argv1 = sys.argv[1].lower() if len(sys.argv) > 1 else ''
    return argv1 == 'runserver'


def _is_cleanup_armed() -> bool:
    """True only when ``RAG_CLEANUP_ON_EXIT`` explicitly asks for deletion.

    THE DEFAULT IS "KEEP MY DATA", AND THAT IS THE WHOLE POINT
    ===========================================================
    This used to be inferred from argv: any ``runserver`` deleted the uploads,
    every document row, the whole vector collection, every conversation and
    every session on shutdown. So one ``Ctrl+C`` during development destroyed
    the data — which is exactly what happened to the demo PDF — and on Render
    every spin-down or deploy would have wiped a visitor's upload.

    A variable that is not set must mean "do nothing destructive". Getting this
    backwards is a data-loss bug, so the safe reading is the default and arming
    requires naming the intent (``RAG_CLEANUP_ON_EXIT=1``).
    """
    return os.environ.get(_CLEANUP_ENV_OVERRIDE, '').strip().lower() in _TRUTHY


def _should_cleanup_on_exit() -> bool:
    """Cleanup runs only when a real server exits AND the deletion was asked for.

    Both conditions are required. Arming alone is not enough — otherwise
    ``manage.py shell`` in an armed environment would destroy the database as
    a side effect of running a command.
    """
    return (
        not _is_running_under_pytest()
        and _is_server_process()
        and _is_cleanup_armed()
    )


def _run_conversation_janitor() -> None:
    """Daemon thread: evict idle conversations (TTL) on a schedule."""
    from application.di import get_conversation_repo
    while True:
        time.sleep(_CONVERSATION_JANITOR_INTERVAL_SECONDS)
        try:
            removed = get_conversation_repo().prune()
            if removed:
                logger.info('Conversation janitor pruned %d idle conversation(s)', removed)
        except Exception:
            logger.exception('Conversation janitor error')


def _cleanup_session():
    """atexit hook: wipe run-time data when a real server exits.

    Data only (files, documents, vector store, conversations, sessions).
    User accounts are never touched. Requires BOTH a server process and an
    explicit ``RAG_CLEANUP_ON_EXIT=1`` — the default is to keep the data.

    Prefer ``manage.py reset_demo`` when the intent is to clear the demo: it is
    visible in the command history, can be scoped, and asks before acting.
    """
    if not _should_cleanup_on_exit():
        return
    from application.services.cleanup import run_cleanup
    run_cleanup()


def _reconcile_interrupted_documents() -> None:
    """Repair rows abandoned by a previous process, on every server boot.

    An ingest that was cut short by a restart leaves its row in ``processing``,
    which is not a retryable state — the document would be stuck for ever with
    no button that can rescue it. The exit hook cannot help (the process that
    would notice is the one that died), so recovery happens at startup, where
    a process can see rows left behind by every process before it.
    """
    try:
        from application.services.reconcile import reconcile_interrupted_documents

        recovered = reconcile_interrupted_documents()
    except Exception:
        # Never let housekeeping stop the server from starting.
        logger.exception('Could not reconcile interrupted documents at startup')
        return
    if recovered:
        logger.warning(
            'Marked %d interrupted document(s) as failed so they can be retried: %s',
            len(recovered),
            recovered,
        )


class ApiConfig(AppConfig):
    name = 'api'

    def ready(self):
        from application.di import preload_embedder
        if not _is_running_under_pytest():
            threading.Thread(target=preload_embedder, daemon=True).start()
        threading.Thread(target=_run_conversation_janitor, daemon=True).start()
        atexit.register(_cleanup_session)
        _warn_if_cleanup_armed()
        if not _is_running_under_pytest() and _is_server_process():
            _reconcile_interrupted_documents()


def _warn_if_cleanup_armed() -> None:
    """Say so at STARTUP, not only in the code, when deletion is armed.

    Announcing this when the process starts is the only moment the person who
    can still stop it is watching: by the time the exit hook fires, the data is
    already gone. The warning is unconditional — it used to be suppressed under
    ``DEBUG``, which silenced it in exactly the local environment where a stray
    ``Ctrl+C`` is most likely.
    """
    if not _is_cleanup_armed():
        return
    logger.warning(
        'DESTRUCTIVE: RAG_CLEANUP_ON_EXIT is enabled — uploaded files, '
        'documents, the vector store, conversations and sessions will be '
        'DELETED when this process exits. Unset it to keep your data, and use '
        '"manage.py reset_demo" to clear the demo deliberately.'
    )
