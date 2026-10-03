"""Authentication endpoints — PROBLEM 7: MANDATORY registration.

Registration is MANDATORY: the whole assistant (chat / documents / settings)
is gated behind a Django session. These endpoints are the ONLY public ones
under ``/api/auth``; every router bound in ``api/urls.py`` uses Ninja's
``django_auth`` so an unauthenticated request gets ``401``.

Safety rules applied here (same spirit as the chat error contract):
  * Errors go out as GENERIC Arabic messages only — no raw exceptions, no
    tracebacks, no technical detail, no internal identifiers.
  * Passwords are never echoed, never logged, never returned.
  * The Gemini key is stored INSIDE the account (server-side) and is never
    returned to the client — the API only reports ``has_gemini_key``.
"""

import logging

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from ninja import Router
from ninja.responses import Response
from ninja.security import django_auth
from pydantic import BaseModel, field_validator

from api import auth_lockout
from api.models import UserProfile

logger = logging.getLogger(__name__)
router = Router()

User = get_user_model()

# Product decision: the user is told plainly that the name is unavailable, so
# they know to change it. This DOES mean a visitor can probe which usernames
# exist — an accepted trade-off for a clearer signup, not an oversight.
# Keep it separate from SAFE_REGISTER_ERROR so a genuine server fault never
# claims the name was taken (which would be a lie and send them in circles).
TAKEN_USERNAME_ERROR = "اسم المستخدم غير متاح، يرجى تغييره"

# Used ONLY for a real fault (line ~115), where the name may well be fine.
SAFE_REGISTER_ERROR = "تعذّر إنشاء الحساب — حاول مرة أخرى"
SAFE_LOGIN_ERROR = "اسم المستخدم أو كلمة المرور غير صحيحة"
SAFE_PASSWORD_ERROR = "كلمة المرور ضعيفة — استخدم 8 أحرف على الأقل وتجنّب الأرقام المتتالية أو الكلمات الشائعة"

MIN_USERNAME_LENGTH = 3


def _attempts_left(remaining: int) -> str:
    """Arabic phrasing for "N attempts left", correct for each plural form."""
    if remaining <= 0:
        return "لم تعد هناك محاولات متبقية"
    if remaining == 1:
        return "بقيت لك محاولة واحدة"
    if remaining == 2:
        return "بقيت لك محاولتان"
    if remaining <= 10:
        return f"بقي لك {remaining} محاولات"
    return f"بقي لك {remaining} محاولة"


def _locked_response(seconds: int) -> Response:
    """The refusal for a username whose failed-attempt budget is spent.

    ``Retry-After`` carries the real remaining time so the browser (or any
    client) knows when the door reopens instead of guessing.
    """
    minutes = auth_lockout.LOCK_MINUTES
    return Response(
        {"detail": f"تم إيقاف الدخول مؤقتًا بسبب محاولات فاشلة — حاول بعد {minutes} دقيقة"},
        status=429,
        headers={"Retry-After": str(max(1, int(seconds)))},
    )


def get_profile(user) -> UserProfile:
    """Return (creating if needed) the account profile for a user."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


class RegisterIn(BaseModel):
    username: str
    password: str
    password_confirm: str

    @field_validator("username")
    @classmethod
    def _username_clean(cls, v: str) -> str:
        return v.strip()


class LoginIn(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def _username_clean(cls, v: str) -> str:
        return v.strip()


class AuthOut(BaseModel):
    username: str
    has_gemini_key: bool
    llm_provider: str | None


class MeOut(BaseModel):
    username: str
    has_gemini_key: bool
    llm_provider: str | None


def _auth_payload(user) -> dict:
    """Plain dict so Ninja validates it against the declared response
    schema (AuthOut for register/login, MeOut for /me)."""
    profile = get_profile(user)
    return {
        "username": user.username,
        "has_gemini_key": profile.has_gemini_key(),
        "llm_provider": profile.llm_provider or None,
    }


@router.post("/register", response=AuthOut)
def register(request, payload: RegisterIn):
    username = payload.username
    password = payload.password

    if len(username) < MIN_USERNAME_LENGTH:
        return Response({"detail": "اسم المستخدم قصير جدًّا (3 أحرف على الأقل)"}, status=422)
    if any(c.isspace() for c in username):
        return Response({"detail": "اسم المستخدم لا يقبل مسافات"}, status=422)
    if User.objects.filter(username__iexact=username).exists():
        # Plainly tells the user the name is taken so they change it (product
        # decision). The wording must NEVER name a person or hint at anything
        # beyond availability — no "already registered by", no timestamps.
        return Response({"detail": TAKEN_USERNAME_ERROR}, status=422)
    if password != payload.password_confirm:
        return Response({"detail": "كلمتا المرور غير متطابقتين"}, status=422)
    try:
        validate_password(password)
    except ValidationError:
        # Generic Arabic message only — never echo the validator's raw hint.
        return Response({"detail": SAFE_PASSWORD_ERROR}, status=422)

    try:
        user = User.objects.create_user(username=username, password=password)
    except Exception:
        logger.exception("Registration failed to create user")
        return Response({"detail": SAFE_REGISTER_ERROR}, status=500)

    get_profile(user)
    # Auto-login after registration: the session is the credential for the app.
    login(request, user)
    logger.info("New account registered: %s", user.username)
    return _auth_payload(user)


@router.post("/login", response=AuthOut)
def login_endpoint(request, payload: LoginIn):
    # Checked BEFORE the password is verified, so a locked account costs no
    # hashing work and the lock cannot be probed with the real password.
    locked_for = auth_lockout.locked_seconds(payload.username)
    if locked_for > 0:
        return _locked_response(locked_for)

    user = authenticate(request, username=payload.username, password=payload.password)
    if user is None or not user.is_active:
        # Counted for EVERY username, existing or not. Counting only real
        # accounts would make the lock an existence oracle: "I got locked"
        # would confirm the account, on a public form, for ten guesses.
        state = auth_lockout.record_failure(payload.username)
        if state.locked:
            return _locked_response(state.locked_seconds)
        if state.remaining <= auth_lockout.WARN_AFTER:
            return Response(
                {"detail": f"{SAFE_LOGIN_ERROR} — {_attempts_left(state.remaining)}"},
                status=401,
            )
        # Generic — reveals nothing about whether the user exists.
        return Response({"detail": SAFE_LOGIN_ERROR}, status=401)

    auth_lockout.clear_failures(payload.username)
    login(request, user)
    logger.info("User logged in: %s", user.username)
    return _auth_payload(user)


@router.post("/logout", auth=django_auth, response=dict)
def logout_endpoint(request):
    logout(request)
    return {"detail": "تم تسجيل الخروج بنجاح"}


@router.get("/me", auth=django_auth, response=MeOut)
def me(request):
    return _auth_payload(request.user)