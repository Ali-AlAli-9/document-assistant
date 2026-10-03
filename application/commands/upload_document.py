import logging
import re
import unicodedata
from dataclasses import dataclass
from domain.entities.document import Document
from domain.entities.chunk import Chunk
from domain.ports.document_repository import DocumentRepositoryPort
from domain.ports.vector_store import VectorStorePort
from domain.ports.embedding import EmbeddingPort
from core.exceptions import IngestionError, FileNotAllowedError
from core.config import settings
from infrastructure.vector_store import writer_lock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Arabic PDF text-order repair
#
# Many Arabic PDFs (especially school textbooks) store glyphs in *visual*
# order: the whole logical line is mirrored, Arabic words are internally
# reversed, while Latin/digit runs keep their own internal order. pypdf does
# not reconstruct the logical order, so the extracted text reads reversed
# (letter by letter, left to right). We detect that case and rebuild the
# logical order per line: reverse the order of the directional runs and
# reverse each Arabic run's characters (as grapheme clusters, so diacritics
# stay attached to their base letter).
# ---------------------------------------------------------------------------

_ARABIC_CHAR_RE = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]')
_ARABIC_WORD_RE = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+')
_ARTIFACT_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\uf000-\uf8ff]')
# Table-of-contents dotted leaders ("1-5 أهداف المشروع .............."), with
# optional single spaces between the dots for PDFs that render leaders spaced.
_TOC_DOTTED_RE = re.compile(r'(?:\.\s*){3,}')
_BIDI_GLUE = '\u200c\u200d'  # ZWNJ / ZWJ: glue chars keep their base letter

# Common Arabic function words used as an auxiliary detection signal. A token
# that equals one of these reads normally; a token whose *reverse* is one of
# them is a reversed form (e.g. «في» -> «يف»), i.e. strong evidence the text
# was extracted in visual order.
_ARABIC_FUNCTION_WORDS = frozenset(
    'في من على إلى هذا عن أن كان ذلك عند بعد قبل كل بين ثم قد مع غير الذي التي'.split()
)
_REVERSED_MARK_MIN_HITS = 2


def _chars_with_clusters(s: str) -> list[str]:
    """Split into grapheme clusters so combining marks stay with their base."""
    clusters: list[str] = []
    for ch in s:
        if unicodedata.combining(ch) or ch in _BIDI_GLUE:
            if clusters:
                clusters[-1] += ch
            else:
                clusters.append(ch)
        else:
            clusters.append(ch)
    return clusters


def _reverse_visual_order_line(line: str) -> str:
    """Rebuild a visually-ordered (mirrored) line into logical Arabic order.

    Reverses the order of directional runs *and* reverses each Arabic run's
    characters. Non-Arabic runs (digits, Latin, punctuation) are kept as-is
    internally, so numbers like "2023" survive intact.
    """
    runs: list[tuple[bool, str]] = []
    i, n = 0, len(line)
    while i < n:
        j = i
        is_ar = _ARABIC_CHAR_RE.match(line[i]) is not None
        while j < n and (_ARABIC_CHAR_RE.match(line[j]) is not None) == is_ar:
            j += 1
        runs.append((is_ar, line[i:j]))
        i = j
    out: list[str] = []
    for is_ar, run in reversed(runs):
        if is_ar:
            out.append(''.join(reversed(_chars_with_clusters(run))))
        else:
            out.append(run)
    return ''.join(out)


def _looks_visually_reversed_arabic(text: str) -> bool:
    """Detect visual-order extraction on a (already normalized) Arabic text.

    Two independent signals, either one decisive:

    1. Reversed "ال" prefix. In normal Arabic many words *start* with «ال»;
       in visually-reversed text those same words *end* with «لا». Non-zero
       counts on the wrong side (with a small minimum for short documents)
       indicate mirrored text.
    2. Function words. In visual order «في/من/على/...» appear as «يف/نم/ىلع/...»
       i.e. tokens whose reverse is a common function word.
    """
    words = _ARABIC_WORD_RE.findall(text)
    la_ending = sum(1 for w in words if len(w) >= 3 and w.endswith('لا'))
    al_starting = sum(1 for w in words if len(w) >= 3 and w.startswith('ال'))
    if la_ending > al_starting and la_ending >= _REVERSED_MARK_MIN_HITS:
        return True

    reversed_hits = 0
    normal_hits = 0
    for word in words:
        if word in _ARABIC_FUNCTION_WORDS:
            normal_hits += 1
        elif word[::-1] in _ARABIC_FUNCTION_WORDS:
            reversed_hits += 1
    return reversed_hits > normal_hits and reversed_hits >= _REVERSED_MARK_MIN_HITS


