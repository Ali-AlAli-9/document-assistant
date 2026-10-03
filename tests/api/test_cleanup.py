"""Server-exit cleanup must be OFF unless explicitly armed, and correct when it is.

THE DEFAULT WAS DANGEROUS
==========================
The hook fired for any server process, so a single ``Ctrl+C`` during
development deleted the uploaded files, every document row, the ENTIRE vector
collection, every conversation and every session. The only guard was
``RAG_CLEANUP_ON_EXIT=0``, which had to be set by hand in two of three
environments — so a fresh clone, or the developer's own ``.env``, wiped the data
on the first stop. ``render.yaml`` and ``.env.example`` set it to ``0``; the
actual ``.env`` did not, and that is the environment the demo runs in.

The default is now fail-closed: an unset variable means KEEP THE DATA, and
deleting requires naming the intent explicitly.

WHY ARMING IS NOT ENOUGH ON ITS OWN
===================================
Even armed, cleanup ran as five independent ``try/except`` blocks. A failure in
the middle leaves the stores disagreeing — rows pointing at deleted files, or
files nobody claims — with the only symptom being a document that will not
open. So the deletion order is fixed (see ``run_cleanup``) and each step reports
what it actually did instead of pretending it succeeded.
"""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from api.apps import _cleanup_session, _is_server_process, _should_cleanup_on_exit
from application.services.cleanup import run_cleanup
from documents.models import Document


# --- arming: the destructive path must be explicit ---------------------------


def test_is_server_process_detects_runserver(monkeypatch):
    monkeypatch.setattr('sys.argv', ['manage.py', 'runserver'])
    assert _is_server_process() is True


def test_is_server_process_detects_wsgi(monkeypatch):
    monkeypatch.setattr('sys.argv', ['gunicorn', 'rag_project.wsgi:application'])
    assert _is_server_process() is True


def test_is_server_process_false_for_management_commands(monkeypatch):
    monkeypatch.setattr('sys.argv', ['manage.py', 'migrate'])
    assert _is_server_process() is False


def test_unset_variable_means_do_not_clean(monkeypatch):
    """The regression this file exists for: bare runserver, no env var."""
    monkeypatch.delenv('RAG_CLEANUP_ON_EXIT', raising=False)
    monkeypatch.setattr('sys.argv', ['manage.py', 'runserver'])
    assert _should_cleanup_on_exit() is False


def test_explicit_on_arms_cleanup_for_a_server(monkeypatch):
    monkeypatch.setenv('RAG_CLEANUP_ON_EXIT', '1')
    monkeypatch.setattr('api.apps._is_running_under_pytest', lambda: False)
    monkeypatch.setattr('sys.argv', ['manage.py', 'runserver'])
    assert _should_cleanup_on_exit() is True


def test_explicit_off_disables_even_for_a_server(monkeypatch):
    monkeypatch.setenv('RAG_CLEANUP_ON_EXIT', '0')
    monkeypatch.setattr('api.apps._is_running_under_pytest', lambda: False)
    monkeypatch.setattr('sys.argv', ['manage.py', 'runserver'])
    assert _should_cleanup_on_exit() is False


def test_armed_still_refuses_management_commands(monkeypatch):
    """Arming must not turn ``manage.py shell`` into a data-loss event."""
    monkeypatch.setenv('RAG_CLEANUP_ON_EXIT', '1')
    monkeypatch.setattr('api.apps._is_running_under_pytest', lambda: False)
    monkeypatch.setattr('sys.argv', ['manage.py', 'migrate'])
    assert _should_cleanup_on_exit() is False


def test_never_under_pytest(monkeypatch):
    monkeypatch.setenv('RAG_CLEANUP_ON_EXIT', '1')
    monkeypatch.setattr('sys.argv', ['manage.py', 'runserver'])
    assert _should_cleanup_on_exit() is False


def test_cleanup_session_noop_under_pytest(monkeypatch):
    monkeypatch.setenv('RAG_CLEANUP_ON_EXIT', '1')
    monkeypatch.setattr('sys.argv', ['manage.py', 'runserver'])
    _cleanup_session()  # must return before touching the DB


# --- what cleanup does when it is allowed to run -----------------------------


@pytest.fixture
def isolated(monkeypatch, settings, tmp_path):
    """No real Chroma, no real uploads directory."""
    settings.MEDIA_ROOT = str(tmp_path)

    store = MagicMock()
    store.delete_all = MagicMock()
    store.delete_by_document = MagicMock()
    monkeypatch.setattr('application.di.get_vector_store', lambda: store)

    conversation_repo = MagicMock()
    conversation_repo.clear_all = MagicMock()
    monkeypatch.setattr('application.di.get_conversation_repo', lambda: conversation_repo)

    return {'store': store, 'conversations': conversation_repo, 'media': tmp_path}


def _make_document(title='a.pdf', status='ready', chunk_count=3, owner=None):
    path = default_storage.save(f'uploads/{title}', ContentFile(b'hello'))
    return Document.objects.create(
        title=title, file=path, status=status, chunk_count=chunk_count, owner=owner
    )


