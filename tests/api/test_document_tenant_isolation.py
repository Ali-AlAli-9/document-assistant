"""Documents belong to the account that uploaded them.

Why this file exists (audit finding, HIGH):

    ``documents.Document`` had no owner at all, so every user-facing document
    operation was an IDOR: ``GET /api/documents`` returned EVERY user's
    documents, ``GET/DELETE /api/documents/{id}`` accepted any id, and — because
    retrieval walks the caller's documents one by one — a chat answer could
    quote the text of a file somebody else had uploaded. Registration is open,
    so on a public URL an account is one POST away.

The contract pinned here:

1. Two accounts never see each other's documents (list, get, retry, delete).
2. A foreign document is a 404, never a 403: a 403 would confirm it exists.
3. Deleting someone else's document leaves their chunks in the shared vector
    collection untouched.
4. Retrieval is scoped to the asker's own documents, so no source excerpt can
    come from another account's file.
5. Unowned rows (written before this field existed) belong to nobody and are
    therefore visible to nobody — never to everyone.
"""

import json

import pytest
from django.contrib.auth.models import User

from documents.models import Document as DocumentModel


PASSWORD = 'StrongPass!123'


def _make_account(username):
    return User.objects.create_user(username=username, password=PASSWORD)


def _login_as(client, user):
    """Log in as an EXISTING account.

    Registration auto-logs-in a brand-new user, so building the fixture by
    registering and then attaching documents to a separately created user was
    comparing two different primary keys — the owner then couldn't see their own
    document, which reads exactly like a broken owner filter.
    """
    resp = client.post(
        '/api/auth/login',
        data=json.dumps({'username': user.username, 'password': PASSWORD}),
        content_type='application/json',
    )
    assert resp.status_code == 200, resp.content
    return resp


def _owned_document(owner, title='contract.pdf'):
    assert owner is not None
    return DocumentModel.objects.create(
        title=title,
        status='ready',
        owner=owner,
    )


