import logging

from ninja import Router
from ninja.responses import Response
from ninja.security import django_auth
from pydantic import BaseModel, field_validator

from api.endpoints.auth import get_profile
from core.config import settings
from core.exceptions import LLMError

logger = logging.getLogger(__name__)

router = Router()

PROVIDER_CHOICES = ("gemini", "ollama")


class ValidateKeyIn(BaseModel):
    api_key: str
    provider: str | None = None


# The stored column is 512 chars and holds a Fernet token (plaintext + ~90 chars
# of overhead), so an unbounded key could overflow it on PostgreSQL. A real
# Gemini key is ~39 characters; 200 leaves generous headroom.
MAX_KEY_LENGTH = 200


class ValidateKeyOut(BaseModel):
    valid: bool
    detail: str


@router.post("/validate-key", auth=django_auth, response=ValidateKeyOut)
def validate_api_key(request, payload: ValidateKeyIn):
    if not payload.api_key.strip():
        return {"valid": False, "detail": "المفتاح فارغ"}

    provider = payload.provider or "gemini"
    # Same allowlist as save_settings (which already enforces it), applied here
    # too: this value reaches the provider factory, so an arbitrary string
    # would (a) fall through to the Ollama adapter with a server-controlled
    # base URL, and (b) create a distinct LLM cache entry, so a handful of junk
    # values is enough to evict every real visitor's cached client.
    if provider not in PROVIDER_CHOICES:
        return Response(
            {"valid": False, "detail": "مزود غير مدعوم — اختر gemini أو ollama"},
            status=422,
        )

    try:
        from application.di import get_llm

        llm = get_llm(provider, payload.api_key.strip())
        llm.generate("Just say \"ok\".")
        return {"valid": True, "detail": "المفتاح شغال بنجاح"}
    except LLMError:
        # Generic Arabic message — never echo the raw exception detail.
        logger.warning("Gemini key validation rejected (LLMError)")
        return {"valid": False, "detail": "المفتاح غير صالح أو منتهي الصلاحية"}
    except Exception:
        logger.exception("Key validation error")
        return {"valid": False, "detail": "تعذّر الاتصال بمزود الخدمة، حاول مرة أخرى"}


class SettingsGetOut(BaseModel):
    provider: str
    has_gemini_key: bool


@router.get("", auth=django_auth, response=SettingsGetOut)
def get_settings(request):
    profile = get_profile(request.user)
    return SettingsGetOut(
        provider=profile.llm_provider,
        has_gemini_key=profile.has_gemini_key(),
    )


class SettingsIn(BaseModel):
    provider: str
    gemini_api_key: str | None = None

    @field_validator("provider")
    @classmethod
    def _provider_clean(cls, v: str) -> str:
        return v.strip().lower()


class SettingsOut(BaseModel):
    provider: str
    has_gemini_key: bool
    detail: str


@router.post("", auth=django_auth, response=SettingsOut)
def save_settings(request, payload: SettingsIn):
    if payload.provider not in PROVIDER_CHOICES:
        return Response(
            {"detail": "مزود غير مدعوم — اختر gemini أو ollama"},
            status=422,
        )

    profile = get_profile(request.user)
    profile.llm_provider = payload.provider

    key = (payload.gemini_api_key or "").strip()
    if key:
        if len(key) > MAX_KEY_LENGTH:
            return Response(
                {"detail": f"المفتاح طويل جدًا (الحد {MAX_KEY_LENGTH} حرفًا)"},
                status=422,
            )
        # Stored INSIDE the account on the server, ENCRYPTED at rest; never
        # returned to the client.
        profile.set_gemini_api_key(key)
    profile.save(update_fields=["llm_provider", "gemini_api_key", "updated_at"])

    return SettingsOut(
        provider=profile.llm_provider,
        has_gemini_key=profile.has_gemini_key(),
        detail="تم حفظ الإعدادات بنجاح",
    )