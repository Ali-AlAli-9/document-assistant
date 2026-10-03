"""Consistency checker: does the database still describe reality?

THE QUESTION
============
Nothing in the system guarantees that the three stores agree:

* ``documents_document`` — rows with a file reference and a ``chunk_count``,
* ``media/uploads/`` — the uploaded files themselves,
* Chroma — the embedded chunks retrieval actually searches.

A crash between two writes leaves them disagreeing, and the symptoms are all
silent: retrieval returns nothing for a document whose row says "جاهز", the file
list shows a document that cannot be opened, or the collection grows forever
holding chunks for documents that no longer exist. Nothing raises. The UI simply
answers badly and nobody can tell whether it is a retrieval bug or missing data.

``chunk_count`` cannot detect any of this — it is a number the database writes
about itself, compared against nothing. Only a read of the actual index can.

Each check is independent and reported separately, because the useful action
differs per failure: a missing file needs a re-upload, a surplus file needs a
deletion, and a chunk mismatch needs a re-ingest.
"""

from dataclasses import dataclass, field

from django.core.files.storage import default_storage

UPLOAD_PREFIX = 'uploads/'


@dataclass
class ConsistencyReport:
    """What was found. Empty lists mean that check passed."""

    #: Document ids whose file is referenced but absent from storage.
    missing_files: list[int] = field(default_factory=list)
    #: Titles whose chunk_count disagrees with the number of stored chunks.
    chunk_mismatches: list[tuple[int, str, int, int]] = field(default_factory=list)
    #: File names in uploads/ that no document row claims.
    orphan_files: list[str] = field(default_factory=list)
    #: Checks that could not run (e.g. the store was unreachable).
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing_files or self.chunk_mismatches or self.orphan_files)


def check_storage_consistency(report: ConsistencyReport | None = None) -> ConsistencyReport:
    """Every document row must point at a file that exists."""
    from documents.models import Document

    report = report or ConsistencyReport()
    for doc_id, name in Document.objects.exclude(file='').values_list('id', 'file'):
        try:
            exists = bool(name) and default_storage.exists(name)
        except Exception as exc:  # storage backend unreachable
            report.skipped.append(f'storage: {exc}')
            return report
        if not exists:
            report.missing_files.append(doc_id)
    return report


def check_vector_store_consistency(
    store,
    report: ConsistencyReport | None = None,
) -> ConsistencyReport:
    """``chunk_count`` must match the chunks actually stored.

    Only ``ready`` documents are checked: a pending or failed one legitimately
    has none, and counting them would report noise instead of a fault.
    """
    from documents.models import Document

    report = report or ConsistencyReport()
    for doc_id, title, declared in Document.objects.filter(status='ready').values_list(
        'id', 'title', 'chunk_count'
    ):
        try:
            stored = store.count_by_document(doc_id)
        except Exception as exc:
            report.skipped.append(f'vector store: {exc}')
            return report
        if stored != declared:
            report.chunk_mismatches.append((doc_id, title, declared, stored))
    return report


def check_orphan_files(report: ConsistencyReport | None = None) -> ConsistencyReport:
    """Every stored upload must be claimed by a document row.

    ``chunk_count`` alone cannot catch this: a delete that removed the row but
    left the bytes behind is invisible to the database, and the file simply
    occupies disk until somebody notices.
    """
    from documents.models import Document

    report = report or ConsistencyReport()
    try:
        directories, stored_files = default_storage.listdir(UPLOAD_PREFIX)
    except Exception as exc:
        report.skipped.append(f'uploads listing: {exc}')
        return report

    claimed = set(Document.objects.exclude(file='').values_list('file', flat=True))
    for name in stored_files:
        relative = f'{UPLOAD_PREFIX}{name}'
        if relative not in claimed:
            report.orphan_files.append(relative)
    return report


def verify_all(store=None) -> ConsistencyReport:
    """Run every check and return the combined report."""
    if store is None:
        from application.di import get_vector_store

        store = get_vector_store()

    report = ConsistencyReport()
    check_storage_consistency(report)
    check_vector_store_consistency(store, report)
    check_orphan_files(report)
    return report
