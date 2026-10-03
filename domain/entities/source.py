from dataclasses import dataclass


@dataclass
class Source:
    """A public, user-facing document source shown in the chat UI.

    Built explicitly from internal ``SearchResult`` data so internal metadata
    keys (``doc_id``, ``chunk_index``, ``index``, ``user_id``, ...) can never
    leak to the client. ``content`` carries a truncated snippet of the chunk;
    the full chunk text stays in the LLM context.
    """

    content: str
    score: float
    document_id: str
    title: str | None = None

    def to_dict(self) -> dict:
        """Serialize to the exact public payload shape."""

        return {
            'content': self.content,
            'score': self.score,
            'document_id': self.document_id,
            'title': self.title,
        }