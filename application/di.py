import threading
import logging
from core.config import settings
from domain.ports.vector_store import VectorStorePort
from domain.ports.embedding import EmbeddingPort
from domain.ports.llm import LLMPort
from domain.ports.document_repository import DocumentRepositoryPort
from domain.entities.conversation import Conversation

logger = logging.getLogger(__name__)

_conversations: dict[str, Conversation] = {}
_conversations_lock = threading.Lock()
MAX_CONVERSATIONS = 1
_embedder_status: dict[str, str] = {'state': 'not_loaded', 'error': ''}
_embedder_lock = threading.Lock()


def get_conversations() -> dict[str, Conversation]:
    with _conversations_lock:
        if len(_conversations) >= MAX_CONVERSATIONS:
            oldest_key = next(iter(_conversations))
            del _conversations[oldest_key]
            logger.info(f'Evicted oldest conversation: {oldest_key}')
    return _conversations


def clear_conversations() -> None:
    with _conversations_lock:
        _conversations.clear()
    logger.info('Conversations cleared')


def get_embedder_status() -> dict[str, str]:
    with _embedder_lock:
        return _embedder_status.copy()


_vector_store_instance: VectorStorePort | None = None
_vector_store_lock = threading.Lock()


def get_vector_store() -> VectorStorePort:
    global _vector_store_instance
    if _vector_store_instance is not None:
        return _vector_store_instance
    with _vector_store_lock:
        if _vector_store_instance is not None:
            return _vector_store_instance
        if settings.VECTOR_STORE_TYPE == 'chroma':
            from infrastructure.vector_store.chroma_adapter import ChromaAdapter
            _vector_store_instance = ChromaAdapter(
                persist_dir=settings.CHROMA_PERSIST_DIR,
                collection_name=settings.CHROMA_COLLECTION_NAME,
            )
            return _vector_store_instance
        raise ValueError(f'Unknown vector store: {settings.VECTOR_STORE_TYPE}')


_embedder_instance: EmbeddingPort | None = None


def get_embedder() -> EmbeddingPort:
    global _embedder_instance
    if _embedder_instance is not None:
        return _embedder_instance
    with _embedder_lock:
        if _embedder_instance is not None:
            return _embedder_instance
        _embedder_status['state'] = 'loading'
        _embedder_status['error'] = ''
    try:
        from infrastructure.embedding.sentence_transformer_adapter import (
            SentenceTransformerAdapter,
        )
        embedder = SentenceTransformerAdapter(
            model_name=settings.EMBEDDING_MODEL,
            model_dir=settings.EMBEDDING_MODEL_DIR,
        )
        with _embedder_lock:
            _embedder_instance = embedder
            _embedder_status['state'] = 'ready'
        return embedder
    except Exception as e:
        with _embedder_lock:
            _embedder_status['state'] = 'error'
            _embedder_status['error'] = str(e)
        raise


def preload_embedder() -> None:
    try:
        get_embedder()
        logger.info('Embedder preloaded successfully')
    except Exception:
        logger.warning('Embedder preload failed, will retry on first use')


_llm_cache: dict[str, LLMPort] = {}
_llm_cache_lock = threading.Lock()
MAX_LLM_CACHE = 10


def get_llm(provider: str | None = None, api_key: str | None = None) -> LLMPort:
    target = provider or settings.LLM_PROVIDER
    cache_key = f'{target}:{api_key or ""}'

    with _llm_cache_lock:
        if cache_key in _llm_cache:
            return _llm_cache[cache_key]
        if len(_llm_cache) >= MAX_LLM_CACHE:
            oldest_key = next(iter(_llm_cache))
            del _llm_cache[oldest_key]
            logger.info(f'Evicted oldest LLM cache entry: {oldest_key}')

    if target == 'gemini':
        from infrastructure.llm.gemini_adapter import GeminiAdapter
        llm = GeminiAdapter(
            api_key=api_key or settings.GEMINI_API_KEY,
            model=settings.GEMINI_MODEL,
            timeout=settings.LLM_TIMEOUT,
            max_retries=settings.LLM_MAX_RETRIES,
        )
    else:
        from infrastructure.llm.ollama_adapter import OllamaAdapter
        llm = OllamaAdapter(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.OLLAMA_MODEL,
            timeout=settings.LLM_TIMEOUT,
        )

    with _llm_cache_lock:
        _llm_cache[cache_key] = llm
    return llm


_document_repo_instance: DocumentRepositoryPort | None = None
_document_repo_lock = threading.Lock()


def get_document_repo() -> DocumentRepositoryPort:
    global _document_repo_instance
    if _document_repo_instance is not None:
        return _document_repo_instance
    with _document_repo_lock:
        if _document_repo_instance is not None:
            return _document_repo_instance
        from infrastructure.repository.django_document_repo import DjangoDocumentRepository
        _document_repo_instance = DjangoDocumentRepository()
        return _document_repo_instance


_upload_handler_instance = None
_upload_handler_lock = threading.Lock()


def get_upload_handler():
    global _upload_handler_instance
    if _upload_handler_instance is not None:
        return _upload_handler_instance
    with _upload_handler_lock:
        if _upload_handler_instance is not None:
            return _upload_handler_instance
        from application.commands.upload_document import UploadDocumentHandler
        _upload_handler_instance = UploadDocumentHandler(
            repo=get_document_repo(),
            vector_store=get_vector_store(),
            embedder=get_embedder(),
        )
        return _upload_handler_instance


_ask_handler_cache: dict[str, AskQuestionHandler] = {}
_ask_handler_lock = threading.Lock()
MAX_ASK_HANDLER_CACHE = 10


def get_ask_handler(provider: str | None = None, api_key: str | None = None):
    from application.commands.ask_question import AskQuestionHandler
    target = provider or settings.LLM_PROVIDER
    cache_key = f'{target}:{api_key or ""}'
    with _ask_handler_lock:
        if cache_key in _ask_handler_cache:
            return _ask_handler_cache[cache_key]
        if len(_ask_handler_cache) >= MAX_ASK_HANDLER_CACHE:
            oldest_key = next(iter(_ask_handler_cache))
            del _ask_handler_cache[oldest_key]
            logger.info(f'Evicted oldest ask handler cache entry: {oldest_key}')
    handler = AskQuestionHandler(
        vector_store=get_vector_store(),
        embedder=get_embedder(),
        llm=get_llm(provider, api_key),
        conversations=get_conversations(),
    )
    with _ask_handler_lock:
        _ask_handler_cache[cache_key] = handler
    return handler
