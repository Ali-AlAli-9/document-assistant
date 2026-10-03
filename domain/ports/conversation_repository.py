from abc import ABC, abstractmethod
from typing import Callable

from domain.entities.conversation import Conversation


class ConversationRepositoryPort(ABC):
    """Storage contract for in-memory conversations.

    Conversations are stored per ``scope`` (e.g. a Django session key) so that
    each scope is fully isolated. Implementations decide retention policy
    (LRU cap, idle TTL, cleanup on session end).
    """

    @abstractmethod
    def get_or_create(
        self,
        scope: str,
        conversation_id: str,
        factory: Callable[[], Conversation] | None = None,
    ) -> Conversation:
        """Return the conversation if it exists in ``scope``, else create it.

        ``factory`` builds the Conversation when missing (may apply settings).
        Updates the activity timestamp (used for idle eviction).
        """
        ...

    @abstractmethod
    def get_all(self, scope: str) -> list[Conversation]:
        """Return all conversations under ``scope``."""
        ...

    @abstractmethod
    def clear_scope(self, scope: str) -> None:
        """Delete every conversation under ``scope`` (e.g. on logout)."""
        ...

    @abstractmethod
    def clear_all(self) -> None:
        """Delete all conversations (e.g. on server shutdown)."""
        ...

    @abstractmethod
    def prune(self, now=None) -> int:
        """Evict idle conversations (past TTL). Returns number removed."""
        ...