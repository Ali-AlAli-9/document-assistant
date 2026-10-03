from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

BASE_DIR = Path(__file__).resolve().parent.parent


def cors_allows_all(origins: list[str] | None) -> bool:
    """Whether django-cors-headers should accept EVERY origin.

    An empty list means "no cross-origin access" — never "allow all". Production
    serves the SPA from this same origin, so CORS is a development-only concern
    (the Vite dev server on :5173). An earlier version read an empty list in
    production as "allow all", which meant the obvious tightening step — clear
    the list — silently opened the app up instead of closing it.
    """
    return '*' in (origins or [])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / '.env', env_file_encoding='utf-8')

    DEBUG: bool = False
    SECRET_KEY: str
    VECTOR_STORE_TYPE: Literal['chroma'] = 'chroma'

    ALLOWED_HOSTS: list[str] = ['localhost', '127.0.0.1']
    CORS_ORIGINS: list[str] = ['http://localhost:5173', 'http://127.0.0.1:5173']

    DB_NAME: str = 'rag_django'
    DB_USER: str = 'postgres'
    DB_PASSWORD: str = ''
    DB_HOST: str = 'localhost'
    DB_PORT: int = 5432

    CHROMA_PERSIST_DIR: str = str(BASE_DIR / 'chroma_db')
    CHROMA_COLLECTION_NAME: str = 'documents'

    # Arms the destructive exit cleanup in api.apps. Declared here so the value
    # shipped in .env.example and render.yaml is a known setting rather than an
    # "extra input" that pydantic rejects — which is what made those files a
    # latent boot failure, unnoticed only because no local .env set it.
    RAG_CLEANUP_ON_EXIT: bool = False
    # The canonical HuggingFace repo id. Only its LAST SEGMENT is used to find
    # the baked snapshot on disk (models/multilingual-e5-small), so the repo id
    # can be written in full without breaking local runs.
    #
    # multilingual-e5-small replaced all-MiniLM-L6-v2, whose WordPiece vocabulary
    # is English-only: it tokenizes Arabic letter-by-letter, so Arabic sentences
    # collapse toward each other and retrieval turns into noise (measured: the
    # correct section ranked 167/265). e5-small covers 100+ languages and
    # measures 8/9 Hits@5 on the demo corpus.
    #
    # 384 dims, identical to the model it replaced, so an existing Chroma
    # collection stays dimension-valid. The vectors themselves are NOT compatible
    # across models, so anything ingested before the switch must be re-ingested —
    # the collection has to be emptied first, or old and new vectors mix.
    EMBEDDING_MODEL: str = 'intfloat/multilingual-e5-small'
    EMBEDDING_MODEL_DIR: str = str(BASE_DIR / 'models')
    # 'local'  -> loads a sentence-transformers snapshot from EMBEDDING_MODEL_DIR
    # 'gemini' -> calls Google AI Studio embedContent with GEMINI_API_KEY;
    #             EMBEDDING_MODEL then holds the Gemini model id and
    #             GEMINI_API_KEY must be set (the same key the LLM adapter uses).
    EMBEDDING_PROVIDER: Literal['local', 'gemini'] = 'local'
    # Target dimensionality for API embeddings (gemini-embedding-001 supports
    # 1..3072; 768 keeps the index small while preserving retrieval quality).
    EMBEDDING_DIMENSION: int = 768
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50
    # Top-K candidates per document. Raised from 10 so breadth questions
    # ("ابحث في كل الفصول عن ...") can reach more of a long document.
    RETRIEVAL_TOP_K: int = 20
    # Section expansion: how many following sibling chunks each retrieved
    # section heading pulls into context (keeps the section body with its head).
    RETRIEVAL_SECTION_EXPAND: int = 3
    # Hard ceiling on chunks handed to the LLM after dedup of top-K + expansion.
    RETRIEVAL_MAX_CONTEXT_CHUNKS: int = 50
    RETRIEVAL_SCORE_THRESHOLD: float = 0.1
    LLM_PROVIDER: Literal['ollama', 'gemini'] = 'ollama'
    OLLAMA_BASE_URL: str = 'http://localhost:11434'
    OLLAMA_MODEL: str = 'llama3.2:3b'
    GEMINI_API_KEY: str = ''
    # gemini-2.5-flash still EXISTS (it is in ListModels and answers 200 on the
    # native endpoint), but its quota is exhausted on the tier this app's key is
    # on: measured, the first call succeeded and the next two returned
    # 429 RESOURCE_EXHAUSTED. gemini-3.5-flash answered 200 on three consecutive
    # calls, so the model is what moved, not the network and not the key.
    #
    # Pinned by name rather than set to `gemini-flash-latest`, for the same
    # reason the Dockerfile pins EMBEDDING_REVISION: an alias that follows
    # "newest flash" would change what the assistant answers on a day no line of
    # this repository changed and no test failed.
    GEMINI_MODEL: str = 'gemini-3.5-flash'
    LLM_TIMEOUT: int = 60
    LLM_MAX_RETRIES: int = 2
    CONVERSATION_HISTORY_LENGTH: int = 6
    LLM_MAX_CONTEXT_CHARS: int = 4000
    CONVERSATION_TTL_MINUTES: int = 30
    CONVERSATIONS_MAX_PER_SESSION: int = 10
    SOURCE_SNIPPET_MAX_CHARS: int = 350

    MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024
    ALLOWED_EXTENSIONS: list[str] = ['.pdf', '.txt', '.docx']

    # Trust X-Forwarded-* headers from the hosting platform's reverse proxy
    # (Render, Railway, Fly...). On a hosted deployment REMOTE_ADDR is the
    # proxy itself, so without this every visitor shares ONE bucket and the
    # per-IP limits in core.middleware throttle the entire site at once — one
    # visitor's 20 questions per minute would lock out everyone else.
    # Set to 0 only when the app is exposed directly to the internet, where a
    # client could spoof the header to reset its own counter.
    TRUST_PROXY_HEADERS: bool = True

settings = Settings()