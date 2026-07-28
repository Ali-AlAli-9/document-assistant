from domain.entities.conversation import Conversation, Message


def test_conversation_defaults():
    c = Conversation(id='test-123')
    assert c.id == 'test-123'
    assert c.user_id is None
    assert c.messages == []


def test_conversation_with_user():
    c = Conversation(id='test-456', user_id='user-1')
    assert c.id == 'test-456'
    assert c.user_id == 'user-1'


def test_add_message():
    c = Conversation(id='c1')
    c.add_message('user', 'hello')
    assert len(c.messages) == 1
    assert c.messages[0].role == 'user'
    assert c.messages[0].content == 'hello'
    assert c.messages[0].sources is None


def test_add_message_with_sources():
    c = Conversation(id='c1')
    sources = [{'doc_id': '1', 'score': 0.95}]
    c.add_message('assistant', 'answer', sources)
    assert len(c.messages) == 1
    assert c.messages[0].role == 'assistant'
    assert c.messages[0].sources == sources


def test_history():
    c = Conversation(id='c1')
    c.add_message('user', 'first question')
    c.add_message('assistant', 'first answer')
    history = c.history
    assert 'User: first question' in history
    assert 'Assistant: first answer' in history


def test_history_only_latest_6():
    c = Conversation(id='c1')
    for i in range(10):
        c.add_message('user', f'q{i}')
        c.add_message('assistant', f'a{i}')
    history = c.history
    assert 'q0' not in history
    assert 'q5' not in history
    assert 'q7' in history
    assert history.count('User:') == 3


def test_message_defaults():
    m = Message(role='user', content='hello')
    assert m.role == 'user'
    assert m.content == 'hello'
    assert m.sources is None
    assert hasattr(m, 'created_at')


def test_empty_conversation_history():
    c = Conversation(id='c1')
    assert c.history == ''
