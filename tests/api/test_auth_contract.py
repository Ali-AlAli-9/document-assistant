"""PROBLEM 7 — MANDATORY REGISTRATION + PER-ACCOUNT LLM SETTINGS CONTRACT.

What this file locks (the product decision the user approved):

1. REGISTRATION IS MANDATORY:
   Every assistant feature (chat, documents, settings) is gated behind a
   logged-in Django session. Unauthenticated requests to those routers get
   ``401``. The ONLY public endpoints are ``/api/auth/register`` and
   ``/api/auth/login`` (plus csrf/health/status which carry no data).

2. THE USER BRINGS HIS OWN GEMINI KEY (the app does NOT issue API keys):
   Each registered user stores his OWN Gemini key INSIDE his account on the
   server. The API never echoes the key back — only ``has_gemini_key`` is
   reported, and the key works because the backend reads it from the account
   when serving ``/api/chat/ask`` / ``/api/chat/stream``. Ollama (local, no
   key) remains an available provider option.

3. SAFE / NON-LEAKING AUTH ERRORS:
   Register/login failures return generic Arabic messages only — never raw
   exceptions, tracebacks, validator English hints, or any technical detail.

4. RATE LIMITING applies to register/login (5/min and 10/min per IP).
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from api.models import UserProfile


class AuthContractTests(TestCase):
    REGISTER_URL = "/api/auth/register"
    LOGIN_URL = "/api/auth/login"
    LOGOUT_URL = "/api/auth/logout"
    ME_URL = "/api/auth/me"

    # ------------------------------------------------------------------ #
    # helpers                                                             #
    # ------------------------------------------------------------------ #

    def _register(self, username="user1", password="StrongPass!123", confirm=None):
        return self.client.post(
            self.REGISTER_URL,
            data=json.dumps({
                "username": username,
                "password": password,
                "password_confirm": confirm if confirm is not None else password,
            }),
            content_type="application/json",
        )

    def _login(self, username="user1", password="StrongPass!123"):
        return self.client.post(
            self.LOGIN_URL,
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
        )

    def _create_user(self, username="existing", password="StrongPass!123"):
        user = User.objects.create_user(username=username, password=password)
        UserProfile.objects.create(user=user)
        return user

    # ------------------------------------------------------------------ #
    # 1) Regular registration + auto-login                                #
    # ------------------------------------------------------------------ #

    def test_register_creates_account_profile_and_auto_logs_in(self):
        resp = self._register()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["username"], "user1")
        self.assertIs(body["has_gemini_key"], False)
        self.assertIsNone(body["llm_provider"])

        user = User.objects.get(username="user1")
        self.assertTrue(UserProfile.objects.filter(user=user).exists())
        self.assertEqual(user.profile.gemini_api_key, "")

        # auto-login: the session created during registration works for /me
        me = self.client.get(self.ME_URL)
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["username"], "user1")

    def test_register_duplicate_username_says_the_name_is_unavailable(self):
        """A duplicate must be actionable: tell the user the name is taken.

        Product decision — the earlier generic reply ("تعذّر إنشاء الحساب")
        left the user unable to tell WHICH input to fix, and they would retry
        the same password forever. The reply therefore states availability
        plainly. This knowingly allows probing which usernames exist; the
        test exists to pin the wording AND to make that trade-off explicit
        rather than let it arrive by accident.
        """
        from api.endpoints.auth import TAKEN_USERNAME_ERROR

        self.assertEqual(self._register("dupuser").status_code, 200)
        dup = self._register("dupuser")

        self.assertEqual(dup.status_code, 422)
        self.assertEqual(dup.json()["detail"], TAKEN_USERNAME_ERROR)
        # Actionable: it must say to change it.
        self.assertIn("غير متاح", dup.json()["detail"])

        # Even when it names the name, it must never name a PERSON or leak
        # anything beyond availability.
        for leak in ("Traceback", "IntegrityError", "مسجّل باسم", "by "):
            self.assertNotIn(leak, dup.content.decode())

    def test_real_fault_never_blames_the_username(self):
        """A server fault must not claim the name was taken.

        TAKEN_USERNAME_ERROR and SAFE_REGISTER_ERROR are separate on purpose:
        reusing one for both would make a genuine outage tell every user their
        perfectly good name was unavailable, sending them to change it forever.
        """
        from api.endpoints.auth import SAFE_REGISTER_ERROR, TAKEN_USERNAME_ERROR

        self.assertNotEqual(SAFE_REGISTER_ERROR, TAKEN_USERNAME_ERROR)
        self.assertNotIn("غير متاح", SAFE_REGISTER_ERROR)

        with patch("django.contrib.auth.models.User.objects.create_user",
                   side_effect=RuntimeError("boom")):
            resp = self._register("faultyuser")

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["detail"], SAFE_REGISTER_ERROR)
        self.assertNotIn("غير متاح", resp.json()["detail"])

    def test_register_short_username_rejected(self):
        resp = self._register("ab")
        self.assertEqual(resp.status_code, 422)
        self.assertIn("detail", resp.json())

    def test_register_weak_password_is_generic_arabic(self):
        resp = self._register(password="12345678")
        self.assertEqual(resp.status_code, 422)
        detail = resp.json()["detail"]
        self.assertIn("كلمة المرور ضعيفة", detail)
        # Never leak the English validator hint / policy internals.
        self.assertNotIn("This password", resp.content.decode())
        self.assertNotIn("MIN_LENGTH", resp.content.decode())

    def test_register_password_mismatch_rejected(self):
        resp = self._register(password="StrongPass!123", confirm="Different!456")
        self.assertEqual(resp.status_code, 422)
        self.assertIn("غير متطابقتين", resp.json()["detail"])

    # ------------------------------------------------------------------ #
    # 2) Login / me / logout                                              #
    # ------------------------------------------------------------------ #

    def test_login_wrong_password_is_generic_401(self):
        self._create_user("sam")
        resp = self._login("sam", "WrongPass!999")
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["detail"], "اسم المستخدم أو كلمة المرور غير صحيحة")
        body_text = resp.content.decode()
        self.assertNotIn("Traceback", body_text)
        self.assertNotIn("exception", body_text.lower())

    def test_login_success_then_me_then_logout(self):
        self._create_user("sam")
        login = self._login("sam")
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["username"], "sam")

        me = self.client.get(self.ME_URL)
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["username"], "sam")

        logout = self.client.post(self.LOGOUT_URL)
        self.assertEqual(logout.status_code, 200)

        me_after = self.client.get(self.ME_URL)
        self.assertEqual(me_after.status_code, 401)

    # ------------------------------------------------------------------ #
    # 3) Mandatory gating: everything else requires a session             #
    # ------------------------------------------------------------------ #

    def test_protected_endpoints_require_authentication(self):
        cases = [
            ("GET", "/api/documents"),
            ("POST", "/api/chat/ask"),
            ("POST", "/api/chat/stream"),
            ("GET", "/api/settings"),
            ("POST", "/api/settings"),
            ("POST", "/api/settings/validate-key"),
            ("GET", "/api/auth/me"),
            ("POST", "/api/auth/logout"),
        ]
        for method, url in cases:
            resp = getattr(self.client, method.lower())(url)
            self.assertEqual(
                resp.status_code, 401,
                f"{method} {url} must require authentication (got {resp.status_code})",
            )

    # ------------------------------------------------------------------ #
    # 4) Per-account Gemini key: stored server-side, never echoed back     #
    # ------------------------------------------------------------------ #

    def _register_and_save_gemini_key(self):
        self._register("guser")
        save = self.client.post(
            "/api/settings",
            data=json.dumps({"provider": "gemini", "gemini_api_key": "sk-SECRET-REAL-999"}),
            content_type="application/json",
        )
        self.assertEqual(save.status_code, 200)
        return save

    def test_account_key_is_saved_and_reported_without_echoing(self):
        save = self._register_and_save_gemini_key()
        self.assertIs(save.json()["has_gemini_key"], True)
        # The raw key must NEVER be echoed back to the client.
        self.assertNotIn("sk-SECRET-REAL-999", save.content.decode())

        settings = self.client.get("/api/settings")
        self.assertEqual(settings.status_code, 200)
        self.assertEqual(settings.json()["provider"], "gemini")
        self.assertIs(settings.json()["has_gemini_key"], True)
        self.assertNotIn("sk-SECRET-REAL-999", settings.content.decode())

        me = self.client.get(self.ME_URL)
        self.assertNotIn("sk-SECRET-REAL-999", me.content.decode())

    def test_bad_provider_value_rejected(self):
        self._register("u2user")
        resp = self.client.post(
            "/api/settings",
            data=json.dumps({"provider": "openai", "gemini_api_key": "x"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 422)

    def test_chat_ask_uses_the_account_stored_key(self):
        """The Gemini key flows from the ACCOUNT to the LLM call — the client
        does not send it per request anymore (server-side storage)."""
        self._register_and_save_gemini_key()

        handler = MagicMock()
        handler.handle.return_value = SimpleNamespace(
            answer="أهلاً بك",
            sources=[],
            conversation_id="conv-7",
        )
        with patch("api.endpoints.chat.get_ask_handler", return_value=handler) as mock_factory:
            resp = self.client.post(
                "/api/chat/ask",
                data=json.dumps({"question": "مرحبًا", "conversation_id": None}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["answer"], "أهلاً بك")

        # The provider + the account-stored key reached the handler factory.
        provider, api_key = mock_factory.call_args[0]
        self.assertEqual(provider, "gemini")
        self.assertEqual(api_key, "sk-SECRET-REAL-999")

    # ------------------------------------------------------------------ #
    # 5) Rate limiting on register                                        #
    # ------------------------------------------------------------------ #

    def test_register_is_rate_limited_per_ip(self):
        # Pin the clock so the 60s rate-limit window can NEVER expire between
        # requests. On a loaded machine 6 sequential HTTP posts could otherwise
        # take longer than the window and wrongly reset the counter (flaky).
        with patch("core.middleware.time.time", side_effect=lambda: 1000.0):
            for i in range(5):
                resp = self._register(f"limituser{i}")
                self.assertEqual(resp.status_code, 200, f"registration {i} should pass")
            sixth = self._register("limituser-final")
        self.assertEqual(sixth.status_code, 429)