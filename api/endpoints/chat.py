import json
import logging
import secrets
from ninja import Router
from pydantic import BaseModel
from django.http import StreamingHttpResponse
from ninja.responses import Response
from application.di import get_vector_store, get_embedder, get_ask_handler
from application.commands.ask_question import AskQuestionCommand
from core.config import settings
from core.exceptions import (
    LLMError,
    MissingLLMCredentials,
    VectorStoreError,
    NO_PERSONAL_KEY_DETAIL,
)
from api.endpoints.auth import get_profile
from api.models import ChatErrorLog

logger = logging.getLogger(__name__)
router = Router()


SAFE_VECTOR_ERROR = "تعذّر الوصول إلى قاعدة المعرفة حاليًّا، حاول مرة أخرى"
SAFE_LLM_ERROR = "تعذّر توليد الرد حاليًّا، حاول مرة أخرى"
SAFE_INTERNAL_ERROR = "حدث خطأ غير متوقع، حاول مرة أخرى"


def _persist_error(request, command, provider, error_type, error_message, conversation_id) -> str:
    """Persist the FULL raw detail internally (non-public staff table) and return
    an opaque error_ref for the wire. Raw detail NEVER leaves the server; the
    wire only carries this opaque ref + a generic safe Arabic message. An error
    event is terminal (no ``done`` after it) per the Problem-5 SSE contract.
    """
    error_ref = secrets.token_hex(6)
    try:
        ChatErrorLog.objects.create(
            error_ref=error_ref,
            conversation_id=conversation_id or "",
            provider=provider,
            error_type=error_type,
            error_message=error_message,
        )
    except Exception:
        # internal best-effort storage; never break the stream
        logger.exception("Failed to persist internal chat error")
    return error_ref


MAX_QUESTION_LENGTH = 2000


def _get_session_key(request) -> str:
    """Return a real Django session key, creating the session if needed.

    The conversation store is scoped by session key, so every client needs a
    stable server-side session even before authentication exists.
    """
    session = request.session
    if not session.session_key:
        session.create()
    return session.session_key or ''


def _resolve_provider_config(request):
    """Per-account LLM settings (PROBLEM 7).

    The provider and the user's OWN Gemini key now live inside the user's
    account (server-side), never in the browser. Falls back to the app defaults
    when the account has not chosen a provider yet.
    """
    profile = get_profile(request.user)
    provider = (profile.llm_provider or "").strip() or settings.LLM_PROVIDER
    # Decrypted here, server-side, for the outbound call only — the client
    # neither sends nor sees the key.
    api_key = profile.get_gemini_api_key() if provider == "gemini" else None
    return provider, api_key


# Kept in step with application.di._CREDENTIAL_REQUIRED_PROVIDERS, which is the
# authority: the factory refuses to build a keyless client, and this is the
# polite face of that refusal. Duplication is deliberate and cross-checked by
# tests/api/test_no_shared_key_fallback.py, because the two must agree or one of
# them becomes a 500.
_CREDENTIAL_REQUIRED_PROVIDERS = frozenset({'gemini'})


def _missing_credential_response(provider: str, api_key: str | None):
    """Refuse a chat that carries no key of the caller's own, before any work.

    Answering here — rather than letting the factory raise — is what makes the
    status code honest. Missing account state is not an upstream fault, so it
    must not leave as 500 or 503.

    401 does not collide with the expired-session case: the body is Arabic, and
    frontend/src/api/errors.js passes an Arabic detail through before it can
    reach the generic 401 string ("انتهت الجلسة"), which would tell a user with a
    perfectly valid session to log in again for no reason. The needle this body
    matches is pinned by tests/api/test_no_shared_key_fallback.py.

    Nothing is logged. There is no fault to report and no retry to schedule; the
    user needs to be told, not the operator paged.
    """
    if provider in _CREDENTIAL_REQUIRED_PROVIDERS and not (api_key or '').strip():
        return Response({'detail': NO_PERSONAL_KEY_DETAIL}, status=401)
    return None


class AskIn(BaseModel):
    question: str
    conversation_id: str | None = None


class SourceOut(BaseModel):
    content: str
    score: float
    document_id: str
    title: str | None = None


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
    provider, api_key = _resolve_provider_config(request)
    refusal = _missing_credential_response(provider, api_key)
    if refusal is not None:
        return refusal
    command = AskQuestionCommand(
        question=question,
        conversation_id=payload.conversation_id,
        session_key=_get_session_key(request),
        owner_id=request.user.pk,
    )
    try:
        handler = get_ask_handler(provider, api_key)
        result = handler.handle(command)
    except MissingLLMCredentials:
        # Backstop: the factory refuses rather than falling back. Reaching this
        # means the endpoint's provider set and the factory's have disagreed, so
        # answer exactly as the guard would instead of surfacing a 500.
        return _missing_credential_response(provider, api_key)
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
        sources=[SourceOut(**s) for s in result.sources],
        conversation_id=result.conversation_id,
    )


@router.post('/stream')
def chat_stream(request, payload: AskIn):
    question = payload.question.strip()
    if not question:
        return Response({'detail': 'Question cannot be empty'}, status=422)
    if len(question) > MAX_QUESTION_LENGTH:
        return Response({'detail': f'Question exceeds maximum length of {MAX_QUESTION_LENGTH} characters'}, status=422)
    provider, api_key = _resolve_provider_config(request)
    refusal = _missing_credential_response(provider, api_key)
    if refusal is not None:
        return refusal
    command = AskQuestionCommand(
        question=question,
        conversation_id=payload.conversation_id,
        session_key=_get_session_key(request),
        owner_id=request.user.pk,
    )
    try:
        handler = get_ask_handler(provider, api_key)
    except MissingLLMCredentials:
        # Backstop, same reasoning as ask_question: answer as the guard would.
        return _missing_credential_response(provider, api_key)

    def event_stream():
        try:
            for chunk in handler.handle_stream_sync(command):
                if chunk.conversation_id:
                    yield f'data: {json.dumps({"conversation_id": chunk.conversation_id})}\n\n'
                elif chunk.done:
                    yield f'data: {json.dumps({"done": True})}\n\n'
                elif chunk.sources is not None:
                    yield f'data: {json.dumps({"sources": chunk.sources})}\n\n'
                elif chunk.content:
                    yield f'data: {json.dumps({"chunk": chunk.content})}\n\n'
        except VectorStoreError as e:
            logger.exception('Vector store error during stream')
            ref = _persist_error(
                request, command, provider,
                "VectorStoreError", str(e), command.conversation_id,
            )
            yield f'data: {json.dumps({"error": SAFE_VECTOR_ERROR, "error_ref": ref})}\n\n'
        except LLMError as e:
            logger.exception('LLM error during stream')
            ref = _persist_error(
                request, command, provider,
                "LLMError", str(e), command.conversation_id,
            )
            yield f'data: {json.dumps({"error": SAFE_LLM_ERROR, "error_ref": ref})}\n\n'
        except Exception:
            logger.exception('Unexpected error during stream')
            ref = _persist_error(
                request, command, provider,
                "Unexpected", "Unexpected error during stream", command.conversation_id,
            )
            yield f'data: {json.dumps({"error": SAFE_INTERNAL_ERROR, "error_ref": ref})}\n\n'

    return StreamingHttpResponse(event_stream(), content_type='text/event-stream')
