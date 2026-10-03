from datetime import datetime, timedelta, timezone

import pytest

from domain.entities.conversation import Conversation
from infrastructure.repository.in_memory_conversation_repo import InMemoryConversationRepository


@pytest.fixture
def repo():
    return InMemoryConversationRepository(max_per_scope=2, ttl_minutes=30)


def test_get_or_create_creates(repo):
    conv = repo.get_or_create('s1', 'conv-1')
    assert isinstance(conv, Conversation)
    assert conv.id == 'conv-1'
    assert len(repo.get_all('s1')) == 1


def test_get_or_create_reuses_existing(repo):
    first = repo.get_or_create('s1', 'conv-1')
    second = repo.get_or_create('s1', 'conv-1')
    assert first is second
    assert len(repo.get_or_create('s1', 'conv-1').messages) == 0
    first.add_message('user', 'hello')
    assert repo.get_or_create('s1', 'conv-1').messages[0].content == 'hello'


def test_factory_is_used_for_new_conversations(repo):
    created = []

    def factory():
        conv = Conversation(id='x', max_history=3)
        created.append(conv)
        return conv

    conv = repo.get_or_create('s1', 'conv-1', factory=factory)
    assert conv.id == 'conv-1'
    assert conv.max_history == 3
    assert len(created) == 1

    # existing conversation must not be re-factored
    repo.get_or_create('s1', 'conv-1', factory=factory)
    assert len(created) == 1


def test_scopes_are_isolated(repo):
    repo.get_or_create('session-a', 'conv-1')
    repo.get_or_create('session-b', 'conv-1')
    assert len(repo.get_all('session-a')) == 1
    assert len(repo.get_all('session-b')) == 1


def test_lru_eviction_per_scope(repo):
    repo.get_or_create('s1', 'a')
    repo.get_or_create('s1', 'b')
    # bucket is full -> oldest ('a') is evicted for 'c'
    repo.get_or_create('s1', 'c')
    assert 'a' not in [c.id for c in repo.get_all('s1')]
    assert {c.id for c in repo.get_all('s1')} == {'b', 'c'}


def test_lru_eviction_respects_recent_access(repo):
    repo.get_or_create('s1', 'a')
    repo.get_or_create('s1', 'b')
    repo.get_or_create('s1', 'a')  # touch 'a' -> 'b' becomes oldest
    repo.get_or_create('s1', 'c')
    assert {c.id for c in repo.get_all('s1')} == {'a', 'c'}


def test_prune_removes_idle_conversations(repo):
    now = datetime.now(timezone.utc)
    conv_a = repo.get_or_create('s1', 'a')
    conv_b = repo.get_or_create('s1', 'b')
    conv_a.last_accessed_at = now - timedelta(minutes=31)
    conv_b.last_accessed_at = now - timedelta(minutes=5)

    removed = repo.prune(now=now)

    assert removed == 1
    assert 'a' not in [c.id for c in repo.get_all('s1')]
    assert 'b' in [c.id for c in repo.get_all('s1')]


def test_prune_drops_empty_scopes(repo):
    now = datetime.now(timezone.utc)
    conv = repo.get_or_create('s1', 'a')
    conv.last_accessed_at = now - timedelta(minutes=31)
    repo.prune(now=now)
    assert repo.get_all('s1') == []


def test_clear_scope(repo):
    repo.get_or_create('s1', 'a')
    repo.get_or_create('s2', 'a')
    repo.clear_scope('s1')
    assert repo.get_all('s1') == []
    assert len(repo.get_all('s2')) == 1


def test_clear_all(repo):
    repo.get_or_create('s1', 'a')
    repo.get_or_create('s2', 'b')
    repo.clear_all()
    assert repo.get_all('s1') == []
    assert repo.get_all('s2') == []