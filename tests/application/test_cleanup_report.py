"""Explicit demo reset — and a report that tells the truth about it.

WHY A COMMAND INSTEAD OF AN EXIT HOOK
======================================
Cleanup used to run automatically when the server stopped. That made the
lifetime of the data equal to the lifetime of the process, so a ``Ctrl+C``
during development silently destroyed every uploaded document, the whole
vector collection and all sessions — which is exactly what happened to the demo
PDF, and exactly what would happen to a visitor's upload on the next deploy.

Destroying data is a decision, so it is now a command that names itself. A
command can be read, repeated deliberately, and — unlike a hook — it can ask
before it acts.

THE ORDERING IS THE POINT
=========================
Vectors first, then rows, then files. Each step can fail independently, so the
order decides what a partial failure leaves behind:

* vectors fail → the row survives with its chunks intact: the document still
  works, it merely was not reset.
* files fail → only disk space is wasted; nothing dangles.

The reverse order (files first) is what produced rows pointing at files that no
longer existed, with no log line and no way for a user to tell that from a
broken document.
"""

import logging
from unittest.mock import MagicMock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from application.services import cleanup
from documents.models import Document

logger = logging.getLogger(__name__)


def _make_document(title, status='ready', chunk_count=3, owner=None):
    path = default_storage.save(f'uploads/{title}', ContentFile(b'hello'))
    return Document.objects.create(
        title=title, file=path, status=status, chunk_count=chunk_count, owner=owner
    )


# --------------------------------------------------------------------------- #
# selection                                                                    #
# --------------------------------------------------------------------------- #


def test_no_selection_means_every_document():
    docs = [Document(id=1), Document(id=2), Document(id=3)]
    assert cleanup.select_document_ids(docs) == [1, 2, 3]


def test_a_title_prefix_narrows_the_blast_radius():
    docs = [
        Document(id=1, title='demo-intro.pdf'),
        Document(id=2, title='demo-appendix.pdf'),
        Document(id=3, title='final.pdf'),
    ]
    # "reset only the demo files" is the question people actually ask; there is
    # no way to ask it when the only option is delete everything.
    assert cleanup.select_document_ids(docs, title_prefix='demo-') == [1, 2]


def test_an_owner_narrows_the_blast_radius():
    docs = [Document(id=1, owner_id=7), Document(id=2, owner_id=9)]
    assert cleanup.select_document_ids(docs, owner_id=7) == [1]


def test_selectors_combine():
    docs = [
        Document(id=1, title='demo-a.pdf', owner_id=7),
        Document(id=2, title='demo-b.pdf', owner_id=9),
        Document(id=3, title='final.pdf', owner_id=7),
    ]
    assert cleanup.select_document_ids(docs, title_prefix='demo-', owner_id=7) == [1]


def test_a_selector_that_matches_nothing_selects_nothing():
    """Deleting everything because a filter matched nothing is the worst
    possible reading of a typo like ``--prefix demoo``."""
    docs = [Document(id=1, title='final.pdf')]
    assert cleanup.select_document_ids(docs, title_prefix='demoo') == []


# --------------------------------------------------------------------------- #
# ordering                                                                     #
# --------------------------------------------------------------------------- #


def test_vectors_go_before_rows_before_files(monkeypatch):
    """The ordering is the contract, so it is asserted directly."""
    calls = []
    monkeypatch.setattr(cleanup, '_clear_vectors', lambda store, ids: (calls.append('vectors'), (0, [], []))[1])
    monkeypatch.setattr(cleanup, '_clear_files', lambda paths: (calls.append('files'), (0, []))[1])

    cleanup._delete_documents([1], {1: 'uploads/a.pdf'}, _Store([]), delete_rows=lambda ids: calls.append('rows') or 1)

    assert calls == ['vectors', 'rows', 'files']


def test_a_failed_vector_step_stops_the_deletion():
    """Vectors are the only irreplaceable step.

    Chunks cost real compute to recompute; a row and a file are rebuilt from a
    re-upload. So if the store cannot be cleaned, the reset must abort rather
    than delete the pointers and leave the vectors stranded and unreferenced.
    """
    calls = []
    store = MagicMock()
    store.delete_by_document.side_effect = RuntimeError('chroma down')

    cleanup._delete_documents([1], {1: 'uploads/a.pdf'}, store, delete_rows=lambda ids: calls.append('rows'))

    assert calls == [], 'rows must not be deleted when the store refused'
    assert store.delete_by_document.called


def test_a_failed_file_step_still_completes_the_row_deletion(monkeypatch):
    """Wasted disk is recoverable; a row pointing at nothing is not."""
    calls = []
    monkeypatch.setattr(cleanup, '_clear_files', lambda paths: (calls.append('files'), (0, ['x']))[1])

    cleanup._delete_documents(
        [1], {1: 'uploads/a.pdf'}, _Store([]), delete_rows=lambda ids: calls.append('rows') or 1
    )

    # Rows were still deleted, and the failure was reported rather than hidden.
    assert calls == ['rows', 'files']


class _Store:
    """Minimal stand-in recording the order in which steps are invoked."""

    def __init__(self, calls):
        self._calls = calls

    def delete_by_document(self, doc_id):
        self._calls.append(f'vectors:{doc_id}')
