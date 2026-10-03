"""The consistency checker must find real breakage, not just run.

WHY
===
Cleanup steps used to be independent ``try/except`` blocks: if the file deletion
succeeded but the row deletion failed, the database pointed at files that no
longer existed — and the only symptom was a document that could not be opened,
with nothing in the logs to explain it. These tests pin that the checker
actually DETECTS each way the stores can disagree, since a checker that reports
"all fine" on broken data is worse than no checker: it manufactures confidence.
"""

import pytest
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

from application.services import consistency
from documents.models import Document


class FakeStore:
    """Records what it was asked and answers with a scripted count."""

    def __init__(self, counts=None, error=None):
        self.counts = counts or {}
        self.error = error
        self.asked = []

    def count_by_document(self, doc_id):
        self.asked.append(doc_id)
        if self.error:
            raise self.error
        return self.counts.get(doc_id, 0)


@pytest.fixture
def storage(settings, tmp_path):
    """Point MEDIA_ROOT at a tmp dir so no real file is touched.

    Overriding the setting is enough: ``default_storage`` caches the storage
    object it built from ``MEDIA_ROOT``, so it is repointed rather than
    replaced. Replacing ``default_storage`` itself breaks Django's lazy proxy
    and every ``save()`` then fails.
    """
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _upload(storage, name, content=b'data'):
    path = default_storage.save(f'uploads/{name}', ContentFile(content))
    return path


# --------------------------------------------------------------------------- #
# storage vs database                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestMissingFiles:
    def test_a_row_pointing_at_nothing_is_reported(self, storage):
        Document.objects.create(title='a.pdf', file='uploads/gone.pdf')

        report = consistency.check_storage_consistency()

        assert report.missing_files != []
        assert not report.ok

    def test_a_row_with_a_real_file_passes(self, storage):
        path = _upload(storage, 'here.pdf')
        Document.objects.create(title='b.pdf', file=path)

        assert consistency.check_storage_consistency().ok

    def test_a_row_with_no_file_at_all_is_not_a_missing_file(self, storage):
        # A pending row legitimately has no file yet; reporting it would bury
        # the real faults in noise.
        Document.objects.create(title='pending.pdf', file='')

        assert consistency.check_storage_consistency().missing_files == []

    def test_the_document_id_is_reported_not_just_a_count(self, storage):
        doc = Document.objects.create(title='c.pdf', file='uploads/gone.pdf')

        report = consistency.check_storage_consistency()

        assert report.missing_files == [doc.id]


# --------------------------------------------------------------------------- #
# database vs vector store                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestChunkCounts:
    def test_a_ready_document_with_no_chunks_is_reported(self, storage):
        Document.objects.create(title='d.pdf', status='ready', chunk_count=12)

        report = consistency.check_vector_store_consistency(FakeStore({}))

        assert len(report.chunk_mismatches) == 1
        assert report.chunk_mismatches[0][2:] == (12, 0)
        assert not report.ok

    def test_extra_stored_chunks_are_also_reported(self, storage):
        # The reverse case: the index holds chunks the row does not know about,
        # which means retrieval can surface content the document does not claim.
        Document.objects.create(title='e.pdf', status='ready', chunk_count=3)

        report = consistency.check_vector_store_consistency(FakeStore({1: 9}))

        assert report.chunk_mismatches[0][2:] == (3, 9)

    def test_a_matching_count_passes(self, storage):
        Document.objects.create(title='f.pdf', status='ready', chunk_count=7)

        assert consistency.check_vector_store_consistency(FakeStore({1: 7})).ok

    def test_unfinished_documents_are_not_counted_as_faults(self, storage):
        # pending/failed documents have no chunks yet, by definition. Asking
        # about them would report every in-flight upload as corruption.
        for status in ('pending', 'processing', 'failed'):
            Document.objects.create(title=f'{status}.pdf', status=status, chunk_count=0)

        report = consistency.check_vector_store_consistency(FakeStore({}))

        assert report.chunk_mismatches == []

    def test_an_unreachable_store_is_reported_as_skipped_not_as_a_fault(self, storage):
        # Distinguishing "broken" from "could not check" matters: a fault tells
        # the user to re-upload, a skip tells them to check the store.
        Document.objects.create(title='g.pdf', status='ready', chunk_count=1)

        report = consistency.check_vector_store_consistency(FakeStore(error=RuntimeError('down')))

        assert report.chunk_mismatches == []
        assert report.skipped


# --------------------------------------------------------------------------- #
# orphan files                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestOrphanFiles:
    def test_an_unclaimed_file_is_reported(self, storage):
        _upload(storage, 'stray.pdf')

        report = consistency.check_orphan_files()

        assert report.orphan_files == ['uploads/stray.pdf']

    def test_a_claimed_file_is_not_reported(self, storage):
        path = _upload(storage, 'kept.pdf')
        Document.objects.create(title='h.pdf', file=path)

        assert consistency.check_orphan_files().ok


# --------------------------------------------------------------------------- #
# combined                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_verify_all_combines_the_checks(storage):
    _upload(storage, 'stray.pdf')
    Document.objects.create(title='ready.pdf', file='uploads/vanished.pdf', status='ready', chunk_count=4)

    report = consistency.verify_all(FakeStore({}))

    assert len(report.missing_files) == 1
    assert len(report.chunk_mismatches) == 1
    assert report.orphan_files == ['uploads/stray.pdf']


@pytest.mark.django_db
def test_verify_all_passes_on_a_healthy_store(storage):
    path = _upload(storage, 'good.pdf')
    Document.objects.create(title='good.pdf', file=path, status='ready', chunk_count=2)

    assert consistency.verify_all(FakeStore({1: 2})).ok


@pytest.mark.django_db
def test_verify_all_accepts_an_injected_store(storage):
    """``verify_all()`` must not reach for a real Chroma client when given one."""
    Document.objects.create(title='i.pdf', file='uploads/x.pdf', status='ready', chunk_count=1)
    store = FakeStore({1: 1})

    consistency.verify_all(store)

    assert store.asked == [1]