@pytest.fixture(autouse=True)
def _isolate_rate_limit():
    """The limiter counts per IP in a process-wide cache shared by every test
    file; without this these registrations inherit another file's counter."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
class TestListingIsScoped:
    def test_a_user_with_no_documents_of_their_own_sees_none(self, client):
        # Alice uploaded nothing; Bob uploaded one. An unscoped get_all() would
        # hand her Bob's file — this is the exact leak that was found.
        _owned_document(_make_account('bob'), 'bob-secret.pdf')
        alice = _make_account('alice')

        _login_as(client, alice)
        titles = {d['title'] for d in client.get('/api/documents').json()}

        assert titles == set()

    def test_the_owner_sees_their_own_document(self, client):
        alice = _make_account('alice')
        _owned_document(alice, 'alice-secret.pdf')

        _login_as(client, alice)
        titles = {d['title'] for d in client.get('/api/documents').json()}

        assert titles == {'alice-secret.pdf'}

    def test_titles_of_other_users_never_appear(self, client):
        _owned_document(_make_account('bob'), 'bob-hr-contracts.pdf')
        alice = _make_account('alice')

        _login_as(client, alice)
        resp = client.get('/api/documents')
        # The 200 matters: "bob-hr-contracts is absent" also holds for a 401
        # body, so without this the test would pass for the wrong reason.
        assert resp.status_code == 200
        assert 'bob-hr-contracts' not in resp.content.decode()

    def test_unowned_rows_are_visible_to_nobody(self, client):
        # A row from before the owner field existed must not fall back to
        # "everyone", which is what an unscoped queryset would do.
        DocumentModel.objects.create(title='legacy.pdf', status='ready')
        alice = _make_account('alice')

        _login_as(client, alice)
        assert client.get('/api/documents').json() == []


@pytest.mark.django_db
class TestSingleDocumentIsScoped:
    def test_foreign_document_is_404_not_403(self, client):
        bob_id = _owned_document(_make_account('bob'), 'bob-secret.pdf').id
        alice = _make_account('alice')

        _login_as(client, alice)
        resp = client.get(f'/api/documents/{bob_id}')

        # 403 would tell the caller "this id exists, it is just not yours".
        assert resp.status_code == 404

    def test_retry_of_a_foreign_document_is_404(self, client):
        bob_id = _owned_document(_make_account('bob'), 'bob-failed.pdf').id
        DocumentModel.objects.filter(id=bob_id).update(status='failed')
        alice = _make_account('alice')

        _login_as(client, alice)
        resp = client.post(f'/api/documents/{bob_id}/retry', data='{}',
                           content_type='application/json')

        assert resp.status_code == 404

    def test_owner_can_still_read_their_own(self, client):
        alice = _make_account('alice')
        alice_id = _owned_document(alice, 'mine.pdf').id

        _login_as(client, alice)
        resp = client.get(f'/api/documents/{alice_id}')

        assert resp.status_code == 200
        assert resp.json()['title'] == 'mine.pdf'


@pytest.mark.django_db
class TestDeleteIsScoped:
    @pytest.fixture(autouse=True)
    def _stub_vector_store(self, monkeypatch):
        """Never touch the real ChromaDB from a test, and record what a delete
        would have removed."""
        from api.endpoints import documents as documents_module

        self.removed_from_store = []

        class _Store:
            def delete_by_document(self, doc_id):
                self.outer.removed_from_store.append(doc_id)

        store = _Store()
        store.outer = self
        monkeypatch.setattr(documents_module, 'get_vector_store', lambda: store)

    def test_deleting_a_foreign_document_is_404_and_changes_nothing(self, client):
        bob_id = _owned_document(_make_account('bob'), 'bob-secret.pdf').id
        alice = _make_account('alice')

        _login_as(client, alice)
        resp = client.delete(f'/api/documents/{bob_id}')

        assert resp.status_code == 404
        assert DocumentModel.objects.filter(id=bob_id).exists(), \
            'another account was able to delete this document'
        assert self.removed_from_store == []

    def test_foreign_chunks_are_not_removed_from_the_vector_store(self, client, monkeypatch):
        """The collection is shared, so a delete must be refused BEFORE any
        vector-store cleanup runs — otherwise a user could blind their
        neighbour's search by targeting their document id."""
        from api.endpoints import documents as documents_module

        bob_id = _owned_document(_make_account('bob'), 'bob-secret.pdf').id
        alice = _make_account('alice')

        class _Repo:
            def get_by_id(self, doc_id, owner_id=None):
                return None  # not this caller's document

            def delete(self, doc_id, owner_id=None):
                raise AssertionError('delete must not run for a foreign document')

        monkeypatch.setattr(documents_module, '_get_repo', lambda: _Repo())

        _login_as(client, alice)
        resp = client.delete(f'/api/documents/{bob_id}')

        assert resp.status_code == 404
        assert self.removed_from_store == []

    def test_owner_can_delete_their_own(self, client):
        alice = _make_account('alice')
        alice_id = _owned_document(alice, 'mine.pdf').id

        _login_as(client, alice)
        resp = client.delete(f'/api/documents/{alice_id}')

        assert resp.status_code in (200, 204)
        assert not DocumentModel.objects.filter(id=alice_id).exists()
        assert self.removed_from_store == [alice_id]


@pytest.mark.django_db
class TestRetrievalIsScoped:
    """The leak that mattered most: file text of one account surfacing in
    another account's answer as a source excerpt."""

    def test_search_never_touches_another_accounts_documents(self):
        from domain.entities.document import Document as DocumentEntity
        from domain.ports.vector_store import SearchResult

        alice = _make_account('alice')
        bob = _make_account('bob')
        alice_id = _owned_document(alice, 'alice.pdf').id
        _owned_document(bob, 'bob.pdf')

        asked_for = []

        class _Repo:
            def get_all(self, owner_id=None):
                asked_for.append(owner_id)
                return [DocumentEntity(id=alice_id, title='alice.pdf')]

        class _Store:
            def search(self, embedding, top_k=5, where=None):
                if where != {'doc_id': str(alice_id)}:
                    raise AssertionError(f'unexpected filter: {where}')
                return [SearchResult(content='ALICE PRIVATE TEXT', score=0.9,
                                     metadata={'doc_id': str(alice_id)})]

        class _Embedder:
            def encode_query(self, q):
                return [0.0]

        from application.commands.ask_question import AskQuestionHandler

        handler = AskQuestionHandler(
            vector_store=_Store(), embedder=_Embedder(), llm=None,
            document_repo=_Repo(),
        )
        results, _ = handler._retrieve('anything', owner_id=7)

        assert asked_for == [7], 'retrieval did not scope by owner'
        assert len(results) == 1
        assert results[0].content == 'ALICE PRIVATE TEXT'
