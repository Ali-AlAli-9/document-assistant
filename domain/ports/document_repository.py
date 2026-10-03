from abc import ABC, abstractmethod
from domain.entities.document import Document

class DocumentRepositoryPort(ABC):
    """Every read/write takes the account that owns the row.

    ``owner_id=None`` means UNSCOPED — all documents — and exists only for
    internal maintenance (cleanup, tests). Any path reachable by a user MUST
    pass their id; ``tests/api/test_document_tenant_isolation.py`` pins that.
    """

    @abstractmethod
    def save(self, document: Document, owner_id: int | None = None) -> Document:
        ...

    @abstractmethod
    def get_by_id(self, doc_id: int, owner_id: int | None = None) -> Document | None:
        ...

    @abstractmethod
    def get_all(self, owner_id: int | None = None) -> list[Document]:
        ...

    @abstractmethod
    def delete(self, doc_id: int, owner_id: int | None = None) -> None:
        ...