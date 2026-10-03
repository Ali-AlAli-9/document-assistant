import logging
import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Callable

from domain.entities.conversation import Conversation
from domain.ports.conversation_repository import ConversationRepositoryPort

logger = logging.getLogger(__name__)


class InMemoryConversationRepository(ConversationRepositoryPort):
    """Thread-safe in-memory conversation store, keyed by scope (session).

    Isolation: each scope has its own bucket — nothing is visible across scopes.
    Retention: per-scope bounded LRU (``max_per_scope``) plus idle TTL
    (``ttl_minutes``) so abandoned sessions release memory automatically.
    """

    def __init__(self, max_per_scope: int = 10, ttl_minutes: int = 30):
        self._max_per_scope = max(1, max_per_scope)
        self._ttl = timedelta(minutes=max(1, ttl_minutes))
        self._data: dict[str, OrderedDict[str, Conversation]] = {}
        self._lock = threading.Lock()

    def get_or_create(
        self,
        scope: str,
        conversation_id: str,
        factory: Callable[[], Conversation] | None = None,
    ) -> Conversation:
        scope = scope or 'default'
        with self._lock:
            self._prune_unlocked()
            bucket = self._data.setdefault(scope, OrderedDict())
            conversation = bucket.get(conversation_id)
            if conversation is None:
                if len(bucket) >= self._max_per_scope:
                    self._evict_oldest(bucket)
                if factory is not None:
                    conversation = factory()
                else:
                    conversation = Conversation(id=conversation_id)
                # The store owns conversation identity: it must match the key.
                conversation.id = conversation_id
                bucket[conversation_id] = conversation
                logger.debug('Created conversation %s in scope %s', conversation_id, scope)
            conversation.last_accessed_at = datetime.now(timezone.utc)
            bucket.move_to_end(conversation_id)
            return conversation

    def get_all(self, scope: str) -> list[Conversation]:
        with self._lock:
            bucket = self._data.get(scope or 'default')
            return list(bucket.values()) if bucket else []

    def clear_scope(self, scope: str) -> None:
        with self._lock:
            self._data.pop(scope or 'default', None)

    def clear_all(self) -> None:
        with self._lock:
            self._data.clear()
        logger.info('All conversations cleared')

    def prune(self, now=None) -> int:
        now = now or datetime.now(timezone.utc)
        with self._lock:
            return self._prune_unlocked(now)

    # -- internals (always called under self._lock) -----------------------

    def _prune_unlocked(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        removed = 0
        empty_scopes: list[str] = []
        for scope, bucket in self._data.items():
            stale_ids = [
                conv_id
                for conv_id, conv in bucket.items()
                if now - conv.last_accessed_at > self._ttl
            ]
            for conv_id in stale_ids:
                bucket.pop(conv_id, None)
            removed += len(stale_ids)
            if not bucket:
                empty_scopes.append(scope)
        for scope in empty_scopes:
            self._data.pop(scope, None)
        if removed:
            logger.info('Pruned %d idle conversation(s)', removed)
        return removed

    def _evict_oldest(self, bucket: OrderedDict) -> None:
        try:
            oldest_id, _ = next(iter(bucket.items()))
        except StopIteration:
            return
        bucket.pop(oldest_id, None)