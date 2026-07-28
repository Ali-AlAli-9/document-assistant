from abc import ABC, abstractmethod
from typing import Generator

class LLMPort(ABC):
    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        ...

    @abstractmethod
    def generate_stream(self, prompt: str) -> Generator[str, None, None]:
        ...
