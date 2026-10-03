"""Gemini embedContent adapter — the API-backed EmbeddingPort.

Used when ``EMBEDDING_PROVIDER`` is ``gemini``, which is NOT the default: the app
ships multilingual-e5-small, offline and quota-free, and reaches this class only
if you opt in. Embeddings come from Google AI Studio's native REST endpoint
(``v1beta/models/<model>:embedContent``); there is no OpenAI-compatible
embeddings path, so this adapter talks to the REST API directly.

Why this exists: the model that used to be the default (all-MiniLM-L6-v2) carries
an English-only WordPiece vocabulary and tokenizes Arabic letter-by-letter, so
every Arabic sentence collapses to near the same vector and retrieval becomes
noise. On the same corpus and the same queries, gemini-embedding-001 ranked the
answer section 167/265 -> 3/265, and a nonsense control correctly fell to the
bottom instead of producing a plausible ranking. The switch to multilingual-e5-small
closed most of that gap locally (8/9 Hits@5) without a quota; what remains of it
is the ceiling this path exists to reach.

The API key is never logged. Transient failures are retried with backoff on
429 and 5xx (the free tier throttles — two concurrent ingestion runs measured
repeated 429 bursts), honoring Retry-After when present, and any other
transport/HTTP error is wrapped in a clear message. Like the LLM GeminiAdapter,
this assumes the deploy is allowed to reach Google (the same network dependency
the LLM already has).
"""
import logging
import time

import httpx

from domain.ports.embedding import EmbeddingPort

logger = logging.getLogger(__name__)

_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta'


class GeminiEmbeddingAdapter(EmbeddingPort):
    def __init__(
        self,
        api_key: str,
        model: str = 'gemini-embedding-001',
        dimension: int = 768,
        timeout: int = 40,
        max_attempts: int = 6,
        client: httpx.Client | None = None,
    ):
        if not api_key:
            raise ValueError('GEMINI_API_KEY is empty; set it in the environment')
        if not (1 <= dimension <= 3072):
            raise ValueError(f'Invalid embedding dimension: {dimension}')
        self._api_key = api_key
        self._model = model
        self._dimension = dimension
        self._timeout = timeout
        self._max_attempts = max_attempts
        # Injectable for tests (httpx.MockTransport) and for callers that want
        # their own connection pooling.
        self._client = client or httpx.Client(
            base_url=_BASE_URL,
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
        logger.info(f'GeminiEmbeddingAdapter ready: {model} ({dimension}d)')

    @staticmethod
    def _backoff(response: httpx.Response, attempt: int) -> None:
        """Exponential sleep, promoted by a Retry-After header.

        Sustained 429 bursts happen when the free tier is shared (measured:
        two concurrent ingestion runs make the API return 429 repeatedly and
        a 4-attempt adapter aborts a 228-chunk document). The header, when
        present, is authoritative; otherwise backoff doubles per attempt and
        is capped so a multi-chunk upload cannot stall for minutes.
        """
        wait = min(2 ** (attempt + 1), 30)
        header = response.headers.get('retry-after')
        if header and header.isdigit():
            wait = max(wait, min(int(header), 30))
        time.sleep(wait)

    def _embed_one(self, text: str) -> list[float]:
        """Embed a single text via embedContent, retrying 429/5xx with backoff."""
        body = {
            'model': f'models/{self._model}',
            'content': {'parts': [{'text': text}]},
            'outputDimensionality': self._dimension,
        }
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                resp = self._client.post(
                    f'/models/{self._model}:embedContent',
                    json=body,
                    headers={'x-goog-api-key': self._api_key},
                )
                if resp.status_code in (429, 500, 502, 503, 504):
                    self._backoff(resp, attempt)
                    continue
                resp.raise_for_status()
                return resp.json()['embedding']['values']
            except (httpx.HTTPStatusError, httpx.TransportError, KeyError, ValueError) as exc:
                last_error = exc
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code in (429, 500, 502, 503, 504)
                ):
                    self._backoff(exc.response, attempt)
                    continue
                break
        detail = last_error if last_error is not None else 'retries exhausted (429/5xx)'
        raise RuntimeError(f'Gemini embedContent failed: {detail}')

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def encode_query(self, text: str) -> list[float]:
        return self._embed_one(text)

    @property
    def dimension(self) -> int:
        return self._dimension