def _normalize_pdf_arabic(text: str) -> str:
    """Clean pypdf artifacts and restore logical order for visual-order PDFs.

    Applied to PDF text only: .txt and .docx sources already store logical
    order. Pure Latin text is passed through untouched, as is normal-order
    Arabic (detection is conservative: only clearly-reversed text is rebuilt).
    """
    text = _ARTIFACT_RE.sub('', text).replace('\r', '')
    text = unicodedata.normalize('NFKC', text)
    if not _ARABIC_CHAR_RE.search(text):
        return text
    if not _looks_visually_reversed_arabic(text):
        return text
    return '\n'.join(
        _reverse_visual_order_line(ln) if _ARABIC_CHAR_RE.search(ln) else ln
        for ln in text.split('\n')
    )


def _is_toc_only_chunk(text: str) -> bool:
    """True when a chunk carries (only) table-of-contents lines and no prose.

    Arabic PDFs keep their فهرس pages: one dotted-leader line per section
    ("1-5 أهداف المشروع .............."). Those lines are *near-exact* matches
    for any question that reuses a section title, so they silently outrank the
    real section body and push the answer out of the top-K (measured: 37 of
    265 chunks were pure index lines, and the answer chunk fell from rank 3 to
    11-23 whenever an index line with the same wording existed). Dropping them
    at ingestion is strictly safe.

    Two patterns are rejected:

    1. Pure index chunks: EVERY non-empty line is a dotted-leader line.
    2. Mixed index pages: some فهرس pages are extracted as multi-column
       blocks with page markers ("VI", "78") interleaved, so not every line is
       a leader. They still carry no prose: when at least two lines are
       dotted-leader rows and those rows make up at least half of the chunk,
       the chunk is index material too. Requires >= 2 leaders so a single
       figure caption inside a real paragraph can never qualify.

    Prose (including math like "E = [e1, e2, ...]" and bibliography initials)
    always survives: a single leader line — or even a few — among dominant
    prose keeps the chunk.
    """
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    if not lines:
        return False
    dotted = sum(1 for ln in lines if _TOC_DOTTED_RE.search(ln))
    if dotted == len(lines):
        return True
    if len(lines) >= 2 and dotted >= 2 and dotted / len(lines) >= 0.5:
        return True
    return False


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
        with writer_lock.vector_store_writer(f'ingest: {document.title}'):
            return self._process(document)

    def handle_by_id(self, doc_id: int) -> Document:
        document = self.repo.get_by_id(doc_id)
        if not document:
            raise ValueError(f'Document {doc_id} not found')
        # Taken BEFORE the row is marked processing. A refusal that arrived after
        # the status write would leave a row stuck in a non-retryable state —
        # the exact failure this locking exists alongside.
        with writer_lock.vector_store_writer(f'ingest: {document.title}'):
            return self._process(document)

    def retry_by_id(self, doc_id: int) -> Document:
        document = self.repo.get_by_id(doc_id)
        if not document:
            raise ValueError(f'Document {doc_id} not found')
        # ONE lock around both steps: re-entering the same process is refused,
        # and a gap between the delete and the insert would let another writer in.
        with writer_lock.vector_store_writer(f'retry: {document.title}'):
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
            raw = '\n'.join(p.page_content for p in pages)
            text = _normalize_pdf_arabic(raw)
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
        raw_chunks = splitter.split_text(text) if text else []
        # Table-of-contents lines are near-exact matches for any question that
        # reuses a section title, so they must not enter the index at all.
        return [c for c in raw_chunks if not _is_toc_only_chunk(c)]