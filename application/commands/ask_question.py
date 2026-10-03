import logging
import uuid
import re
from dataclasses import dataclass
from typing import Generator
from domain.ports.vector_store import VectorStorePort, SearchResult
from domain.ports.embedding import EmbeddingPort
from domain.ports.llm import LLMPort
from domain.ports.conversation_repository import ConversationRepositoryPort
from domain.entities.conversation import Conversation
from domain.entities.source import Source
from core.config import settings
from application.commands.upload_document import _TOC_DOTTED_RE

logger = logging.getLogger(__name__)

_DEFAULT_SCOPE = 'default'

# ---------------------------------------------------------------------------
# Section anchors for retrieval expansion
#
# Questions that scan a whole section ("ابحث في كل الفصول عن الصعوبات
# والتحديات") retrieve the section heading but not the section body: the top-K
# points at "4-4 الصعوبات والتحديات", while the actual paragraphs live in the
# following chunks. A chunk counts as an anchor when any of its lines is a
# real heading — a numbered heading ("4-4-1 تغير ظروف الإضاءة") or a chapter
# heading ("الفصل الرابع"). A dotted-leader row is never a heading: it is a
# فهرس row, not content.
# ---------------------------------------------------------------------------
_NUMBERED_HEADING_LINE_RE = re.compile(r'^\s*\d+(?:[-.]\s*\d+){1,3}\s+[\u0600-\u06FF]')
_CHAPTER_HEADING_LINE_RE = re.compile(r'^\s*\u0627\u0644\u0641\u0635\u0644\s+[\u0600-\u06FF]')


def _is_section_anchor(text: str) -> bool:
    """True when ``text`` carries a real section or chapter heading line."""
    for ln in text.split('\n'):
        ln = ln.strip()
        if not ln or _TOC_DOTTED_RE.search(ln):
            continue
        if _NUMBERED_HEADING_LINE_RE.search(ln):
            return True
        if _CHAPTER_HEADING_LINE_RE.search(ln):
            return True
    return False

_INJECTION_PATTERNS = [
    re.compile(r'(?i)(ignore|disregard|forget)\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?)'),
    re.compile(r'(?i)you\s+are\s+now\s+'),
    re.compile(r'(?i)new\s+instructions?:'),
    re.compile(r'(?i)system\s*prompt:'),
    re.compile(r'(?i)<\s*system\s*>'),
]


def _sanitize_input(text: str) -> str:
    for pattern in _INJECTION_PATTERNS:
        text = pattern.sub('[FILTERED]', text)
    return text


def _snippet(text: str) -> str:
    """Truncate a chunk for the UI; the full text still reaches the LLM context."""
    max_chars = settings.SOURCE_SNIPPET_MAX_CHARS
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + '…'
    return text


def _build_sources(results: list[SearchResult]) -> list[Source]:
    """Map internal search results to public ``Source`` objects.

    Only explicitly-selected fields are copied, so internal metadata keys
    (doc_id, chunk_index, index, user_id, ...) can never reach the client.
    """
    sources: list[Source] = []
    for result in results:
        if not result.metadata:
            continue
        sources.append(
            Source(
                content=_snippet(result.content),
                score=result.score,
                document_id=str(result.metadata.get('doc_id', '')),
                title=result.metadata.get('source'),
            )
        )
    return sources


@dataclass
class AskQuestionCommand:
    question: str
    conversation_id: str | None = None
    session_key: str | None = None
    # The account whose documents may be searched. Retrieval walks the caller's
    # documents one by one, so scoping the list is what keeps another user's
    # file text out of the answer and out of the source excerpts.
    owner_id: int | None = None

@dataclass
class AnswerResult:
    answer: str
    sources: list[dict]
    conversation_id: str

@dataclass
class StreamChunk:
    content: str | None = None
    sources: list[dict] | None = None
    done: bool = False
    conversation_id: str | None = None

