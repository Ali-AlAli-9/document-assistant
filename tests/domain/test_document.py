from datetime import datetime
from domain.entities.document import Document, DocumentStatus


def test_document_defaults():
    doc = Document()
    assert doc.id is None
    assert doc.title == ''
    assert doc.file_path == ''
    assert doc.status == DocumentStatus.PENDING
    assert doc.chunk_count == 0
    assert doc.file_size == 0
    assert isinstance(doc.uploaded_at, datetime)


def test_document_custom_values():
    ts = datetime(2025, 1, 1)
    doc = Document(id=1, title='test.pdf', status=DocumentStatus.PROCESSING, chunk_count=5, uploaded_at=ts)
    assert doc.id == 1
    assert doc.title == 'test.pdf'
    assert doc.status == DocumentStatus.PROCESSING
    assert doc.chunk_count == 5
    assert doc.uploaded_at == ts


def test_mark_processing():
    doc = Document()
    doc.mark_processing()
    assert doc.status == DocumentStatus.PROCESSING


def test_mark_ready():
    doc = Document(id=1, title='test.pdf')
    doc.mark_ready(5)
    assert doc.status == DocumentStatus.READY
    assert doc.chunk_count == 5


def test_mark_failed():
    doc = Document(id=1, title='test.pdf')
    doc.mark_failed()
    assert doc.status == DocumentStatus.FAILED


def test_is_processable_pending():
    doc = Document()
    assert doc.is_processable is True


def test_is_processable_failed():
    doc = Document(status=DocumentStatus.FAILED)
    assert doc.is_processable is True


def test_is_processable_processing():
    doc = Document(status=DocumentStatus.PROCESSING)
    assert doc.is_processable is False


def test_is_processable_ready():
    doc = Document(status=DocumentStatus.READY)
    assert doc.is_processable is False


def test_status_chains():
    doc = Document()
    assert doc.status == DocumentStatus.PENDING
    doc.mark_processing()
    assert doc.status == DocumentStatus.PROCESSING
    doc.mark_ready(10)
    assert doc.status == DocumentStatus.READY
    assert doc.chunk_count == 10
    doc.mark_failed()
    assert doc.status == DocumentStatus.FAILED
