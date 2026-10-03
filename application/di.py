import threading
import hashlib
import logging
import time
from core.config import settings
from domain.ports.vector_store import VectorStorePort
from domain.ports.embedding import EmbeddingPort
from domain.ports.llm import LLMPort
from domain.ports.document_repository import DocumentRepositoryPort
from domain.ports.conversation_repository import ConversationRepositoryPort
from core.exceptions import MissingLLMCredentials

logger = logging.getLogger(__name__)

_embedder_status: dict[str, str] = {'state': 'not_loaded', 'error': ''}
# Two locks, on purpose. The status lock is held only for a dict copy, so
# /api/health/ keeps answering instantly even while a model is being built. The
# load lock is held across that slow construction. One lock for both would
# block the health endpoint for the whole load, and the platform's own probe
# would then start calling the service unhealthy.
_embedder_lock = threading.Lock()
_embedder_load_lock = threading.Lock()


def _set_embedder_status(state: str, error: str = '') -> None:
    with _embedder_lock:
        _embedder_status['state'] = state
        _embedder_status['error'] = error


def get_embedder_status() -> dict[str, str]:
    with _embedder_lock:
        return _embedder_status.copy()


_conversation_repo_instance: ConversationRepositoryPort | None = None
_conversation_repo_lock = threading.Lock()


def get_conversation_repo() -> ConversationRepositoryPort:
    global _conversation_repo_instance
    if _conversation_repo_instance is not None:
        return _conversation_repo_instance
    with _conversation_repo_lock:
        if _conversation_repo_instance is not None:
            return _conversation_repo_instance
        from infrastructure.repository.in_memory_conversation_repo import InMemoryConversationRepository
        _conversation_repo_instance = InMemoryConversationRepository(
            max_per_scope=settings.CONVERSATIONS_MAX_PER_SESSION,
            ttl_minutes=settings.CONVERSATION_TTL_MINUTES,
        )
        return _conversation_repo_instance


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
    """The one embedding model for this process, constructed at most once.

    The construction used to run OUTSIDE the lock, with the instance published
    only after it finished. Two callers arriving together therefore both saw
    ``None`` and each built a full copy — two weight sets and two torch
    runtimes on a 512MB instance, which is one of the ways a slow first upload
    could push the container over. Holding the load lock across the
    construction makes the second caller wait for the first and reuse it.
    """
    global _embedder_instance
    if _embedder_instance is not None:
        return _embedder_instance
    with _embedder_load_lock:
        if _embedder_instance is not None:
            return _embedder_instance
        _set_embedder_status('loading')
        try:
            if settings.EMBEDDING_PROVIDER == 'gemini':
                from infrastructure.embedding.gemini_embedding_adapter import (
                    GeminiEmbeddingAdapter,
                )
                embedder = GeminiEmbeddingAdapter(
                    api_key=settings.GEMINI_API_KEY,
                    model=settings.EMBEDDING_MODEL,
                    dimension=settings.EMBEDDING_DIMENSION,
                )
            else:
                from infrastructure.embedding.sentence_transformer_adapter import (
                    SentenceTransformerAdapter,
                )
                embedder = SentenceTransformerAdapter(
                    model_name=settings.EMBEDDING_MODEL,
                    model_dir=settings.EMBEDDING_MODEL_DIR,
                )
        except Exception as e:
            _set_embedder_status('error', str(e))
            raise
        _embedder_instance = embedder
        _set_embedder_status('ready')
        return embedder


def preload_embedder() -> None:
    """Load the model at boot, and pay torch's first-call cost while nobody waits.

    Measured on the deployment, with the model already loaded and reporting
    ``ready``: the first upload of a one-page PDF took 17.4s, and the identical
    file took 8.2s afterwards. That 9s gap is torch's first ``encode`` in the
    process — not the model load. Running it here spends it in the background
    thread that ``ApiConfig.ready`` already starts, so a visitor's first
    document behaves like every document after it.

    A failed warm-up is not fatal: the model is loaded and usable, and the
    first real request simply pays the cost itself.
    """
    try:
        embedder = get_embedder()
    except Exception:
        logger.warning('Embedder preload failed, will retry on first use')
        return
    if settings.EMBEDDING_PROVIDER == 'gemini':
        # Construction is cheap (no weights) and there is nothing to warm up on
        # disk; an API warm-up call at boot would just add a network dependency
        # to startup and spend a request on nothing.
        logger.info('Embedder preload: gemini provider, nothing to warm up')
        return
    try:
        started = time.monotonic()
        embedder.encode(['warmup'])
        logger.info(f'Embedder warm in {time.monotonic() - started:.1f}s')
    except Exception:
        logger.warning('Embedder warm-up failed; the first request will pay for it')


