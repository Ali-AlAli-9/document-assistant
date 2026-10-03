"""ChromaAdapter.fetch_chunks against a real ChromaDB collection.

This is the one adapter method whose correctness depends on Chroma query
syntax rather than on our own logic, so it is exercised against a real
collection instead of a mock. A mocked vector store happily accepts
``where={'doc_id': ..., 'index': {'$in': [...]}}``; Chroma rejects it with
"Expected where to have exactly one operator", which broke section expansion
at runtime while every unit test stayed green.
"""

import pytest

from domain.entities.chunk import Chunk
from infrastructure.vector_store.chroma_adapter import ChromaAdapter


def _chunks(doc_id: int, count: int) -> list[Chunk]:
    return [
        Chunk(
            id=f'{doc_id}-{i}',
            document_id=doc_id,
            content=f'doc {doc_id} chunk {i}',
            index=i,
            embedding=[float(i), 1.0, 0.0],
            metadata={'source': 'final.pdf'},
        )
        for i in range(count)
    ]


@pytest.fixture
def adapter(tmp_path):
    return ChromaAdapter(persist_dir=str(tmp_path / 'chroma'), collection_name='fetch_chunks_test')


@pytest.fixture
def store(adapter):
    adapter.add(_chunks(doc_id=7, count=5))
    adapter.add(_chunks(doc_id=8, count=3))
    return adapter


def test_fetches_only_requested_indices(store):
    results = store.fetch_chunks(7, [1, 3])

    assert sorted(r.metadata['index'] for r in results) == [1, 3]


def test_never_leaks_another_documents_chunks(store):
    # doc 7 has indices 0-4 and doc 8 has 0-2; overlapping index numbers must
    # not cross the document boundary.
    results = store.fetch_chunks(7, [0, 1, 2])

    assert len(results) == 3
    assert {r.metadata['doc_id'] for r in results} == {'7'}
    assert all(r.content.startswith('doc 7') for r in results)


def test_returns_content_and_metadata_without_a_score(store):
    results = store.fetch_chunks(7, [2])

    assert len(results) == 1
    result = results[0]
    assert result.content == 'doc 7 chunk 2'
    assert result.score == 0.0
    assert result.metadata['source'] == 'final.pdf'


def test_unknown_indices_are_skipped_not_raised(store):
    assert store.fetch_chunks(7, [99]) == []


def test_unknown_document_returns_empty(store):
    assert store.fetch_chunks(4242, [0, 1]) == []


def test_empty_index_list_short_circuits(store):
    assert store.fetch_chunks(7, []) == []


def test_duplicate_indices_are_returned_once(store):
    results = store.fetch_chunks(7, [2, 2, 2])

    assert len(results) == 1


def test_non_integer_indices_are_ignored(store):
    assert store.fetch_chunks(7, ['not-an-index']) == []