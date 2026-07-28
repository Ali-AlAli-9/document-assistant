import json
import logging
from ninja import Router
from pydantic import BaseModel
from django.http import StreamingHttpResponse
from ninja.responses import Response
from application.di import get_vector_store, get_embedder, get_ask_handler
from application.commands.ask_question import AskQuestionCommand
from core.config import settings
from core.exceptions import LLMError, VectorStoreError

logger = logging.getLogger(__name__)
router = Router()


MAX_QUESTION_LENGTH = 2000


class AskIn(BaseModel):
    question: str
    conversation_id: str | None = None


class SourceOut(BaseModel):
    content: str
    score: float
    document_id: str


class AnswerOut(BaseModel):
    answer: str
    sources: list[SourceOut]
    conversation_id: str


@router.post('/ask', response=AnswerOut)
def ask_question(request, payload: AskIn):
    question = payload.question.strip()
    if not question:
        return Response({'detail': 'Question cannot be empty'}, status=422)
    if len(question) > MAX_QUESTION_LENGTH:
        return Response({'detail': f'Question exceeds maximum length of {MAX_QUESTION_LENGTH} characters'}, status=422)
    provider = request.headers.get('X-Provider') or settings.LLM_PROVIDER
    api_key = request.headers.get('X-API-Key')
    command = AskQuestionCommand(
        question=question,
        conversation_id=payload.conversation_id,
    )
    handler = get_ask_handler(provider, api_key)
    try:
        result = handler.handle(command)
    except VectorStoreError as e:
        logger.exception('Vector store error during ask')
        return Response({'detail': str(e)}, status=503)
    except LLMError as e:
        logger.exception('LLM error during ask')
        return Response({'detail': str(e)}, status=503)
    except Exception:
        logger.exception('Unexpected error during ask')
        return Response({'detail': 'Internal server error'}, status=500)
    return AnswerOut(
        answer=result.answer,
        sources=[
            SourceOut(
                content=s.get('content', s.get('source', '')),
                score=s.get('score', 0.0),
                document_id=s.get('doc_id', ''),
            )
            for s in result.sources
        ],
        conversation_id=result.conversation_id,
    )


@router.post('/stream')
def chat_stream(request, payload: AskIn):
    question = payload.question.strip()
    if not question:
        return Response({'detail': 'Question cannot be empty'}, status=422)
    if len(question) > MAX_QUESTION_LENGTH:
        return Response({'detail': f'Question exceeds maximum length of {MAX_QUESTION_LENGTH} characters'}, status=422)
    provider = request.headers.get('X-Provider') or settings.LLM_PROVIDER
    api_key = request.headers.get('X-API-Key')
    command = AskQuestionCommand(
        question=question,
        conversation_id=payload.conversation_id,
    )
    handler = get_ask_handler(provider, api_key)

    def event_stream():
        try:
            for chunk in handler.handle_stream_sync(command):
                if chunk.done:
                    yield f'data: {json.dumps({"done": True})}\n\n'
                elif chunk.sources is not None:
                    yield f'data: {json.dumps({"sources": chunk.sources})}\n\n'
                elif chunk.content:
                    yield f'data: {json.dumps({"chunk": chunk.content})}\n\n'
        except VectorStoreError as e:
            logger.exception('Vector store error during stream')
            yield f'data: {json.dumps({"error": str(e)})}\n\n'
        except LLMError as e:
            logger.exception('LLM error during stream')
            yield f'data: {json.dumps({"error": str(e)})}\n\n'
        except Exception:
            logger.exception('Unexpected error during stream')
            yield f'data: {json.dumps({"error": "Internal server error"})}\n\n'

    return StreamingHttpResponse(event_stream(), content_type='text/event-stream')
