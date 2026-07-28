import logging
from ninja import Router
from pydantic import BaseModel
from ninja.responses import Response
from core.config import settings
from core.exceptions import LLMError

logger = logging.getLogger(__name__)
router = Router()


class ValidateKeyIn(BaseModel):
    api_key: str
    provider: str | None = None


class ValidateKeyOut(BaseModel):
    valid: bool
    detail: str


@router.post('/validate-key', response=ValidateKeyOut)
def validate_api_key(request, payload: ValidateKeyIn):
    if not payload.api_key.strip():
        return {'valid': False, 'detail': 'المفتاح فارغ'}

    provider = payload.provider or 'gemini'

    try:
        from application.di import get_llm
        llm = get_llm(provider, payload.api_key.strip())
        llm.generate('Just say "ok".')
        return {'valid': True, 'detail': 'المفتاح شغال بنجاح'}
    except LLMError as e:
        return {'valid': False, 'detail': f'خطأ في المفتاح: {e}'}
    except Exception as e:
        logger.exception('Key validation error')
        return {'valid': False, 'detail': f'خطأ في الاتصال: {e}'}
