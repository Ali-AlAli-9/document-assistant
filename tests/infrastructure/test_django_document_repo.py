import pytest
import os
import tempfile
from domain.entities.document import Document, DocumentStatus
from infrastructure.repository.django_document_repo import DjangoDocumentRepository


pytestmark = pytest.mark.django_db


@pytest.fixture
def repo():
    return DjangoDocumentRepository()


def test_save_new_document(repo):
    doc = Document(title='test.pdf', file_path='')
    saved = repo.save(doc)
    assert saved.id is not None
    assert saved.title == 'test.pdf'
    assert saved.status == DocumentStatus.PENDING
    assert saved.file_size == 0  # derived from model.file, which is None


def test_save_and_get_by_id(repo):
    doc = Document(title='report.pdf', file_path='')
    saved = repo.save(doc)
    fetched = repo.get_by_id(saved.id)
    assert fetched is not None
    assert fetched.id == saved.id
    assert fetched.title == 'report.pdf'
    assert fetched.status == DocumentStatus.PENDING


def test_save_update_status(repo):
    doc = Document(title='test.pdf', file_path='')
    saved = repo.save(doc)
    saved.mark_ready(5)
    updated = repo.save(saved)
    assert updated.status == DocumentStatus.READY
    assert updated.chunk_count == 5
    fetched = repo.get_by_id(updated.id)
    assert fetched.status == DocumentStatus.READY
    assert fetched.chunk_count == 5


def test_get_by_id_not_found(repo):
    result = repo.get_by_id(99999)
    assert result is None


def test_get_all_empty(repo):
    docs = repo.get_all()
    assert docs == []


def test_get_all_multiple(repo):
    repo.save(Document(title='a.pdf', file_path=''))
    repo.save(Document(title='b.pdf', file_path=''))
    repo.save(Document(title='c.pdf', file_path=''))
    docs = repo.get_all()
    assert len(docs) == 3


def test_delete(repo):
    doc = repo.save(Document(title='test.pdf', file_path=''))
    assert repo.get_by_id(doc.id) is not None
    repo.delete(doc.id)
    assert repo.get_by_id(doc.id) is None


def test_delete_nonexistent(repo):
    repo.delete(99999)


def test_save_with_file(repo):
    with tempfile.NamedTemporaryFile(suffix='.txt', delete=False, mode='w', encoding='utf-8') as f:
        f.write('hello world')
        tmp_path = f.name
    try:
        doc = Document(title='test.txt', file_path=tmp_path, file_size=11)
        saved = repo.save(doc)
        assert saved.id is not None
        assert saved.title == 'test.txt'
        assert saved.file_size == 11
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def test_save_updates_status_and_chunk_count(repo):
    doc = repo.save(Document(title='first.pdf', file_path=''))
    doc.mark_ready(7)
    updated = repo.save(doc)
    assert updated.id == doc.id
    assert updated.status == DocumentStatus.READY
    assert updated.chunk_count == 7
    fetched = repo.get_by_id(doc.id)
    assert fetched.status == DocumentStatus.READY
    assert fetched.chunk_count == 7
