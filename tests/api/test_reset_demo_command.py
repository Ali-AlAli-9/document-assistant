"""Deleting data must be a command that names itself, asks first, and reports.

WHY
===
The behaviour these tests pin used to be an ``atexit`` hook: any server
shutdown wiped the uploads, every document row, the whole vector collection,
every conversation and every session. Nothing about that was visible — no
command in the history, no prompt, no output. So ``Ctrl+C`` during development
silently destroyed the demo PDF, and a spin-down on a hosted service would have
destroyed a visitor's upload.

Moving destruction behind a command fixes three things at once: it is visible
in the shell history, it can be scoped to a subset, and it can refuse to run
unattended.
"""

from io import StringIO

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from documents.models import Document


@pytest.fixture
def isolated(monkeypatch, settings, tmp_path):
    """No real Chroma, no real uploads directory."""
    settings.MEDIA_ROOT = str(tmp_path)

    from unittest.mock import MagicMock

    store = MagicMock()
    monkeypatch.setattr('application.di.get_vector_store', lambda: store)

    conversations = MagicMock()
    monkeypatch.setattr('application.di.get_conversation_repo', lambda: conversations)

    return {'store': store, 'conversations': conversations}


def _make_document(title, status='ready', chunk_count=3, owner=None):
    path = default_storage.save(f'uploads/{title}', ContentFile(b'hello'))
    return Document.objects.create(
        title=title, file=path, status=status, chunk_count=chunk_count, owner=owner
    )


# --------------------------------------------------------------------------- #
# asking before destroying                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_it_refuses_when_the_answer_is_no(isolated, monkeypatch):
    doc = _make_document('a.pdf')
    monkeypatch.setattr('builtins.input', lambda *a: 'n')
    out = StringIO()

    call_command('reset_demo', stdout=out)

    # "no" must mean "nothing happened", not "it deleted them anyway".
    assert Document.objects.filter(id=doc.id).exists() is True
    isolated['store'].delete_by_document.assert_not_called()


@pytest.mark.django_db
def test_a_refusal_explains_how_to_proceed(isolated, monkeypatch):
    monkeypatch.setattr('builtins.input', lambda *a: 'n')
    out = StringIO()

    call_command('reset_demo', stdout=out)

    assert '--yes' in out.getvalue()


@pytest.mark.django_db
def test_yes_performs_the_deletion(isolated, monkeypatch):
    _make_document('a.pdf')
    monkeypatch.setattr('builtins.input', lambda *a: 'y')
    out = StringIO()

    call_command('reset_demo', stdout=out)

    assert Document.objects.count() == 0
    isolated['store'].delete_by_document.assert_called_once()


@pytest.mark.django_db
def test_the_yes_flag_skips_the_prompt_entirely(isolated):
    """For automation the prompt must not block waiting for a human."""
    _make_document('a.pdf')

    call_command('reset_demo', '--yes', stdout=StringIO())

    assert Document.objects.count() == 0


# --------------------------------------------------------------------------- #
# what survives                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_accounts_survive_the_reset(isolated):
    user = User.objects.create_user('demo', password='secret')
    _make_document('a.pdf', owner=user)

    call_command('reset_demo', '--yes', stdout=StringIO())

    # The one thing a reset must never take: the people who can log back in.
    assert User.objects.filter(id=user.id).exists() is True


# --------------------------------------------------------------------------- #
# scoping                                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_a_prefix_leaves_other_documents_alone(isolated):
    keep = _make_document('final.pdf')
    drop = _make_document('demo-intro.pdf')

    call_command('reset_demo', '--yes', '--prefix', 'demo-', stdout=StringIO())

    assert list(Document.objects.values_list('id', flat=True)) == [keep.id]


@pytest.mark.django_db
def test_a_prefix_matching_nothing_deletes_nothing(isolated):
    keep = _make_document('final.pdf')

    call_command('reset_demo', '--yes', '--prefix', 'demoo', stdout=StringIO())

    # A typo must not read as "no filter" — that is how a scoped reset becomes
    # a full wipe without anyone noticing.
    assert list(Document.objects.values_list('id', flat=True)) == [keep.id]


@pytest.mark.django_db
def test_nothing_to_delete_is_a_success_not_a_crash(isolated):
    out = StringIO()

    call_command('reset_demo', '--yes', stdout=out)

    # Counts are printed unconditionally, so an empty reset reads as "0 deleted"
    # rather than as a command that silently did nothing.
    assert '0' in out.getvalue()


# --------------------------------------------------------------------------- #
# honest reporting                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_a_store_failure_is_reported_and_nothing_is_deleted(isolated):
    """The vector store is the only irreplaceable step.

    If it cannot be cleaned, deleting the rows would leave chunks nothing
    points at — unrecoverable except by re-ingesting. So the reset aborts and
    says why.
    """
    doc = _make_document('a.pdf')
    isolated['store'].delete_by_document.side_effect = RuntimeError('chroma down')
    out, err = StringIO(), StringIO()

    call_command('reset_demo', '--yes', stdout=out, stderr=err)

    assert Document.objects.filter(id=doc.id).exists() is True
    assert 'chroma down' in err.getvalue()


@pytest.mark.django_db
def test_a_busy_vector_store_is_refused(isolated, monkeypatch):
    """Two writers corrupt Chroma's SQLite file, so the second one is stopped."""
    doc = _make_document('a.pdf')

    def _busy(*args, **kwargs):
        from infrastructure.vector_store.writer_lock import VectorStoreBusy

        raise VectorStoreBusy('the store is busy')

    monkeypatch.setattr('infrastructure.vector_store.writer_lock.vector_store_writer', _busy)
    out, err = StringIO(), StringIO()

    call_command('reset_demo', '--yes', stdout=out, stderr=err)

    assert Document.objects.filter(id=doc.id).exists() is True
    assert 'busy' in err.getvalue()


# --------------------------------------------------------------------------- #
# sessions and conversations                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_conversations_are_cleared(isolated):
    _make_document('a.pdf')

    call_command('reset_demo', '--yes', stdout=StringIO())

    isolated['conversations'].clear_all.assert_called_once()