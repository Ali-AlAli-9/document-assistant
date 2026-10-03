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
    def fetch_chunks(self, doc_id: int, indices: list[int]) -> list[SearchResult]:
        """Fetch stored chunks for ``doc_id`` at the given chunk indices.

        Used by retrieval to expand a matched section heading into its body:
        content and metadata only, score is undefined (0.0).
        """

    @abstractmethod
    def delete_by_document(self, doc_id: int) -> None:
        ...

    @abstractmethod
    def delete_all(self) -> None:
        """Wipe the whole collection (demo reset). Not scoped to an owner."""

    @abstractmethod
    def count_by_document(self, doc_id: int) -> int:
        """How many chunks are stored for ``doc_id``.

        Used by the consistency checker to answer "does the index match what the
        database claims?" — the question a ``chunk_count`` column can never
        answer about itself.
        """