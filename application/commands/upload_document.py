import logging
import unicodedata
from dataclasses import dataclass
from domain.entities.document import Document
from domain.entities.chunk import Chunk
from domain.ports.document_repository import DocumentRepositoryPort
from domain.ports.vector_store import VectorStorePort
from domain.ports.embedding import EmbeddingPort
from core.exceptions import IngestionError, FileNotAllowedError
from core.config import settings

logger = logging.getLogger(__name__)

@dataclass
class UploadDocumentCommand:
    title: str
    file_path: str
    file_size: int

class UploadDocumentHandler:
    def __init__(
        self,
        repo: DocumentRepositoryPort,
        vector_store: VectorStorePort,
        embedder: EmbeddingPort,
    ):
        self.repo = repo
        self.vector_store = vector_store
        self.embedder = embedder

    def handle(self, command: UploadDocumentCommand) -> Document:
        ext = command.title[command.title.rfind('.'):].lower()
        if ext not in settings.ALLOWED_EXTENSIONS:
            raise FileNotAllowedError(f'File type {ext} not allowed')

        document = Document(
            title=command.title,
            file_path=command.file_path,
            file_size=command.file_size,
        )
        document = self.repo.save(document)
        return self._process(document)

    def handle_by_id(self, doc_id: int) -> Document:
        document = self.repo.get_by_id(doc_id)
        if not document:
            raise ValueError(f'Document {doc_id} not found')
        return self._process(document)

    def retry_by_id(self, doc_id: int) -> Document:
        document = self.repo.get_by_id(doc_id)
        if not document:
            raise ValueError(f'Document {doc_id} not found')
        self.vector_store.delete_by_document(doc_id)
        return self._process(document)

    def _process(self, document: Document) -> Document:
        try:
            document.mark_processing()
            document = self.repo.save(document)

            text = self._extract_text(document.file_path)
            chunks = self._chunk_text(text)

            embeddings = self.embedder.encode(chunks)
            chunk_entities = [
                Chunk(
                    id=f'doc_{document.id}_chunk_{i}',
                    document_id=document.id,
                    content=chunk,
                    index=i,
                    embedding=embeddings[i],
                    metadata={
                        'doc_id': str(document.id),
                        'source': document.title,
                        'chunk_index': i,
                    },
                )
                for i, chunk in enumerate(chunks)
            ]
            self.vector_store.add(chunk_entities)

            document.mark_ready(len(chunks))
            return self.repo.save(document)

        except Exception as e:
            logger.exception(f'Ingestion failed for doc {document.id}')
            document.mark_failed()
            self.repo.save(document)
            raise IngestionError(str(e)) from e

    def _extract_text(self, file_path: str) -> str:
        ext = file_path[file_path.rfind('.'):].lower()
        if ext == '.pdf':
            from langchain_community.document_loaders import PyPDFLoader
            pages = PyPDFLoader(file_path).load()
            text = '\n'.join(p.page_content for p in pages)
        elif ext == '.txt':
            with open(file_path, encoding='utf-8', errors='replace') as f:
                text = f.read()
        elif ext == '.docx':
            from langchain_community.document_loaders import Docx2txtLoader
            pages = Docx2txtLoader(file_path).load()
            text = '\n'.join(p.page_content for p in pages)
        else:
            raise IngestionError(f'Unsupported file type: {ext}')
        return unicodedata.normalize('NFKC', text)

    def _chunk_text(self, text: str) -> list[str]:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
        )
        return splitter.split_text(text) if text else []