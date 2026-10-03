"""The writer lock must reach the HTTP layer as a 409, not a silent failure.

WHY
===
Chroma persists through SQLite, and two clients writing at once corrupt it. The
guidance used to be "stop the server before you run an ingest script" — folklore
that fails as an opaque SQLite error raised from inside the driver.

Locking the write paths only pays off if the refusal is USABLE. If
``VectorStoreBusy`` were swallowed by the endpoint's catch-all, the upload would
be silently marked failed with no explanation — and because only ``failed``
documents can be retried, the user would then be locked out of their own
document. That is strictly worse than the error it replaced. So these tests pin:

* the server, which is the real writer, actually takes the lock,
* a refusal answers 409 and names the holder,
* the document is left in a state the user can retry from.

The happy path is guarded too: a lock that leaked or misfired would break every
upload in the product.
"""

from unittest.mock import MagicMock

import pytest

from application.commands.upload_document import UploadDocumentHandler
from domain.entities.document import Document, DocumentStatus
from documents.models import Document as DocumentModel
from infrastructure.vector_store import writer_lock


@pytest.fixture
def lock_path(monkeypatch, tmp_path):
    """Point the lock at a throwaway directory so no real store is touched."""
    monkeypatch.setattr(writer_lock, '_resolve_lock_path', lambda: tmp_path / '.writer.lock')
    return tmp_path / '.writer.lock'


@pytest.fixture
def handler(monkeypatch):
    """A handler with mocked ports, plus fake extraction so no PDF is opened."""
    repo = MagicMock()
    store = MagicMock()
    embedder = MagicMock()

    repo.get_by_id.side_effect = lambda doc_id: Document(
        id=doc_id, title='a.pdf', file_path='a.pdf', status=DocumentStatus.PENDING
    )
    repo.save.side_effect = lambda doc: doc
    embedder.encode.side_effect = lambda chunks: [[0.0] for _ in chunks]

    handler = UploadDocumentHandler(repo=repo, vector_store=store, embedder=embedder)
    monkeypatch.setattr(handler, '_extract_text', lambda path: 'hello world')
    monkeypatch.setattr(handler, '_chunk_text', lambda text: ['a', 'b'])
    return handler, repo, store


def _busy(monkeypatch, message='held by another ingest'):
    def _raise(purpose=''):
        raise writer_lock.VectorStoreBusy(message)

    monkeypatch.setattr(writer_lock, 'vector_store_writer', _raise)


# --------------------------------------------------------------------------- #
# the server takes the lock                                                       #
# --------------------------------------------------------------------------- #


def test_ingest_holds_the_lock_while_it_writes(lock_path, handler):
    handler, repo, store = handler
    seen = []

    store.add.side_effect = lambda chunks: seen.append(writer_lock.read_lock() is not None)

    handler.handle_by_id(1)

    assert seen == [True], 'the server is the real writer and must hold the lock'


def test_retry_holds_one_lock_across_the_delete_and_the_insert(lock_path, handler):
    """One lock for both steps.

    Taking it twice in the same process is refused as re-entrant, and a gap
    between the delete and the insert would let another writer in — which is the
    exact corruption the lock exists to prevent.
    """
    handler, repo, store = handler
    seen = []

    store.delete_by_document.side_effect = lambda doc_id: seen.append(
        ('delete', writer_lock.read_lock() is not None)
    )
    store.add.side_effect = lambda chunks: seen.append(('add', writer_lock.read_lock() is not None))

    handler.retry_by_id(1)

    assert seen == [('delete', True), ('add', True)]


def test_the_lock_is_released_after_a_successful_ingest(lock_path, handler):
    """A leaked lock would make every later upload fail with no visible cause."""
    handler, repo, store = handler

    handler.handle_by_id(1)

    assert writer_lock.read_lock() is None


def test_the_lock_is_released_after_a_failed_ingest(lock_path, handler):
    handler, repo, store = handler
    store.add.side_effect = RuntimeError('chroma down')

    with pytest.raises(Exception):
        handler.handle_by_id(1)

    assert writer_lock.read_lock() is None


