"""Lock the terminal-LLM-error SSE contract that ChatInterface.jsx depends on.

CONTEXT (Problem 5)
------------------
The backend SSE wire for /api/chat/stream is produced by event_stream() in
api/endpoints/chat.py:

    def event_stream():
        try:
            for chunk in handler.handle_stream_sync(command):
                ... conversation_id / chunk / sources / done ...
        except VectorStoreError as e:
            yield 'data: {"error": ...}'
        except LLMError as e:
            yield 'data: {"error": ...}'
        except Exception:
            yield 'data: {"error": "Internal server error"}'

So when the LLM call fails the endpoint emits a TERMINAL in-band event
``{"error": ...}`` and the generator ends there — no `done` event EVER
follows an error.

The frontend (ChatInterface.jsx) relies on EXACTLY this contract:
  - `data.error` is treated the same as `data.done`: stop appending chunks,
    mark the last assistant message as failed, and DO NOT attach sources.
  - Because the error event is terminal, there is no `done` after it.

These tests pin that wire contract at the HTTP/SSE level so a backend
refactor cannot silently break the frontend's streaming assumptions.
"""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase


class ChatStreamErrorContractTests(TestCase):
    """Pin the SSE error-wire contract at the HTTP layer."""

    URL = "/api/chat/stream"

    def setUp(self):
        # PROBLEM 7: registration is MANDATORY — authenticate the session.
        from django.contrib.auth.models import User

        from api.models import UserProfile

        self.user = User.objects.create_user("tester", password="Passw0rd!234")
        profile = UserProfile.objects.create(user=self.user, llm_provider="gemini")
        # The account must own a key of its own. A hosted provider is refused
        # with 401 BEFORE the stream opens when it does not -- see
        # api/endpoints/chat.py::_missing_credential_response -- so without this
        # these tests would never reach the SSE contract they exist to pin.
        #
        # The key goes on the profile through the encrypted setter, which is the
        # only way it can arrive now. These tests used to pass `X-Provider` /
        # `X-API-Key` request headers instead; that transport was removed when
        # keys moved server-side (frontend/src/api/client.js records it) and the
        # backend reads neither header, so passing them achieved nothing except
        # hiding this prerequisite.
        profile.set_gemini_api_key("test-key")
        profile.save(update_fields=["gemini_api_key"])
        self.client.force_login(self.user)

    def _post_stream(self):
        """POST to the stream endpoint; return the raw SSE body text."""
        payload = {
            "question": "ما هي الوثيقة؟",
            "conversation_id": None,
        }
        response = self.client.post(
            self.URL,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        # StreamingHttpResponse -> consume the whole body; Django yields bytes
        return b"".join(response.streaming_content).decode("utf-8")

    @staticmethod
    def _sse_events(body):
        """Parse SSE `data: {...}` lines into a list of dicts."""
        events = []
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
        return events

    # ------------------------------------------------------------------ #
    # Problem 5 contract: an error event is terminal (no `done` follows). #
    # ------------------------------------------------------------------ #

    @patch("api.endpoints.chat.get_ask_handler")
    def test_llm_error_is_terminal(self, mock_get_ask_handler):
        """When the handler raises LLMError the stream ends on error.

        The SSE body must contain an ``{"error": ...}`` event and must NEVER
        contain a ``done`` event after it (the generator returns right after
        yielding the error).
        """
        from core.exceptions import LLMError

        handler = MagicMock()
        handler.handle_stream_sync.side_effect = LLMError("مفتاح API غير صالح")
        mock_get_ask_handler.return_value = handler

        body = self._post_stream()
        events = self._sse_events(body)

        self.assertTrue(events, "must emit at least one SSE event")
        # The error is emitted in-band and terminal...
        last = events[-1]
        self.assertIn("error", last)
        # ...and there is NO `done` any time after the error.
        self.assertFalse(
            any("done" in e for e in events),
            "an error event must be terminal: no `done` may follow",
        )

    @patch("api.endpoints.chat.get_ask_handler")
    def test_error_then_end_of_stream(self, mock_get_ask_handler):
        """After a terminal error, the generator yields nothing more.

        The contract the frontend relies on: error === end of stream. There is
        no conversation_id, no chunk, no sources, and no done after it.
        """
        from core.exceptions import VectorStoreError

        handler = MagicMock()
        handler.handle_stream_sync.side_effect = VectorStoreError("المتجهات غير مهيأة")
        mock_get_ask_handler.return_value = handler

        body = self._post_stream()
        events = self._sse_events(body)

        self.assertTrue(events)
        self.assertTrue(all("error" in e for e in events))
        # Frontend stops collecting on the *first* error event; assert the
        # stream does not append sources after an error ever.
        for e in events:
            self.assertNotIn("sources", e)
