from domain.entities.chunk import Chunk


def test_chunk_full():
    chunk = Chunk(
        id='doc_1_chunk_0',
        document_id=1,
        content='hello world',
        index=0,
        embedding=[0.1, 0.2, 0.3],
        metadata={'source': 'test.pdf'},
    )
    assert chunk.id == 'doc_1_chunk_0'
    assert chunk.document_id == 1
    assert chunk.content == 'hello world'
    assert chunk.index == 0
    assert chunk.embedding == [0.1, 0.2, 0.3]
    assert chunk.metadata == {'source': 'test.pdf'}


def test_chunk_minimal():
    chunk = Chunk(id='doc_2_chunk_0', document_id=2, content='minimal', index=0)
    assert chunk.embedding is None
    assert chunk.metadata == {}


def test_chunk_str():
    chunk = Chunk(id='doc_1_chunk_0', document_id=1, content='data', index=0)
    assert chunk.id == 'doc_1_chunk_0'
    assert chunk.document_id == 1
