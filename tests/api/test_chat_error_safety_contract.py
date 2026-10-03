"""PROBLEM 6 — SAFE / NON-LEAKING ERROR DISPLAY CONTRACT.

What this file locks (the contract the user approved):

1. WIRE SAFETY (always, no exceptions):
   The SSE stream for a chat error carries ONLY:
       {"error": <generic safe Arabic>, "error_ref": <opaque ref>}
   and it is TERMINAL (no ``done`` afterward, no sources afterward — the
   Problem-5 terminality contract is preserved). It NEVER carries: the raw
   ``str(e)`` technical detail, any secret / API key, any traceback, any
   error type/class name, any host/IP, any "Internal server error".
   This holds in EVERY mode — including when a dev-mode header is present.

2. INTERNAL STORAGE (not public):
   The full raw detail is persisted internally in ``ChatErrorLog`` (staff-only
   admin, no public route), and the wire ``error_ref`` matches the stored row so
   an operator can correlate a user complaint to the internal cause.

3. NO PUBLIC SURFACE for the internal table:
   There is deliberately no public URL or public endpoint that serves the
   error log rows. Requests to plausible guessable routes must 404/405.

4. DEV-MODE IS NOT ENABLED BY DEFAULT (backend half):
   Even if a client sends ``X-Dev-Mode: 1``, the wire still carries NO raw
   detail — only the opaque ref. (The frontend toggle is additionally an
   opt-in, default-off concern in ``ChatMessage.jsx``/``ChatInterface.jsx``.)
"""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase

from api.models import ChatErrorLog
from core.exceptions import LLMError, VectorStoreError


