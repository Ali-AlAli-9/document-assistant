import pytest
from core.exceptions import (
    AppError,
    DocumentError,
    IngestionError,
    FileNotAllowedError,
    VectorStoreError,
    LLMError,
    ChatError,
)


def test_app_error_base():
    assert issubclass(DocumentError, AppError)
    assert issubclass(VectorStoreError, AppError)
    assert issubclass(LLMError, AppError)
    assert issubclass(ChatError, AppError)
    assert issubclass(IngestionError, DocumentError)
    assert issubclass(FileNotAllowedError, DocumentError)


def test_app_error_message():
    with pytest.raises(AppError, match='test error'):
        raise AppError('test error')


def test_document_error_message():
    with pytest.raises(DocumentError, match='doc error'):
        raise DocumentError('doc error')


def test_file_not_allowed():
    with pytest.raises(FileNotAllowedError, match='not allowed'):
        raise FileNotAllowedError('not allowed')


def test_ingestion_error():
    with pytest.raises(IngestionError, match='ingestion failed'):
        raise IngestionError('ingestion failed')


def test_catch_app_error_catches_all():
    for exc in [DocumentError, VectorStoreError, LLMError, ChatError]:
        with pytest.raises(AppError):
            raise exc()

    for exc in [IngestionError, FileNotAllowedError]:
        with pytest.raises(DocumentError):
            raise exc()


def test_exception_str():
    assert str(AppError('hello')) == 'hello'
    assert str(AppError()) == ''
