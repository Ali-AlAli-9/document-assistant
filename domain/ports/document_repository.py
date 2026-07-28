from abc import ABC, abstractmethod
from domain.entities.document import Document

class DocumentRepositoryPort(ABC):
    @abstractmethod
    def save(self, document: Document) -> Document:
        ...

    @abstractmethod
    def get_by_id(self, doc_id: int) -> Document | None:
        ...

    @abstractmethod
    def get_all(self) -> list[Document]:
        ...

    @abstractmethod
    def delete(self, doc_id: int) -> None:
        ...