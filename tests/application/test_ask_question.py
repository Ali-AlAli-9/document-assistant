import pytest
from application.commands.ask_question import AskQuestionCommand, AskQuestionHandler, StreamChunk
from domain.ports.vector_store import SearchResult


class TestAskQuestionHandler:

    def test_ask_with_results(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        result = handler.handle(AskQuestionCommand(question='What is the capital of France?'))

        assert result.answer == 'Test answer'
        assert len(result.sources) == 2
        assert result.sources[0]['content'] == 'Paris is the capital of France.'
        assert result.sources[0]['score'] == 0.92
        assert result.sources[0]['document_id'] == '1'
        assert result.sources[0]['title'] == 'geography.pdf'
        assert result.sources[1]['title'] == 'geography.pdf'
        assert result.sources[1]['score'] == 0.85

    def test_ask_no_results(self, mock_vector_store, mock_embedder, mock_llm):
        mock_vector_store.search.return_value = []
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        result = handler.handle(AskQuestionCommand(question='Unknown topic'))

        assert result.answer == 'Test answer'
        assert len(result.sources) == 0
        prompt_arg = mock_llm.generate.call_args[0][0]
        assert 'No relevant information found' in prompt_arg
        assert 'not enough information' in prompt_arg

    def test_ask_with_history(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        handler.handle(AskQuestionCommand(question='first question', conversation_id='conv-1'))
        result = handler.handle(AskQuestionCommand(question='second question', conversation_id='conv-1'))

        assert result.answer == 'Test answer'
        assert result.conversation_id == 'conv-1'
        prompt_arg = mock_llm.generate.call_args[0][0]
        assert 'first question' in prompt_arg
        assert 'second question' in prompt_arg

    def test_ask_creates_new_conversation(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        result = handler.handle(AskQuestionCommand(question='hello', conversation_id='new-conv'))

        assert result.conversation_id == 'new-conv'
        conversations = handler.conversation_repo.get_all('default')
        assert any(c.id == 'new-conv' for c in conversations)

    def test_ask_appends_to_existing_conversation(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        handler.handle(AskQuestionCommand(question='q1', conversation_id='c1'))
        handler.handle(AskQuestionCommand(question='q2', conversation_id='c1'))

        conversation = next(
            c for c in handler.conversation_repo.get_all('default') if c.id == 'c1'
        )
        assert len(conversation.messages) == 4

    def test_build_context(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        context = handler._build_context(search_results)

        assert '[Source 1]' in context
        assert '[Source 2]' in context
        assert 'Paris is the capital' in context
        assert 'France is a country' in context

    def test_build_context_empty(self, mock_vector_store, mock_embedder, mock_llm):
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        context = handler._build_context([])

        assert context == ''

    def test_build_prompt_with_context(self, mock_vector_store, mock_embedder, mock_llm):
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        prompt = handler._build_prompt('Question?', 'Context data', 'History text')

        assert 'Question?' in prompt
        assert 'Context data' in prompt
        assert 'History text' in prompt
        assert 'Answer based only' in prompt

    def test_build_prompt_no_context(self, mock_vector_store, mock_embedder, mock_llm):
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        prompt = handler._build_prompt('Question?', '', '')

        assert 'No relevant information found' in prompt
        assert 'not enough information' in prompt

    def test_build_prompt_with_threshold_filtering(self, mock_vector_store, mock_embedder, mock_llm, monkeypatch):
        low_score = [type('SearchResult', (), {'content': 'low', 'score': 0.1, 'metadata': {}})()]
        high_score = [type('SearchResult', (), {'content': 'high', 'score': 0.9, 'metadata': {}})()]
        mock_vector_store.search.return_value = low_score + high_score
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        monkeypatch.setattr('core.config.settings.RETRIEVAL_SCORE_THRESHOLD', 0.3)

        handler.handle(AskQuestionCommand(question='test'))

        prompt_arg = mock_llm.generate.call_args[0][0]
        assert 'low' not in prompt_arg
        assert 'high' in prompt_arg

    def test_stream_yields_chunks(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        mock_llm.generate_stream.return_value = iter(['token1', 'token2', 'token3'])
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        chunks = list(handler.handle_stream_sync(AskQuestionCommand(question='test')))

        content_chunks = [c for c in chunks if c.content is not None]
        assert len(content_chunks) == 3
        assert content_chunks[0].content == 'token1'
        assert content_chunks[1].content == 'token2'
        assert content_chunks[2].content == 'token3'
        assert any(c.done for c in chunks)

    def test_stream_empty(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        mock_llm.generate_stream.return_value = iter([])
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        chunks = list(handler.handle_stream_sync(AskQuestionCommand(question='test')))

        content_chunks = [c for c in chunks if c.content is not None]
        assert len(content_chunks) == 0
        assert any(c.done for c in chunks)

    def test_stream_emits_conversation_id_first(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        mock_llm.generate_stream.return_value = iter(['token1'])
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        chunks = list(handler.handle_stream_sync(
            AskQuestionCommand(question='test', conversation_id='conv-9', session_key='sess-1')
        ))

        assert chunks[0].conversation_id == 'conv-9'
        assert chunks[0].content is None
        assert chunks[0].sources is None
        assert chunks[0].done is False
        assert sum(1 for c in chunks if c.conversation_id) == 1
        conversations = handler.conversation_repo.get_all('sess-1')
        assert any(c.id == 'conv-9' for c in conversations)

    def test_conversations_are_isolated_per_session(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        handler.handle(AskQuestionCommand(question='q for session A', conversation_id='conv-1', session_key='session-a'))
        handler.handle(AskQuestionCommand(question='q for session B', conversation_id='conv-1', session_key='session-b'))

        session_a = handler.conversation_repo.get_all('session-a')
        session_b = handler.conversation_repo.get_all('session-b')
        assert any(c.id == 'conv-1' and len(c.messages) == 2 for c in session_a)
        assert any(c.id == 'conv-1' and len(c.messages) == 2 for c in session_b)

        # The same conversation id under session-a must NOT leak into session-b.
        conv_b = next(c for c in session_b if c.id == 'conv-1')
        assert all('A' not in m.content for m in conv_b.messages)

    def test_ask_with_sources_in_metadata(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        result = handler.handle(AskQuestionCommand(question='What is the capital of France?'))

        sources = result.sources
        assert len(sources) == 2
        assert sources[0]['document_id'] == '1'
        assert sources[0]['title'] == 'geography.pdf'
        assert sources[0]['content'] == 'Paris is the capital of France.'
        assert sources[0]['score'] == 0.92
        assert sources[1]['document_id'] == '1'
        assert sources[1]['title'] == 'geography.pdf'
        assert sources[1]['content'] == 'France is a country in Europe.'
        assert sources[1]['score'] == 0.85

    def test_sources_payload_contains_only_public_keys(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        result = handler.handle(AskQuestionCommand(question='What is the capital of France?'))

        for source in result.sources:
            assert set(source.keys()) == {'content', 'score', 'document_id', 'title'}

    def test_stream_sources_payload_contains_only_public_keys(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        mock_llm.generate_stream.return_value = iter(['token'])
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        chunks = list(handler.handle_stream_sync(AskQuestionCommand(question='test')))
        sources_event = next(c for c in chunks if c.sources is not None)

        assert len(sources_event.sources) == 2
        for source in sources_event.sources:
            assert set(source.keys()) == {'content', 'score', 'document_id', 'title'}

    def test_sources_are_truncated_but_context_is_full(self, mock_vector_store, mock_embedder, mock_llm, monkeypatch):
        long_result = SearchResult(
            content='A' * 1000,
            score=0.9,
            metadata={'doc_id': '1', 'source': 'big.pdf', 'chunk_index': '0'},
        )
        mock_vector_store.search.return_value = [long_result]
        monkeypatch.setattr('core.config.settings.SOURCE_SNIPPET_MAX_CHARS', 20)
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        result = handler.handle(AskQuestionCommand(question='test'))

        snippet = result.sources[0]['content']
        assert len(snippet) <= 21
        assert snippet.endswith('…')
        assert snippet.startswith('A' * 20)
        # the full chunk still reaches the LLM context
        prompt_arg = mock_llm.generate.call_args[0][0]
        assert 'A' * 1000 in prompt_arg

    def test_sources_stored_in_conversation_are_public_only(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        handler.handle(AskQuestionCommand(question='q1', conversation_id='c1', session_key='s1'))

        conversation = next(
            c for c in handler.conversation_repo.get_all('s1') if c.id == 'c1'
        )
        assistant_msg = next(m for m in conversation.messages if m.role == 'assistant')
        assert assistant_msg.sources is not None
        for source in assistant_msg.sources:
            assert set(source.keys()) == {'content', 'score', 'document_id', 'title'}
