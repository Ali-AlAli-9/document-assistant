import requests
import json
import logging
from typing import Generator
from domain.ports.llm import LLMPort
from core.exceptions import LLMError

logger = logging.getLogger(__name__)


class OllamaAdapter(LLMPort):
    def __init__(self, base_url: str = 'http://localhost:11434', model: str = 'llama3.2:3b', timeout: int = 120):
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        logger.info(f'OllamaAdapter ready: {model}')

    def generate(self, prompt: str, **kwargs) -> str:
        try:
            response = requests.post(
                f'{self.base_url}/api/generate',
                json={'model': self.model, 'prompt': prompt, 'stream': False, **kwargs},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()['response']
        except requests.exceptions.ConnectionError as e:
            raise LLMError(f'Cannot connect to Ollama at {self.base_url}') from e
        except requests.exceptions.Timeout as e:
            raise LLMError('Ollama request timed out') from e
        except requests.exceptions.RequestException as e:
            raise LLMError(f'Ollama request failed: {e}') from e

    def generate_stream(self, prompt: str) -> Generator[str, None, None]:
        try:
            response = requests.post(
                f'{self.base_url}/api/generate',
                json={'model': self.model, 'prompt': prompt, 'stream': True},
                stream=True,
                timeout=(30, self.timeout),
            )
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(f'Ollama returned non-JSON line: {line[:100]}')
                        continue
                    if data.get('response'):
                        yield data['response']
        except requests.exceptions.ConnectionError as e:
            raise LLMError(f'Cannot connect to Ollama at {self.base_url}') from e
        except requests.exceptions.Timeout as e:
            raise LLMError('Ollama stream timed out') from e
        except requests.exceptions.RequestException as e:
            raise LLMError(f'Ollama stream failed: {e}') from e
