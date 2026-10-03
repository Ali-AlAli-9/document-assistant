"""A checker that says "all fine" on broken data is worse than no checker.

WHY THESE TESTS EXIST
=====================
Cleanup used to run as independent ``try/except`` blocks, so a failure part way
through left the three stores disagreeing — rows pointing at files that no
longer exist, files nobody claims, chunks with no row. Every one of those
failures is silent: nothing raises, retrieval simply answers badly, and the
user cannot tell a missing-data problem from a retrieval bug.

``verify_consistency`` exists to name those states. The tests below pin that it
actually detects each one, and — just as important — that it does NOT report
noise, because a checker that cries wolf gets ignored exactly when it matters.

The exit code matters as much as the text: a non-zero code is what makes this
usable from CI or from a deployment script.
"""

from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command

from application.services import consistency
from documents.models import Document


@pytest.fixture
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def store(monkeypatch):
    from unittest.mock import MagicMock

    store = MagicMock()
    monkeypatch.setattr('application.di.get_vector_store', lambda: store)
    return store


def _upload(name):
    return default_storage.save(f'uploads/{name}', ContentFile(b'hello'))


def run():
    """Run the command and return (exit_code, combined output).

    Findings are written to stdout and the verdict to stderr, so both streams
    are captured: the test is about what the operator is told, wherever it
    lands.
    """
    out, err = StringIO(), StringIO()
    try:
        call_command('verify_consistency', stdout=out, stderr=err)
    except SystemExit as exc:
        return exc.code, out.getvalue() + err.getvalue()
    return 0, out.getvalue() + err.getvalue()


# --------------------------------------------------------------------------- #
# reporting                                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_a_healthy_store_reports_success(storage, store):
    path = _upload('good.pdf')
    Document.objects.create(title='good.pdf', file=path, status='ready', chunk_count=2)
    store.count_by_document.return_value = 2

    code, text = run()

    assert code == 0
    assert text.strip()


@pytest.mark.django_db
def test_a_missing_file_is_named_not_merely_counted(storage, store):
    # "1 problem" leaves the operator to guess; the id and the path are what
    # turn a report into an action.
    Document.objects.create(title='gone.pdf', file='uploads/gone.pdf', status='ready', chunk_count=0)
    store.count_by_document.return_value = 0

    _, text = run()

    assert 'gone.pdf' in text


@pytest.mark.django_db
def test_a_chunk_mismatch_shows_both_numbers(storage, store):
    # Declared vs actual is the whole diagnostic: which side is wrong, and by
    # how much, decides whether to re-ingest or to re-upload.
    Document.objects.create(title='mismatch.pdf', file='', status='ready', chunk_count=12)
    store.count_by_document.return_value = 3

    _, text = run()

    assert '12' in text and '3' in text


@pytest.mark.django_db
def test_an_orphan_file_is_listed(storage, store):
    _upload('stray.pdf')
    store.count_by_document.return_value = 0

    _, text = run()

    assert 'stray.pdf' in text


# --------------------------------------------------------------------------- #
# exit codes                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_problems_exit_non_zero(storage, store):
    Document.objects.create(title='gone.pdf', file='uploads/gone.pdf', status='ready', chunk_count=0)
    store.count_by_document.return_value = 0

    code, _ = run()

    assert code != 0


@pytest.mark.django_db
def test_a_healthy_store_exits_zero(storage, store):
    path = _upload('good.pdf')
    Document.objects.create(title='good.pdf', file=path, status='ready', chunk_count=1)
    store.count_by_document.return_value = 1

    code, text = run()

    assert code == 0
    assert text.strip()


@pytest.mark.django_db
def test_a_check_that_could_not_run_is_distinguishable_from_a_fault(storage, store):
    # "We could not check" must not be reported as "everything is fine" — a
    # green result that was never verified is the most dangerous output here.
    Document.objects.create(title='x.pdf', file='', status='ready', chunk_count=1)
    store.count_by_document.side_effect = RuntimeError('store down')

    code, text = run()

    assert code != 0
    assert 'store down' in text


# --------------------------------------------------------------------------- #
# the service it wraps                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_the_service_reports_each_finding_separately(storage, store):
    """One report per check: the remedy differs per failure."""
    _upload('stray.pdf')
    Document.objects.create(title='gone.pdf', file='uploads/gone.pdf', status='ready', chunk_count=4)
    store.count_by_document.return_value = 0

    report = consistency.verify_all(store)

    assert report.missing_files
    assert report.chunk_mismatches
    assert report.orphan_files == ['uploads/stray.pdf']
    assert report.ok is False