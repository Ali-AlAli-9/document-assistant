import os
from core.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.setenv('DEBUG', 'false')
    s = Settings()
    assert s.DEBUG is False
    assert s.SECRET_KEY is not None
    assert s.VECTOR_STORE_TYPE == 'chroma'
    assert s.CHROMA_PERSIST_DIR == 'chroma_db'
    assert s.CHROMA_COLLECTION_NAME == 'documents'
    assert s.EMBEDDING_MODEL == 'all-MiniLM-L6-v2'
    assert s.CHUNK_SIZE == 500
    assert s.CHUNK_OVERLAP == 50
    assert s.RETRIEVAL_TOP_K == 5
    assert s.RETRIEVAL_SCORE_THRESHOLD == 0.1
    assert s.OLLAMA_BASE_URL == 'http://localhost:11434'
    assert s.OLLAMA_MODEL == 'llama3.2:3b'
    assert s.MAX_UPLOAD_SIZE == 50 * 1024 * 1024
    assert s.ALLOWED_EXTENSIONS == ['.pdf', '.txt', '.docx']


def test_env_override(monkeypatch):
    monkeypatch.setenv('OLLAMA_MODEL', 'llama3.1:8b')
    monkeypatch.setenv('CHUNK_SIZE', '1000')
    monkeypatch.setenv('DEBUG', 'true')
    s = Settings()
    assert s.OLLAMA_MODEL == 'llama3.1:8b'
    assert s.CHUNK_SIZE == 1000
    assert s.DEBUG is True


def test_allowed_extensions_default():
    s = Settings()
    assert '.pdf' in s.ALLOWED_EXTENSIONS
    assert '.txt' in s.ALLOWED_EXTENSIONS
    assert '.docx' in s.ALLOWED_EXTENSIONS
    assert '.png' not in s.ALLOWED_EXTENSIONS
    assert '.py' not in s.ALLOWED_EXTENSIONS


def test_max_upload_size():
    s = Settings()
    assert s.MAX_UPLOAD_SIZE == 52428800
