from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

class DocumentStatus(Enum):
    PENDING = 'pending'
    PROCESSING = 'processing'
    READY = 'ready'
    FAILED = 'failed'

@dataclass
class Document:
    id: int | None = None
    title: str = ''
    file_path: str = ''
    status: DocumentStatus = DocumentStatus.PENDING
    chunk_count: int = 0
    file_size: int = 0
    uploaded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def mark_processing(self) -> None:
        self.status = DocumentStatus.PROCESSING

    def mark_ready(self, chunk_count: int) -> None:
        self.status = DocumentStatus.READY
        self.chunk_count = chunk_count

    def mark_failed(self) -> None:
        self.status = DocumentStatus.FAILED

    @property
    def is_processable(self) -> bool:
        return self.status in (DocumentStatus.PENDING, DocumentStatus.FAILED)