class AskQuestionHandler:
    def __init__(
        self,
        vector_store: VectorStorePort,
        embedder: EmbeddingPort,
        llm: LLMPort,
        conversation_repo: ConversationRepositoryPort | None = None,
        document_repo=None,
    ):
        self.vector_store = vector_store
        self.embedder = embedder
        self.llm = llm
        if conversation_repo is None:
            from infrastructure.repository.in_memory_conversation_repo import InMemoryConversationRepository
            conversation_repo = InMemoryConversationRepository(
                max_per_scope=settings.CONVERSATIONS_MAX_PER_SESSION,
                ttl_minutes=settings.CONVERSATION_TTL_MINUTES,
            )
        self.conversation_repo = conversation_repo
        # Resolved lazily on first retrieval so plain unit tests can stay
        # database-free (and future user-scoping wires it via DI).
        self._document_repo = document_repo

    def _resolve_document_repo(self):
        if self._document_repo is None:
            from application.di import get_document_repo
            self._document_repo = get_document_repo()
        return self._document_repo

    def _get_or_create_conversation(self, conv_id: str, scope: str) -> Conversation:
        return self.conversation_repo.get_or_create(
            scope,
            conv_id,
            factory=lambda: Conversation(
                id=conv_id,
                max_history=settings.CONVERSATION_HISTORY_LENGTH,
                max_context_chars=settings.LLM_MAX_CONTEXT_CHARS,
            ),
        )

    def _retrieve(self, question: str, owner_id: int | None = None) -> tuple[list[SearchResult], str]:
        query_embedding = self.embedder.encode_query(question)

        all_docs = self._resolve_document_repo().get_all(owner_id=owner_id)

        if not all_docs:
            return [], ''

        per_doc_k = settings.RETRIEVAL_TOP_K
        all_results: list[SearchResult] = []
        for doc in all_docs:
            doc_results = self.vector_store.search(
                query_embedding,
                top_k=per_doc_k,
                where={'doc_id': str(doc.id)},
            )
            all_results.extend(self._expand_sections(doc.id, doc_results))

        all_results.sort(key=lambda r: r.score, reverse=True)
        filtered = [r for r in all_results if r.score >= settings.RETRIEVAL_SCORE_THRESHOLD]
        if not filtered:
            filtered = all_results[:3]

        context = self._build_context(filtered)
        return filtered, context

    def _expand_sections(self, doc_id: int, results: list[SearchResult]) -> list[SearchResult]:
        """Append the body of any section heading the retriever matched.

        A top-K match on a heading points at the heading but not the section
        body; breadth questions then miss most of the actual content. For every
        retrieved chunk that carries a section/chapter heading, pull the next
        ``RETRIEVAL_SECTION_EXPAND`` chunks in reading order so the body
        reaches the LLM too. Expansions inherit their anchor's score, are
        deduplicated against already-retrieved chunks, and the total stays
        under ``RETRIEVAL_MAX_CONTEXT_CHUNKS``.
        """
        expand = settings.RETRIEVAL_SECTION_EXPAND
        if expand <= 0 or not results:
            return results
        cap = settings.RETRIEVAL_MAX_CONTEXT_CHUNKS
        seen = {
            r.metadata.get('index') for r in results
            if isinstance(r.metadata.get('index'), int)
        }
        out = list(results)
        for anchor in results:
            if len(out) >= cap:
                break
            if not _is_section_anchor(anchor.content):
                continue
            idx = anchor.metadata.get('index')
            if not isinstance(idx, int):
                continue
            wanted = list(range(idx + 1, idx + 1 + expand))
            if not wanted:
                continue
            for sibling in self.vector_store.fetch_chunks(doc_id, wanted):
                sibling_index = sibling.metadata.get('index')
                if not isinstance(sibling_index, int) or sibling_index in seen:
                    continue
                seen.add(sibling_index)
                out.append(SearchResult(
                    content=sibling.content,
                    score=anchor.score,
                    metadata=sibling.metadata,
                ))
                if len(out) >= cap:
                    break
        return out

    def handle(self, command: AskQuestionCommand) -> AnswerResult:
        safe_question = _sanitize_input(command.question)
        results, context = self._retrieve(safe_question, command.owner_id)
        sources = _build_sources(results)

        conv_id = command.conversation_id or str(uuid.uuid4())[:8]
        scope = command.session_key or _DEFAULT_SCOPE
        conversation = self._get_or_create_conversation(conv_id, scope)
        history = conversation.history

        prompt = self._build_prompt(safe_question, context, history)
        answer = self.llm.generate(prompt)

        conversation.add_message('user', command.question)
        conversation.add_message('assistant', answer, [s.to_dict() for s in sources])

        logger.info(f'Q: {safe_question[:60]}... -> {len(sources)} sources')

        return AnswerResult(
            answer=answer,
            sources=[s.to_dict() for s in sources],
            conversation_id=conv_id,
        )

    def handle_stream_sync(self, command: AskQuestionCommand) -> Generator[StreamChunk, None, None]:
        safe_question = _sanitize_input(command.question)
        results, context = self._retrieve(safe_question, command.owner_id)
        sources = _build_sources(results)

        conv_id = command.conversation_id or str(uuid.uuid4())[:8]
        scope = command.session_key or _DEFAULT_SCOPE
        conversation = self._get_or_create_conversation(conv_id, scope)
        history = conversation.history

        prompt = self._build_prompt(safe_question, context, history)

        # Emit the conversation id FIRST so the client can keep its memory alive
        # across messages (streams otherwise have no channel for it).
        yield StreamChunk(conversation_id=conv_id)

        full_answer = ''
        for chunk in self.llm.generate_stream(prompt):
            full_answer += chunk
            yield StreamChunk(content=chunk)

        conversation.add_message('user', command.question)
        conversation.add_message('assistant', full_answer, [s.to_dict() for s in sources])

        yield StreamChunk(sources=[s.to_dict() for s in sources])
        yield StreamChunk(done=True)

    def _build_context(self, results: list[SearchResult]) -> str:
        return '\n\n'.join(
            f'[Source {i+1}]: {r.content}' for i, r in enumerate(results)
        )

    def _build_prompt(self, question: str, context: str, history: str) -> str:
        if not context:
            return f'''Question: {question}

Note: No relevant information found in the uploaded documents.
Answer that there is not enough information to answer this question.'''

        return f'''You are a helpful assistant. Answer based ONLY on the provided context.

Context:
---
{context}
---

Conversation history:
{history}

Question: {question}

Instructions:
1. Answer based only on the provided context
2. If the answer is not clearly stated, mention what information IS available and suggest the user check the source documents
3. Cite sources when possible

Answer:'''
