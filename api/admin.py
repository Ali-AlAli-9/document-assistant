"""Django admin registration for the internal (non-public) error log.

This is the ONLY way the internal table is readable, and it is gated behind
Django's staff authentication (the admin site). There is deliberately no public
URL and no API endpoint that exposes these rows; contract tests in
``tests/api/test_chat_error_safety_contract.py`` lock that fact.
"""

from django.contrib import admin

from api.models import ChatErrorLog, UserProfile
from core.crypto import SecretDecryptionError


def _mask_key(key: str) -> str:
    if not key:
        return "—"
    if len(key) <= 8:
        return "••••"
    return f"{key[:4]}••••••••{key[-4:]}"


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """Read-mostly internal view of per-account LLM settings (staff only)."""

    list_display = ("user", "llm_provider", "key_masked", "updated_at")

    def key_masked(self, obj):
        # Show a mask of the real key so staff can tell two keys apart without
        # the page ever rendering the key (or its ciphertext).
        try:
            return _mask_key(obj.get_gemini_api_key())
        except SecretDecryptionError:
            return "⚠︎ غير قابل للفك (تغيّر SECRET_KEY)"

    key_masked.short_description = "مفتاح Gemini (مُخفًى)"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request):
        return False

    # The raw column is deliberately NOT in the form: a readonly field still
    # renders its value, so including it would put the key on screen for any
    # staff account. Only the masked form of it is shown.
    fields = ("user", "llm_provider", "key_masked", "updated_at")
    readonly_fields = fields


@admin.register(ChatErrorLog)
class ChatErrorLogAdmin(admin.ModelAdmin):
    """Read-mostly, internal diagnostic view (staff only)."""

    list_display = ("error_ref", "error_type", "provider", "conversation_id", "created_at")
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request):
        return False
    def has_delete_permission(self, request):
        return False
    list_filter = ("error_type", "provider", "created_at")
    search_fields = ("error_ref", "conversation_id", "error_message")
    readonly_fields = (
        "error_ref",
        "conversation_id",
        "provider",
        "error_type",
        "error_message",
        "created_at",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