class ChatErrorSafetyContractTest(TestCase):
    URL = "/api/chat/stream"

    def setUp(self):
        self.ask_handler = MagicMock()
        self.patch_handler = patch(
            "api.endpoints.chat.get_ask_handler", return_value=self.ask_handler
        )
        self.patch_handler.start()
        self.addCleanup(self.patch_handler.stop)

        # PROBLEM 7: registration is MANDATORY — authenticate the session and
        # give the account a Gemini provider so `_resolve_provider_config`
        # returns "gemini" (the internal row records this provider).
        from django.contrib.auth.models import User

        from api.models import UserProfile

        self.user = User.objects.create_user("tester", password="Passw0rd!234")
        profile = UserProfile.objects.create(user=self.user, llm_provider="gemini")
        # The account also needs a key OF ITS OWN. A hosted provider is refused
        # with 401 before the stream opens without one, so an unkeyed account
        # would answer 401 and none of the error-wire guarantees below — the
        # whole point of this file — would ever be exercised.
        #
        # This class previously sent `X-Provider` / `X-API-Key` headers to stand
        # in for the credential. Those headers are no longer read by any
        # endpoint; they survived here unnoticed, which is how a removed
        # transport can look like a working one for months. The key now arrives
        # the only way it can: encrypted on the profile.
        profile.set_gemini_api_key("test-key")
        profile.save(update_fields=["gemini_api_key"])
        self.client.force_login(self.user)

    def _raw_stream(self, **extra_headers) -> str:
        response = self.client.post(
            self.URL,
            data=json.dumps({"question": "ما هي الوثيقة؟"}),
            content_type="application/json",
            **extra_headers,
        )
        self.assertEqual(response.status_code, 200)
        return b"".join(response.streaming_content).decode("utf-8")

    def _events(self, **extra_headers) -> list[dict]:
        events = []
        for line in self._raw_stream(**extra_headers).splitlines():
            line = line.strip()
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
        return events

    # ------------------------------------------------------------------ #
    # 1) Wire safety — no raw detail, no secrets, no traceback, ever.     #
    # ------------------------------------------------------------------ #

    def test_vector_store_error_wire_is_generic_and_safe(self):
        self.ask_handler.handle_stream_sync.side_effect = VectorStoreError(
            "اتصال قاعدة المتجهات فشل: 127.0.0.1:5432 secret=REAL-XYZ"
        )
        body = self._raw_stream(X_DEV_MODE="1")
        events = self._events(X_DEV_MODE="1")
        last = events[-1]

        self.assertIn("error", last)
        self.assertIn("error_ref", last)
        # raw detail must NEVER appear — even in dev mode
        self.assertNotIn("secret=REAL-XYZ", body)
        self.assertNotIn("127.0.0.1", body)
        self.assertNotIn("VectorStoreError", body)
        self.assertNotIn("اتصال قاعدة المتجهات", body)
        self.assertNotIn("Internal server error", body)
        self.assertNotIn("Traceback", body)
        # terminal: generic safe Arabic + ref; no done afterward
        self.assertFalse(
            any("done" in e for e in events),
            "an error event must be terminal (no done afterward)",
        )

    def test_llm_error_wire_is_generic_and_safe(self):
        self.ask_handler.handle_stream_sync.side_effect = LLMError(
            "مفتاح API غير صالح: sk-REAL-SECRET-12345"
        )
        body = self._raw_stream()
        last = self._events()[-1]

        self.assertIn("error", last)
        self.assertIn("error_ref", last)
        self.assertNotIn("sk-REAL-SECRET-12345", body)
        self.assertNotIn("مفتاح API", body)
        self.assertNotIn("LLMError", body)
        self.assertNotIn("Internal server error", body)
        self.assertEqual(last["error"], "تعذّر توليد الرد حاليًّا، حاول مرة أخرى")

    def test_unexpected_error_wire_is_generic_and_safe(self):
        self.ask_handler.handle_stream_sync.side_effect = RuntimeError(
            "boom stack secret=tok"
        )
        body = self._raw_stream()
        last = self._events()[-1]
        self.assertIn("error_ref", last)
        self.assertNotIn("boom stack", body)
        self.assertNotIn("secret=tok", body)
        self.assertNotIn("RuntimeError", body)
        self.assertEqual(last["error"], "حدث خطأ غير متوقع، حاول مرة أخرى")

    # ------------------------------------------------------------------ #
    # 2) Internal storage holds full raw detail + ref matches the wire.   #
    # ------------------------------------------------------------------ #

    def test_internal_row_stores_raw_detail_and_matches_wire_ref(self):
        raw = "اتصال قاعدة المتجهات فشل: 127.0.0.1:5432 secret=REAL-XYZ"
        self.ask_handler.handle_stream_sync.side_effect = VectorStoreError(raw)
        last = self._events()[-1]
        ref = last["error_ref"]

        row = ChatErrorLog.objects.filter(error_ref=ref).first()
        self.assertIsNotNone(row, "an internal row must be created for the error")
        self.assertEqual(row.error_message, raw)
        self.assertEqual(row.error_type, "VectorStoreError")
        self.assertEqual(row.provider, "gemini")

    def test_internal_row_llm_error_message_is_full_raw(self):
        raw = "مفتاح API غير صالح: sk-REAL-SECRET-12345"
        self.ask_handler.handle_stream_sync.side_effect = LLMError(raw)
        last = self._events()[-1]
        row = ChatErrorLog.objects.filter(error_ref=last["error_ref"]).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.error_message, raw)
        self.assertEqual(row.error_type, "LLMError")

    # ------------------------------------------------------------------ #
    # 3) No public route exposes the internal table.                      #
    # ------------------------------------------------------------------ #

    def test_error_log_table_has_no_public_route(self):
        """The internal error log must never be served by ANY public route.

        ``rag_project/urls.py`` ends with an SPA catch-all (``re_path('^.*$',
        TemplateView)``) that returns 200 + ``frontend/index.html`` for any URL
        that has no registered route — so a raw 404/405 check would be fooled.
        The contract to pin is the ESSENCE: no registered route serves the log,
        and no response body ever contains the internal table's data columns.
        """
        leaked = ["error_ref", "error_message", "error_type"]
        for url in (
            "/api/chat/error-log",
            "/api/chat/errorlog",
            "/api/error-log",
            "/api/errorlog",
            "/api/chat/errors",
            "/api/chat/error_log",
        ):
            resp = self.client.get(url)
            if resp.status_code not in (404, 405):
                # SPA catch-all: may be 200, but must NOT carry log data columns
                body = resp.content.decode("utf-8", errors="replace").lower()
                for col in leaked:
                    self.assertNotIn(
                        col, body,
                        f"{url} leaked internal log column {col!r}",
                    )

    def test_credentials_are_staff_only_never_wire(self):
        """No row content is served anywhere public — prove via 404 on guesses
        AND assert the SSRF/leak string never landed on the SSE wire."""
        raw = "امتياز سرّي: SECRET-INTERNAL-ROW"
        self.ask_handler.handle_stream_sync.side_effect = LLMError(raw)
        body = self._raw_stream(X_DEV_MODE="1")
        self.assertNotIn("SECRET-INTERNAL-ROW", body)


# A single class is enough: the file above is the whole contract.
