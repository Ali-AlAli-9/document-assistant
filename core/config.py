from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

BASE_DIR = Path(__file__).resolve().parent.parent

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
    EMBEDDING_MODEL: str = 'all-MiniLM-L6-v2'
    EMBEDDING_MODEL_DIR: str = str(BASE_DIR / 'models')
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50
    RETRIEVAL_TOP_K: int = 10
    RETRIEVAL_SCORE_THRESHOLD: float = 0.1
    LLM_PROVIDER: Literal['ollama', 'gemini'] = 'ollama'
    OLLAMA_BASE_URL: str = 'http://localhost:11434'
    OLLAMA_MODEL: str = 'llama3.2:3b'
    GEMINI_API_KEY: str = ''
    GEMINI_MODEL: str = 'gemini-2.5-flash'
    LLM_TIMEOUT: int = 60
    LLM_MAX_RETRIES: int = 2
    CONVERSATION_HISTORY_LENGTH: int = 6
    LLM_MAX_CONTEXT_CHARS: int = 4000

    MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024
    ALLOWED_EXTENSIONS: list[str] = ['.pdf', '.txt', '.docx']

settings = Settings()