@pytest.mark.django_db
def test_cleanup_wipes_data_but_keeps_accounts(isolated):
    user = User.objects.create_user('demo', password='secret')
    _make_document(owner=user)
    Session.objects.create(
        session_key='sess-abc',
        session_data='e30=',
        expire_date=timezone.now() + timedelta(days=1),
    )

    report = run_cleanup()

    assert Document.objects.count() == 0
    assert Session.objects.count() == 0
    isolated['conversations'].clear_all.assert_called_once()
    # The one thing that must survive a demo reset: the people who log in.
    assert User.objects.filter(id=user.id).exists() is True
    assert report['documents'] == 1


@pytest.mark.django_db
def test_vectors_are_removed_per_document_not_by_wiping_the_collection(isolated):
    """Deleting by id keeps the reset scoped and makes the order verifiable.

    Wiping the whole collection would also erase the chunks of any OTHER
    document the reset did not select — silent data loss for rows that survive.
    """
    _make_document('one.pdf')
    _make_document('two.pdf')

    run_cleanup()

    assert isolated['store'].delete_by_document.call_count == 2
    isolated['store'].delete_all.assert_not_called()


@pytest.mark.django_db
def test_a_vector_failure_does_not_strand_the_row(isolated):
    """The order is what makes this safe.

    Vectors are removed FIRST. If that step fails the row survives with its
    chunks, and a document whose row and chunks both still exist is a document
    that still works — the reset simply did not happen. The reverse order would
    delete the pointers and leave chunks nothing can reach.
    """
    doc = _make_document('one.pdf')
    isolated['store'].delete_by_document.side_effect = RuntimeError('chroma down')

    report = run_cleanup()

    assert Document.objects.filter(id=doc.id).exists() is True
    assert default_storage.exists(doc.file.name) is True
    assert report['vector_errors'] == [doc.id]
    assert report['vectors_deleted'] == 0


@pytest.mark.django_db
def test_files_are_deleted_after_the_rows(isolated):
    """Reverse order would leave files no row claims — invisible to the DB."""
    doc = _make_document('one.pdf')

    run_cleanup()

    assert not default_storage.exists(doc.file.name)


@pytest.mark.django_db
def test_only_selected_documents_are_touched(isolated):
    keep = _make_document('keep.pdf')
    drop = _make_document('drop.pdf')

    run_cleanup(documents=[drop])

    assert list(Document.objects.values_list('id', flat=True)) == [keep.id]
    isolated['store'].delete_by_document.assert_called_once_with(drop.id)


@pytest.mark.django_db
def test_a_title_prefix_limits_the_blast_radius(isolated):
    keep = _make_document('keep.pdf')
    drop = _make_document('demo-drop.pdf')

    run_cleanup(title_prefix='demo-')

    assert list(Document.objects.values_list('id', flat=True)) == [keep.id]


@pytest.mark.django_db
def test_the_report_names_what_actually_happened(isolated):
    """A step that failed must not be reported as done."""
    _make_document('one.pdf')
    isolated['store'].delete_by_document.side_effect = RuntimeError('chroma down')

    report = run_cleanup()

    assert report['documents'] == 0
    assert report['vector_errors'], 'a failed vector delete must be reported'
    assert report['vectors_deleted'] == 0


@pytest.mark.django_db
def test_an_unreachable_vector_store_deletes_nothing_and_says_why(isolated, monkeypatch):
    """The store could not even be opened — so nothing may be removed.

    Importing chromadb fails during interpreter shutdown (opentelemetry submits
    work to an already-closed thread pool), which is precisely when the atexit
    hook runs. Deleting the rows anyway would strand every chunk with nothing
    pointing at it; raising instead would print a traceback at shutdown and
    leave a half-cleaned store. The only safe outcome is a clean refusal.
    """
    doc = _make_document('one.pdf')

    def _unreachable():
        raise RuntimeError('cannot schedule new futures after interpreter shutdown')

    monkeypatch.setattr('application.di.get_vector_store', _unreachable)

    report = run_cleanup()

    assert Document.objects.filter(id=doc.id).exists() is True
    assert default_storage.exists(doc.file.name) is True
    assert isolated['conversations'].clear_all.call_count == 0
    assert report['vector_errors']
    assert report['documents'] == 0


@pytest.mark.django_db
def test_the_abort_report_explains_the_remedy(isolated, monkeypatch):
    def _unreachable():
        raise RuntimeError('boom')

    monkeypatch.setattr('application.di.get_vector_store', _unreachable)

    report = run_cleanup()

    assert 'boom' in ' '.join(report['vector_error_messages'])


@pytest.mark.django_db
def test_a_successful_run_reports_what_it_deleted(isolated):
    doc = _make_document('one.pdf')

    report = run_cleanup()

    assert report['documents'] >= 1
    assert report['files'] == 1
    assert report['vector_errors'] == []
