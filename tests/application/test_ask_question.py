import pytest
from application.commands.ask_question import AskQuestionCommand, AskQuestionHandler, StreamChunk


class TestAskQuestionHandler:

    def test_ask_with_results(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        result = handler.handle(AskQuestionCommand(question='What is the capital of France?'))

        assert result.answer == 'Test answer'
        assert len(result.sources) == 2
        assert result.sources[0]['doc_id'] == '1'
        assert result.sources[0]['source'] == 'geography.pdf'
        assert result.sources[1]['source'] == 'geography.pdf'
        assert result.sources[1]['chunk_index'] == '1'

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
        assert 'new-conv' in handler.conversations

    def test_ask_appends_to_existing_conversation(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        handler.handle(AskQuestionCommand(question='q1', conversation_id='c1'))
        handler.handle(AskQuestionCommand(question='q2', conversation_id='c1'))

        assert len(handler.conversations['c1'].messages) == 4

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

    def test_ask_with_sources_in_metadata(self, mock_vector_store, mock_embedder, mock_llm, search_results):
        mock_vector_store.search.return_value = search_results
        handler = AskQuestionHandler(mock_vector_store, mock_embedder, mock_llm)

        result = handler.handle(AskQuestionCommand(question='What is the capital of France?'))

        sources = result.sources
        assert len(sources) == 2
        assert sources[0]['doc_id'] == '1'
        assert sources[0]['source'] == 'geography.pdf'
        assert sources[0]['chunk_index'] == '0'
        assert sources[1]['doc_id'] == '1'
