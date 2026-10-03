import os
from core.config import BASE_DIR, Settings, cors_allows_all


def test_empty_cors_list_does_not_allow_every_origin():
    """Empty must mean "no cross-origin access", never "allow all".

    This is the regression this file exists for. The rule used to be
    `'*' in origins or (not DEBUG and not origins)`, so clearing the list in
    production — the obvious way to tighten it — silently opened the API to
    every origin on the internet.
    """
    assert cors_allows_all([]) is False
    assert cors_allows_all(None) is False


def test_explicit_wildcard_still_allows_all():
    # Opting IN to everything must keep working (local experiments only).
    assert cors_allows_all(['*']) is True


def test_specific_origins_do_not_allow_all():
    assert cors_allows_all(['http://localhost:5173', 'http://127.0.0.1:5173']) is False


def test_defaults(monkeypatch):
    # Check the code's true defaults, fully isolated from the machine's .env
    # (which may override values on the developer's machine).
    monkeypatch.setenv('SECRET_KEY', 'test-secret')  # required; has no default
    s = Settings(_env_file=None)
    assert s.DEBUG is False
    assert s.SECRET_KEY == 'test-secret'
    assert s.VECTOR_STORE_TYPE == 'chroma'
    assert s.CHROMA_PERSIST_DIR == str(BASE_DIR / 'chroma_db')
    assert s.CHROMA_COLLECTION_NAME == 'documents'
    assert s.EMBEDDING_MODEL == 'intfloat/multilingual-e5-small'
    assert s.EMBEDDING_PROVIDER == 'local'
    assert s.EMBEDDING_DIMENSION == 768
    assert s.CHUNK_SIZE == 500
    assert s.CHUNK_OVERLAP == 50
    assert s.RETRIEVAL_TOP_K == 20
    assert s.RETRIEVAL_SECTION_EXPAND == 3
    assert s.RETRIEVAL_MAX_CONTEXT_CHUNKS == 50
    assert s.RETRIEVAL_SCORE_THRESHOLD == 0.1
    assert s.OLLAMA_BASE_URL == 'http://localhost:11434'
    assert s.OLLAMA_MODEL == 'llama3.2:3b'
    assert s.MAX_UPLOAD_SIZE == 50 * 1024 * 1024
    assert s.ALLOWED_EXTENSIONS == ['.pdf', '.txt', '.docx']
    # On by default: a hosted deployment sees the platform proxy in
    # REMOTE_ADDR, and per-visitor rate limits depend on this.
    assert s.TRUST_PROXY_HEADERS is True


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
