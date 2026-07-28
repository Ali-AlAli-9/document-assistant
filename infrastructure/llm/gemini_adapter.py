import logging
from typing import Generator
from domain.ports.llm import LLMPort
from core.exceptions import LLMError
from openai import OpenAI, APITimeoutError, APIConnectionError
import httpx

logger = logging.getLogger(__name__)

_NETWORK_ERRORS = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    APIConnectionError,
)


class GeminiAdapter(LLMPort):
    def __init__(self, api_key: str, model: str = 'gemini-2.5-flash', timeout: int = 60, max_retries: int = 2):
        self.client = OpenAI(
            api_key=api_key,
            base_url='https://generativelanguage.googleapis.com/v1beta/openai/',
            timeout=httpx.Timeout(timeout, connect=10.0),
            max_retries=max_retries,
        )
        self.model = model
        logger.info(f'GeminiAdapter ready: {model} (timeout={timeout}s, retries={max_retries})')

    def _wrap_error(self, e: Exception) -> LLMError:
        if isinstance(e, _NETWORK_ERRORS + (APITimeoutError,)):
            return LLMError('تعذر الاتصال بخادم Gemini. تحقق من اتصال الإنترنت وأعد المحاولة.')
        return LLMError(f'Gemini request failed: {e}')

    def generate(self, prompt: str, **kwargs) -> str:
        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=[{'role': 'user', 'content': prompt}],
                **kwargs,
            )
            return completion.choices[0].message.content
        except Exception as e:
            raise self._wrap_error(e) from e

    def generate_stream(self, prompt: str) -> Generator[str, None, None]:
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=[{'role': 'user', 'content': prompt}],
                stream=True,
            )
            for chunk in stream:
                content = chunk.choices[0].delta.content
                if content:
                    yield content
        except Exception as e:
            raise self._wrap_error(e) from e
