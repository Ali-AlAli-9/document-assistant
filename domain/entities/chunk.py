from dataclasses import dataclass, field

@dataclass
class Chunk:
    id: str
    document_id: int
    content: str
    index: int
    embedding: list[float] | None = None
    metadata: dict = field(default_factory=dict)