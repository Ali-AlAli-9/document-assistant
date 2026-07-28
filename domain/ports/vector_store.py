from abc import ABC, abstractmethod
from dataclasses import dataclass
from domain.entities.chunk import Chunk

@dataclass
class SearchResult:
    content: str
    score: float
    metadata: dict

class VectorStorePort(ABC):
    @abstractmethod
    def add(self, chunks: list[Chunk]) -> None:
        ...

    @abstractmethod
    def search(self, embedding: list[float], top_k: int = 5, where: dict | None = None) -> list[SearchResult]:
        ...

    @abstractmethod
    def delete_by_document(self, doc_id: int) -> None:
        ...