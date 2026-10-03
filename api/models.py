"""Internal (non-public) chat error logging.

PROBLEM 6 — SECURE DISPLAY + INTERNAL STORAGE
=============================================
The safe-display contract (Problem 6/phase B) demands that a user-facing chat
error is ALWAYS a generic, escaped Arabic message that never contains anything
technical, secret, or a traceback. The RAW technical detail of what failed is
never put on the SSE wire in any mode.

Instead, the full detail is captured at the single error-catch point (in
``api/endpoints/chat.py`` ``event_stream``) and persisted here — an internal
table that is NOT exposed through any public endpoint. The user-facing event
only carries a generic safe message plus an opaque ``error_ref`` so an operator
can correlate the complaint with the internal row (and its technical cause)
later, without ever sending the details to the browser.

Dev-mode (frontend) is OPT-IN and OFF by default; when enabled it reveals only
the opaque error_ref for correlation — raw details never leave the server.

This table is deliberately NOT registered on any public URL and is only
reachable through the Django admin (staff): contracts in
``tests/api/test_chat_error_safety_contract.py`` pin both facts.
"""

from django.conf import settings
from django.db import models

from core.crypto import decrypt_secret, encrypt_secret


def _default_error_ref() -> str:
    """Short opaque reference used to correlate a user complaint to a row.

    12 chars, kept opaque and free of dashes so it is easy to read/quote aloud;
    it is NOT a secret and carries no technical meaning.
    """
    import secrets

    return secrets.token_hex(6)


class ChatErrorLog(models.Model):
    """One row per chat error, storing the raw detail internally only."""

    error_ref = models.CharField(
        max_length=12,
        unique=True,
        default=_default_error_ref,
        editable=False,
    )
    conversation_id = models.CharField(max_length=128, blank=True, default="")
    provider = models.CharField(max_length=32, blank=True, default="")
    error_type = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "سجلّ خطأ المحادثة (داخلي)"
        verbose_name_plural = "سجلات أخطاء المحادثة (داخلية)"

    def __str__(self) -> str:
        return f"{self.error_ref} | {self.error_type}"


class UserProfile(models.Model):
    """Per-account LLM preferences (PROBLEM 7 — mandatory registration).

    Registration is now MANDATORY: every usage of the assistant (chat,
    documents, settings) is gated behind a Django session (see ``api/urls.py``
    where the chat/documents/settings routers are bound to ``django_auth``).

    Each registered user brings his OWN provider configuration:

    * ``llm_provider`` — ``gemini`` (cloud, needs the user's own key) or
      ``ollama`` (local, no key). Both options intentionally remain available.
    * ``gemini_api_key`` — the user's PERSONAL Gemini key, stored INSIDE the
      account on the server (never in the browser). It is never echoed back to
      the client: the API only reports ``has_gemini_key`` (write-once).

    The key is what the user supplies so he can actually try the app; the app
    does NOT issue API keys of its own.

    The column holds the key ENCRYPTED at rest (``core.crypto``): a leaked
    database dump must not hand out working credentials. Read/write goes
    through :meth:`get_gemini_api_key` / :meth:`set_gemini_api_key` so no caller
    ever handles ciphertext by accident.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    llm_provider = models.CharField(
        max_length=16,
        choices=[("gemini", "Gemini"), ("ollama", "Ollama")],
        blank=True,
        default="",
    )
    gemini_api_key = models.CharField(max_length=512, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ملف المستخدم (مفتاح المزود)"
        verbose_name_plural = "ملفات المستخدمين (مفاتيح المزود)"

    def __str__(self) -> str:
        return f"{self.user.username} | {self.llm_provider or 'غير مضبوط'}"

    def set_gemini_api_key(self, plain_key: str) -> None:
        """Store the key ENCRYPTED; the plaintext never reaches the column."""
        self.gemini_api_key = encrypt_secret(plain_key.strip())

    def get_gemini_api_key(self) -> str:
        """Return the plaintext for an outbound LLM call (server-side only)."""
        return decrypt_secret(self.gemini_api_key)

    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key)


class LoginAttempt(models.Model):
    """Failed-login counter for ONE username — the brute-force lockout.

    ``core.middleware`` already limits login attempts per IP, but that is the
    wrong axis for password guessing: rotating the source address (VPN, proxy
    pool, botnet) resets the budget completely, so a single known account can
    be ground down at full speed. This row follows the attacker instead of the
    address.

    ``username`` is stored CASE-FOLDED and trimmed (``api.auth_lockout``), so
    every spelling of one username shares a single budget — keying on the raw
    string would let an attacker dodge the lock by changing case.

    A counter is written for EVERY failed attempt, including for usernames that
    have no account. That is deliberate and load-bearing: if rows existed only
    for real accounts, "I got locked" would answer "does this username exist?"
    and reintroduce the enumeration hole the registration endpoint closed.

    Rows are swept by ``api.auth_lockout`` once they go stale, because probing
    invented usernames would otherwise grow this table without bound.
    """

    username = models.CharField(max_length=150, unique=True)
    failures = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        verbose_name = "محاولات دخول فاشلة"
        verbose_name_plural = "محاولات دخول فاشلة"

    def __str__(self) -> str:
        return f"{self.username} | {self.failures}"
