"""Interrupted ingests must not leave a document stuck forever.

THE BUG THIS EXISTS FOR
=======================
``domain.entities.document.Document.is_processable`` accepts only ``pending``
and ``failed``. A ``processing`` row is therefore not retryable — by the API
(``Only failed documents can be retried``) or by the entity.

Ingesting a real PDF with the local e5 model takes minutes, and it runs
SYNCHRONOUSLY inside the upload request. Stop the server during that window
(``Ctrl+C``, a crash, a deploy) and the row stays ``processing`` forever:

* the UI shows "قيد المعالجة" indefinitely,
* ``chunk_count`` stays 0, so retrieval finds nothing for it,
* and there is no button that can rescue it.

``processing`` is a marker left by a worker, not a durable state. Nothing is
working on that row after a restart, so on the next boot it must be resolved.
Marking it ``failed`` puts the document back in the user's hands — the retry
button exists for exactly that.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from application.services.reconcile import (
    STALE_PROCESSING_MINUTES,
    reconcile_interrupted_documents,
)
from documents.models import Document


@pytest.mark.django_db
class TestInterruptedRowsBecomeRetryable:
    def test_a_long_running_row_is_marked_failed(self):
        doc = Document.objects.create(title='stuck.pdf', status='processing')
        self._age(doc, minutes=STALE_PROCESSING_MINUTES + 5)

        recovered = reconcile_interrupted_documents()

        assert recovered == [doc.id]
        doc.refresh_from_db()
        assert doc.status == 'failed'

    def test_a_freshly_started_row_is_left_alone(self):
        # The whole point is not to kill an ingest that is still running in
        # another worker/process.
        doc = Document.objects.create(title='running.pdf', status='processing')
        self._age(doc, minutes=1)

        assert reconcile_interrupted_documents() == []
        doc.refresh_from_db()
        assert doc.status == 'processing'

    def test_failed_and_ready_rows_are_untouched(self):
        ready = Document.objects.create(title='done.pdf', status='ready')
        failed = Document.objects.create(title='bad.pdf', status='failed')
        pending = Document.objects.create(title='queued.pdf', status='pending')
        for doc in (ready, failed, pending):
            self._age(doc, minutes=STALE_PROCESSING_MINUTES + 60)

        assert reconcile_interrupted_documents() == []

    def test_the_threshold_is_configurable(self):
        doc = Document.objects.create(title='stuck.pdf', status='processing')
        self._age(doc, minutes=20)

        assert reconcile_interrupted_documents(max_age_minutes=60) == []
        assert reconcile_interrupted_documents(max_age_minutes=10) == [doc.id]

    def test_the_boundary_is_inclusive(self):
        doc = Document.objects.create(title='edge.pdf', status='processing')
        self._age(doc, minutes=STALE_PROCESSING_MINUTES, seconds=5)

        assert reconcile_interrupted_documents() == [doc.id]

    def test_running_twice_is_harmless(self):
        # Called on every boot; a second call must find nothing left to do
        # rather than re-flagging or erroring.
        doc = Document.objects.create(title='stuck.pdf', status='processing')
        self._age(doc, minutes=STALE_PROCESSING_MINUTES + 5)

        assert reconcile_interrupted_documents() == [doc.id]
        assert reconcile_interrupted_documents() == []

    def test_an_empty_table_is_fine(self):
        assert reconcile_interrupted_documents() == []

    @staticmethod
    def _age(doc, minutes=0, seconds=0):
        """Backdate ``updated_at``, which auto_now refuses to let us set."""
        Document.objects.filter(id=doc.id).update(
            updated_at=timezone.now() - timedelta(minutes=minutes, seconds=seconds)
        )
