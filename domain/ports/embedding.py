from abc import ABC, abstractmethod

class EmbeddingPort(ABC):
    @abstractmethod
    def encode(self, texts: list[str]) -> list[list[float]]:
        ...

    @abstractmethod
    def encode_query(self, text: str) -> list[float]:
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        ...