def test_an_ordinary_ingest_is_otherwise_unchanged(lock_path, handler):
    """The regression guard: nothing about the happy path moved."""
    handler, repo, store = handler

    doc = handler.handle_by_id(7)

    assert doc.status == DocumentStatus.READY
    assert doc.chunk_count == 2
    store.add.assert_called_once()


def test_a_busy_store_refuses_before_the_row_is_touched(lock_path, handler, monkeypatch):
    """The refusal must land BEFORE the row is marked processing.

    A row marked ``processing`` is not retryable, so a refused ingest that had
    already written the status would strand the document exactly like the
    interrupted-ingest bug this whole change set exists to fix.
    """
    handler, repo, store = handler
    _busy(monkeypatch)

    with pytest.raises(writer_lock.VectorStoreBusy):
        handler.handle_by_id(1)

    assert store.add.call_count == 0
    assert repo.save.call_count == 0


# --------------------------------------------------------------------------- #
# the refusal reaches the client as 409                                           #
# --------------------------------------------------------------------------- #


class _Uploaded:
    def __init__(self, name='a.pdf', size=1024):
        self.name = name
        self.size = size


class _User:
    def __init__(self, pk):
        self.pk = pk


class _Request:
    def __init__(self, pk):
        self.user = _User(pk)


@pytest.fixture
def account(db):
    """A real owner: the endpoints scope every lookup by ``request.user.pk``,
    so a document with no owner is invisible to the caller — a 404 that would
    mask whatever this file is actually testing."""
    from django.contrib.auth.models import User

    return User.objects.create_user('owner', password='secret')


@pytest.fixture
def request_for(account):
    return _Request(account.pk)


@pytest.mark.django_db
def test_upload_answers_409_when_the_store_is_held(monkeypatch, lock_path, request_for):
    from django.core.files.storage import default_storage

    from api.endpoints import documents as docs

    _busy(monkeypatch)
    monkeypatch.setattr(default_storage, 'save', lambda *a, **k: 'uploads/x.pdf')

    response = docs.upload_document(request_for, file=_Uploaded())

    assert response.status_code == 409
    # Naming the holder is the point: "busy" alone is the dead end this replaced.
    # ninja's Response is a Django JsonResponse, so the payload is encoded bytes.
    assert 'another ingest' in response.content.decode()


@pytest.mark.django_db
def test_a_refused_upload_stays_pending_so_it_can_be_retried(monkeypatch, lock_path, request_for):
    from django.core.files.storage import default_storage

    from api.endpoints import documents as docs

    _busy(monkeypatch)
    monkeypatch.setattr(default_storage, 'save', lambda *a, **k: 'uploads/x.pdf')

    docs.upload_document(request_for, file=_Uploaded())

    doc = DocumentModel.objects.order_by('-id').first()
    assert doc.status == 'pending'


@pytest.mark.django_db
def test_retry_answers_409_when_the_store_is_held(monkeypatch, lock_path, request_for, account):
    from api.endpoints import documents as docs

    _busy(monkeypatch)
    doc = DocumentModel.objects.create(title='a.pdf', status='failed', owner=account)

    response = docs.retry_document_endpoint(request_for, doc.id)

    assert response.status_code == 409
    doc.refresh_from_db()
    assert doc.status == 'failed'


@pytest.mark.django_db
def test_delete_answers_409_and_keeps_the_row(monkeypatch, lock_path, request_for, account):
    """The row must survive: deleting it while the chunks remain would leave
    chunks nothing points at, which no later command could account for."""
    from api.endpoints import documents as docs

    _busy(monkeypatch)
    doc = DocumentModel.objects.create(title='a.pdf', status='ready', owner=account)

    response = docs.delete_document(request_for, doc.id)

    assert response.status_code == 409
    assert DocumentModel.objects.filter(id=doc.id).exists() is True