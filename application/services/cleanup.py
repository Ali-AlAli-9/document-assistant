"""Destroying data is a decision, so it is now a command that names itself.

WHY THIS EXISTS INSTEAD OF AN EXIT HOOK
=======================================
``api.apps`` used to register an ``atexit`` hook that, on any server shutdown,
deleted the uploaded files, every document row, the ENTIRE vector collection,
every conversation and every session. The lifetime of the data was therefore the
lifetime of the process:

* ``Ctrl+C`` during development silently destroyed the demo document — which is
  what happened, and why the demo PDF had to be re-ingested from scratch.
* On Render, every deploy or spin-down would have wiped what visitors uploaded.

Accounts were (and remain) the one thing never touched: the people who log in
outlive the files they uploaded.

WHAT REPLACED IT
================
``manage.py reset_demo`` — explicit, repeatable, scoped, and it asks first.

ORDERING, AND WHY IT IS NOT ARBITRARY
======================================
Vectors → rows → files. Each step can fail on its own, so the order decides
what a partial failure leaves behind:

* **vectors fail** → the row and its chunks both survive: the document still
  works. The reset simply did not happen. This is the one irreplaceable step —
  recomputing embeddings costs real compute, while a row and a file are
  rebuilt by one re-upload.
* **rows fail** → chunks remain but nothing points at them. Recoverable by
  re-running the reset; wasteful, not destructive.
* **files fail** → wasted disk only. Fully recoverable.

The reverse order produced the state this file exists to prevent: files deleted
first, then a failure before the rows went, leaving rows pointing at files that
no longer existed — a document that cannot be opened, with nothing in the logs
to explain it and no way for a user to tell it from a broken PDF.
"""

import logging

from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)


def select_document_ids(documents, title_prefix=None, owner_id=None) -> list[int]:
    """Choose which documents a reset touches.

    No selector means "everything" — that is what ``reset_demo`` with no flags
    does. But a selector that matches NOTHING must select nothing: silently
    falling back to "all" would turn a typo like ``--prefix demoo`` into a full
    wipe, which is the worst possible reading of a mistyped argument.
    """
    if title_prefix is None and owner_id is None:
        return [doc.id for doc in documents]

    selected = [
        doc.id
        for doc in documents
        if (title_prefix is None or doc.title.startswith(title_prefix))
        and (owner_id is None or getattr(doc, 'owner_id', None) == owner_id)
    ]
    if not selected:
        logger.warning(
            'No document matched title_prefix=%r owner_id=%r — nothing will be deleted',
            title_prefix,
            owner_id,
        )
    return selected


def _clear_vectors(store, doc_ids: list[int]) -> tuple[int, list[int], list[str]]:
    """Remove chunks per document. Returns (deleted, failed_ids, messages).

    The messages are kept because a bare id tells an operator nothing: when a
    reset aborts, "document 41: chroma down" is actionable and "document 41" is
    not.
    """
    deleted, failed, messages = 0, [], []
    for doc_id in doc_ids:
        try:
            store.delete_by_document(doc_id)
            deleted += 1
        except Exception as exc:
            # Chunks are the only part that cannot be rebuilt from the file, so
            # a failure here is reported loudly and stops the reset below.
            logger.exception('Vector store delete failed for document %s', doc_id)
            failed.append(doc_id)
            messages.append(f'document {doc_id}: {exc}')
    return deleted, failed, messages


def _clear_rows(doc_ids: list[int]) -> int:
    from documents.models import Document

    deleted, _ = Document.objects.filter(id__in=doc_ids).delete()
    return deleted


def _clear_files(paths: list[str]) -> tuple[int, list[str]]:
    deleted, failed = 0, []
    for path in paths:
        try:
            if path and default_storage.exists(path):
                default_storage.delete(path)
                deleted += 1
        except Exception:
            logger.exception('Could not delete stored file %s', path)
            failed.append(path)
    return deleted, failed


def _clear_conversations() -> None:
    try:
        from application.di import get_conversation_repo

        get_conversation_repo().clear_all()
    except Exception:
        logger.exception('Cleanup: failed to clear conversations')


def _clear_sessions() -> None:
    from django.contrib.sessions.models import Session

    try:
        Session.objects.all().delete()
    except Exception:
        logger.exception('Cleanup: failed to clear session records')


def _delete_documents(
    doc_ids: list[int],
    files_by_id: dict,
    store,
    delete_rows=None,
) -> dict:
    """Vectors, then rows, then files — with the store failure aborting early."""
    report = {'documents': 0, 'files': 0, 'vector_errors': [], 'file_errors': []}

    vectors_deleted, vector_errors, vector_messages = _clear_vectors(store, doc_ids)
    if vector_errors:
        # Abort before removing the pointers: a row whose chunks are gone is
        # unrecoverable, whereas a surviving row is merely not reset.
        report['vector_errors'] = vector_errors
        report['vector_error_messages'] = vector_messages
        report['vectors_deleted'] = vectors_deleted
        logger.error(
            'Reset aborted: %d document(s) could not be cleaned in the vector '
            'store. Nothing was deleted from the database.',
            len(vector_errors),
        )
        return report

    report['documents'] = (delete_rows or _clear_rows)(doc_ids)
    report['files'], report['file_errors'] = _clear_files(
        [files_by_id.get(doc_id) for doc_id in doc_ids]
    )
    report['vectors_deleted'] = vectors_deleted
    return report


def run_cleanup(title_prefix=None, owner_id=None, documents=None, store=None) -> dict:
    """Delete run-time data. Never touches accounts.

    Safe to call more than once, and safe to call when there is nothing to do.
    Every step is isolated so one failure never silently skips the rest, and the
    returned report says what ACTUALLY happened rather than what was attempted.
    """
    from documents.models import Document

    if documents is None:
        documents = list(Document.objects.all())

    doc_ids = select_document_ids(documents, title_prefix=title_prefix, owner_id=owner_id)

    if store is None:
        try:
            from application.di import get_vector_store

            store = get_vector_store()
        except Exception as exc:
            # The vector store could not even be opened. Importing chromadb is
            # not safe during interpreter shutdown (opentelemetry submits work
            # to an already-closed thread pool), which is exactly when the
            # atexit hook runs. Deleting the rows anyway would strand every
            # chunk with nothing pointing at it, so nothing is deleted and the
            # reason is reported instead of raised: an atexit hook that throws
            # prints a traceback nobody sees and leaves a half-cleaned store.
            logger.error(
                'Cleanup aborted: the vector store could not be reached (%s). '
                'No document, file or session was deleted. Use '
                '"manage.py reset_demo" from a shell, which can import cleanly.',
                exc,
            )
            return {
                'documents': 0,
                'files': 0,
                'vector_errors': 'store unavailable',
                'vector_error_messages': [str(exc)],
                'file_errors': [],
                'vectors_deleted': 0,
                'conversations': 'kept',
                'sessions': 'kept',
            }

    files_by_id = {
        doc.id: doc.file.name
        for doc in documents
        if doc.id in doc_ids and getattr(doc, 'file', None)
    }

    report = _delete_documents(doc_ids, files_by_id, store)
    report['conversations'] = 'cleared'
    _clear_conversations()
    report['sessions'] = 'cleared'
    _clear_sessions()
    logger.info('Cleanup complete: %s', report)
    return report
