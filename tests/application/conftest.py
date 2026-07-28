from unittest.mock import MagicMock
import pytest

from domain.ports.vector_store import VectorStorePort, SearchResult
from domain.ports.embedding import EmbeddingPort
from domain.ports.llm import LLMPort
from domain.ports.document_repository import DocumentRepositoryPort


@pytest.fixture
def mock_repo():
    m = MagicMock(spec=DocumentRepositoryPort)
    m.save.side_effect = lambda doc: doc
    return m


@pytest.fixture
def mock_vector_store():
    m = MagicMock(spec=VectorStorePort)
    return m


@pytest.fixture
def mock_embedder():
    m = MagicMock(spec=EmbeddingPort)
    m.encode.return_value = [[0.1, 0.2, 0.3]]
    m.encode_query.return_value = [0.1, 0.2, 0.3]
    m.dimension = 3
    return m


@pytest.fixture
def mock_llm():
    m = MagicMock(spec=LLMPort)
    m.generate.return_value = 'Test answer'
    m.generate_stream.return_value = iter(['chunk1', 'chunk2'])
    return m


@pytest.fixture
def search_results():
    return [
        SearchResult(
            content='Paris is the capital of France.',
            score=0.92,
            metadata={'doc_id': '1', 'source': 'geography.pdf', 'chunk_index': '0'},
        ),
        SearchResult(
            content='France is a country in Europe.',
            score=0.85,
            metadata={'doc_id': '1', 'source': 'geography.pdf', 'chunk_index': '1'},
        ),
    ]
