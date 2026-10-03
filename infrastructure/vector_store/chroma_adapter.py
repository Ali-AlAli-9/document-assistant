import chromadb
import logging
import threading
from domain.ports.vector_store import VectorStorePort, SearchResult
from domain.entities.chunk import Chunk

logger = logging.getLogger(__name__)

class ChromaAdapter(VectorStorePort):
    def __init__(self, persist_dir: str, collection_name: str):
        self._persist_dir = persist_dir
        self._collection_name = collection_name
        self._reset_lock = threading.Lock()
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._collection = self._get_or_create()
        logger.info(f'ChromaAdapter ready: {collection_name} at {persist_dir}')

    def _get_or_create(self):
        return self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={'hnsw:space': 'cosine'},
        )

    def _reset(self):
        with self._reset_lock:
            logger.warning('Resetting ChromaDB collection due to corruption')
            try:
                existing = self._collection.get()
                self._client.delete_collection(self._collection_name)
                self._collection = self._get_or_create()
                if existing and existing['ids']:
                    try:
                        self._collection.add(
                            ids=existing['ids'],
                            documents=existing['documents'],
                            embeddings=existing['embeddings'],
                            metadatas=existing['metadatas'],
                        )
                        logger.info(f'Restored {len(existing["ids"])} chunks after reset')
                    except Exception:
                        logger.exception('Failed to restore chunks after reset, data lost')
            except Exception:
                logger.exception('Failed during reset, recreating empty collection')
                try:
                    self._client.delete_collection(self._collection_name)
                except Exception:
                    pass
                self._collection = self._get_or_create()

    def add(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        try:
            self._collection.add(
                ids=[c.id for c in chunks],
                documents=[c.content for c in chunks],
                embeddings=[c.embedding for c in chunks],
                metadatas=[{'doc_id': str(c.document_id), 'index': c.index} | c.metadata for c in chunks],
            )
        except chromadb.errors.InternalError:
            logger.exception('ChromaDB corruption during add, resetting')
            self._reset()
            self._collection.add(
                ids=[c.id for c in chunks],
                documents=[c.content for c in chunks],
                embeddings=[c.embedding for c in chunks],
                metadatas=[{'doc_id': str(c.document_id), 'index': c.index} | c.metadata for c in chunks],
            )

    def search(self, embedding: list[float], top_k: int = 5, where: dict | None = None) -> list[SearchResult]:
        query_params: dict = {
            'query_embeddings': [embedding],
            'n_results': top_k,
        }
        if where:
            query_params['where'] = where
        try:
            results = self._collection.query(**query_params)
        except chromadb.errors.InternalError:
            logger.exception('ChromaDB search failed due to corruption')
            return []
        if not results['documents'] or not results['documents'][0]:
            return []
        return [
            SearchResult(content=doc, score=1 - dist, metadata=meta or {})
            for doc, meta, dist in zip(
                results['documents'][0],
                results['metadatas'][0],
                results['distances'][0],
            )
        ]

    def delete_by_document(self, doc_id: int) -> None:
        try:
            self._collection.delete(where={'doc_id': str(doc_id)})
        except chromadb.errors.InternalError:
            logger.warning('ChromaDB corruption during delete, resetting')
            self._reset()

    def fetch_chunks(self, doc_id: int, indices: list[int]) -> list[SearchResult]:
        """Fetch stored chunks for ``doc_id`` at the given indices (content only)."""
        unique = sorted({i for i in indices if isinstance(i, int)})
        if not unique:
            return []
        try:
            # Chroma's ``where`` accepts ONE clause per dict, so the two
            # conditions (document + index set) must be combined explicitly
            # under ``$and``; passing both keys raises "Expected where to have
            # exactly one operator".
            got = self._collection.get(
                where={'$and': [{'doc_id': str(doc_id)}, {'index': {'$in': unique}}]},
                include=['documents', 'metadatas'],
            )
        except chromadb.errors.InternalError:
            logger.exception('ChromaDB corruption during fetch_chunks')
            return []
        return [
            SearchResult(content=doc_txt, score=0.0, metadata=meta or {})
            for doc_txt, meta in zip(got['documents'], got['metadatas'])
        ]

    def delete_all(self) -> None:
        with self._reset_lock:
            logger.info('Clearing all ChromaDB data')
            try:
                self._client.delete_collection(self._collection_name)
            except Exception:
                logger.warning('Failed to delete ChromaDB collection')
            self._collection = self._get_or_create()

    def count_by_document(self, doc_id: int) -> int:
        """How many chunks the index holds for ``doc_id``.

        ``include=[]`` asks Chroma for ids only — this is a consistency check
        over potentially every chunk of a document, and pulling the documents
        and embeddings back would be pure waste.
        """
        try:
            got = self._collection.get(
                where={'doc_id': str(doc_id)},
                include=[],
            )
        except chromadb.errors.InternalError:
            logger.exception('ChromaDB corruption during count_by_document')
            return 0
        return len(got.get('ids') or [])