_llm_cache: dict[str, LLMPort] = {}
_llm_cache_lock = threading.Lock()
MAX_LLM_CACHE = 10

# Providers that cannot make a single call without a credential the ACCOUNT
# supplies. A set rather than a check at the call site, so that adding a hosted
# provider makes the refusal automatic instead of quietly inheriting a default.
_CREDENTIAL_REQUIRED_PROVIDERS = frozenset({'gemini'})


def _llm_cache_key(target: str, api_key: str | None) -> str:
    """Cache identity WITHOUT the secret.

    The key used to be f'{target}:{api_key}', which put every visitor's Gemini
    key into the cache key itself — and the eviction log line then printed it
    in clear text once MAX_LLM_CACHE distinct users had connected. A digest
    keeps the cache correct (same key -> same entry) while making the plaintext
    impossible to recover from a cache dump or a log line.
    """
    if not api_key:
        return target
    digest = hashlib.sha256(api_key.encode('utf-8')).hexdigest()[:12]
    return f'{target}:{digest}'


def get_llm(provider: str | None = None, api_key: str | None = None) -> LLMPort:
    target = provider or settings.LLM_PROVIDER

    # Fail closed, and fail closed EARLY — above the cache lookup, so a keyless
    # call can neither build a client nor be handed one cached by an earlier
    # caller that did have a key.
    #
    # This used to read `api_key or settings.GEMINI_API_KEY`. That expression
    # conflated two unrelated credentials. The server-wide key is the operator's
    # credential for the EMBEDDING provider (EMBEDDING_PROVIDER=gemini), and
    # paying for that is the operator's own decision. As an LLM fallback it was
    # something else entirely: a visitor who never entered a key spent the
    # operator's quota silently — no error, no prompt, and nothing in the
    # response to say whose account had paid for the answer. A shared secret no
    # user can see is not a convenience.
    #
    # settings.GEMINI_API_KEY is therefore still read for embeddings
    # (get_embedder) and nowhere else. A deployment that wants to fund chats must
    # make that an explicit, per-account decision rather than inherit it.
    if target in _CREDENTIAL_REQUIRED_PROVIDERS and not (api_key or '').strip():
        raise MissingLLMCredentials(target)

    cache_key = _llm_cache_key(target, api_key)

    with _llm_cache_lock:
        if cache_key in _llm_cache:
            return _llm_cache[cache_key]
        if len(_llm_cache) >= MAX_LLM_CACHE:
            oldest_key = next(iter(_llm_cache))
            del _llm_cache[oldest_key]
            logger.info(f'Evicted oldest LLM cache entry for provider: {target}')

    if target == 'gemini':
        from infrastructure.llm.gemini_adapter import GeminiAdapter
        llm = GeminiAdapter(
            api_key=api_key,
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


# The handler is imported inside get_ask_handler (it drags in the vector store
# and the LLM client), so its name is NOT bound when this module is imported.
# A bare ``dict[str, AskQuestionHandler]`` annotation is evaluated at import
# time on Python 3.12 — the version in the container — and raises NameError
# there, while 3.14 defers it (PEP 649) and hides the bug from every local run.
# Quoting keeps the annotation as documentation and the import on 3.12.
_ask_handler_cache: dict[str, 'AskQuestionHandler'] = {}
_ask_handler_lock = threading.Lock()
MAX_ASK_HANDLER_CACHE = 10


def get_ask_handler(provider: str | None = None, api_key: str | None = None):
    from application.commands.ask_question import AskQuestionHandler
    target = provider or settings.LLM_PROVIDER
    cache_key = _llm_cache_key(target, api_key)
    with _ask_handler_lock:
        if cache_key in _ask_handler_cache:
            return _ask_handler_cache[cache_key]
        if len(_ask_handler_cache) >= MAX_ASK_HANDLER_CACHE:
            oldest_key = next(iter(_ask_handler_cache))
            del _ask_handler_cache[oldest_key]
            logger.info(f'Evicted oldest ask handler entry for provider: {target}')
    handler = AskQuestionHandler(
        vector_store=get_vector_store(),
        embedder=get_embedder(),
        llm=get_llm(provider, api_key),
        conversation_repo=get_conversation_repo(),
        document_repo=get_document_repo(),
    )
    with _ask_handler_lock:
        _ask_handler_cache[cache_key] = handler
    return handler
