import logging
import uuid
import re
from dataclasses import dataclass
from typing import Generator
from domain.ports.vector_store import VectorStorePort, SearchResult
from domain.ports.embedding import EmbeddingPort
from domain.ports.llm import LLMPort
from domain.entities.conversation import Conversation
from core.config import settings

logger = logging.getLogger(__name__)

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


@dataclass
class AskQuestionCommand:
    question: str
    conversation_id: str | None = None

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

class AskQuestionHandler:
    def __init__(
        self,
        vector_store: VectorStorePort,
        embedder: EmbeddingPort,
        llm: LLMPort,
        conversations: dict[str, Conversation] | None = None,
    ):
        self.vector_store = vector_store
        self.embedder = embedder
        self.llm = llm
        self.conversations = conversations if conversations is not None else {}

    def _get_or_create_conversation(self, conv_id: str) -> Conversation:
        if conv_id not in self.conversations:
            self.conversations[conv_id] = Conversation(
                id=conv_id,
                max_history=settings.CONVERSATION_HISTORY_LENGTH,
                max_context_chars=settings.LLM_MAX_CONTEXT_CHARS,
            )
        return self.conversations[conv_id]

    def _retrieve(self, question: str) -> tuple[list[SearchResult], str]:
        query_embedding = self.embedder.encode_query(question)

        from application.di import get_document_repo
        all_docs = get_document_repo().get_all()

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
            all_results.extend(doc_results)

        all_results.sort(key=lambda r: r.score, reverse=True)
        filtered = [r for r in all_results if r.score >= settings.RETRIEVAL_SCORE_THRESHOLD]
        if not filtered:
            filtered = all_results[:3]

        context = self._build_context(filtered)
        return filtered, context

    def handle(self, command: AskQuestionCommand) -> AnswerResult:
        safe_question = _sanitize_input(command.question)
        results, context = self._retrieve(safe_question)
        sources = [r.metadata for r in results if r.metadata]

        conv_id = command.conversation_id or str(uuid.uuid4())[:8]
        conversation = self._get_or_create_conversation(conv_id)
        history = conversation.history

        prompt = self._build_prompt(safe_question, context, history)
        answer = self.llm.generate(prompt)

        conversation.add_message('user', command.question)
        conversation.add_message('assistant', answer, sources)

        logger.info(f'Q: {safe_question[:60]}... -> {len(sources)} sources')

        return AnswerResult(
            answer=answer,
            sources=sources,
            conversation_id=conv_id,
        )

    def handle_stream_sync(self, command: AskQuestionCommand) -> Generator[StreamChunk, None, None]:
        safe_question = _sanitize_input(command.question)
        results, context = self._retrieve(safe_question)
        sources = [r.metadata for r in results if r.metadata]

        conv_id = command.conversation_id or str(uuid.uuid4())[:8]
        conversation = self._get_or_create_conversation(conv_id)
        history = conversation.history

        prompt = self._build_prompt(safe_question, context, history)

        full_answer = ''
        for chunk in self.llm.generate_stream(prompt):
            full_answer += chunk
            yield StreamChunk(content=chunk)

        conversation.add_message('user', command.question)
        conversation.add_message('assistant', full_answer, sources)

        yield StreamChunk(sources=sources)
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
