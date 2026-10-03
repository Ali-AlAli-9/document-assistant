"""The reconcile command is the manual, auditable form of the boot repair.

WHY IT IS A COMMAND AND NOT ONLY A STARTUP HOOK
===============================================
The startup hook repairs documents nobody is watching. This command does the
same work on demand and prints exactly what it recovered, which is what you
need when a user reports a document stuck at "قيد المعالجة" and you need to see
whether the row was in fact abandoned, and which ones.

Its exit behaviour is deliberately boring: it reports, it does not raise. A
recovery command that fails loudly on a database with nothing to fix would be
useless.
"""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from application.services.reconcile import STALE_PROCESSING_MINUTES
from api.management.commands.reconcile_documents import NOTHING_STUCK
from documents.models import Document


def _stale_document(minutes=30):
    doc = Document.objects.create(title='stuck.pdf', status='processing')
    Document.objects.filter(id=doc.id).update(
        updated_at=timezone.now() - timedelta(minutes=minutes)
    )
    return doc


@pytest.mark.django_db
def test_it_recovers_an_abandoned_row():
    doc = _stale_document()
    out = StringIO()

    call_command('reconcile_documents', stdout=out)

    doc.refresh_from_db()
    assert doc.status == 'failed'
    assert str(doc.id) in out.getvalue()


@pytest.mark.django_db
def test_it_leaves_a_running_ingest_alone():
    doc = Document.objects.create(title='running.pdf', status='processing')

    call_command('reconcile_documents', stdout=StringIO())

    doc.refresh_from_db()
    assert doc.status == 'processing'


@pytest.mark.django_db
def test_the_threshold_can_be_overridden():
    doc = _stale_document(minutes=20)
    out = StringIO()

    call_command('reconcile_documents', '--minutes', '60', stdout=out)

    doc.refresh_from_db()
    assert doc.status == 'processing'


@pytest.mark.django_db
def test_a_clean_database_reports_zero_without_failing():
    out = StringIO()

    call_command('reconcile_documents', stdout=out)

    assert NOTHING_STUCK in out.getvalue()


@pytest.mark.django_db
def test_running_it_twice_is_harmless():
    doc = _stale_document()

    call_command('reconcile_documents', stdout=StringIO())
    out = StringIO()
    call_command('reconcile_documents', stdout=out)

    doc.refresh_from_db()
    assert doc.status == 'failed'
    assert NOTHING_STUCK in out.getvalue()