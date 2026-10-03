"""Per-account LLM keys are ENCRYPTED at rest.

The contract this file pins:

1. ``encrypt_secret``/``decrypt_secret`` round-trip, and the ciphertext never
   contains the plaintext.
2. The key is derived from ``SECRET_KEY`` with HKDF-SHA256, so a *different*
   secret cannot decrypt a row written with another one, and tampering with the
   token is detected (Fernet authenticates).
3. A row written BEFORE encryption existed (no ``fernet:v1:`` prefix) is still
   readable, so existing databases keep working and get re-encrypted on the
   next save.
4. The API writes ciphertext to the database while the outbound LLM call still
   receives the real key — encryption is transparent to the feature.
5. Nothing echoes the key back to the client, and the admin never renders it.
"""

import json

import pytest
from django.contrib.auth.models import User

from api.models import UserProfile
from core.config import settings as core_settings
from core.crypto import (
    CIPHER_PREFIX,
    SecretDecryptionError,
    decrypt_secret,
    encrypt_secret,
    is_encrypted,
)

# Obviously fake, and deliberately NOT shaped like a Gemini key. A realistic
# `AIza...` placeholder would sit in the repository looking exactly like the leak
# this suite exists to prevent: the next audit greps for that prefix, finds a
# match, and can no longer trust its own result.
PLAIN = 'FAKE-GEMINI-KEY-NOT-REAL-0123456789'


class TestRoundTrip:
    def test_plaintext_never_appears_in_ciphertext(self):
        token = encrypt_secret(PLAIN)
        assert PLAIN not in token
        assert token.startswith(CIPHER_PREFIX)
        assert is_encrypted(token)

    def test_round_trip(self):
        assert decrypt_secret(encrypt_secret(PLAIN)) == PLAIN

    def test_ciphertext_differs_each_time(self):
        # Fernet uses a fresh IV per encryption, so two encryptions of the same
        # key must not be comparable by anyone reading the database.
        assert encrypt_secret(PLAIN) != encrypt_secret(PLAIN)

    def test_empty_stays_empty(self):
        # "no key" must stay distinguishable from "a key that decrypts to ''".
        assert encrypt_secret('') == ''
        assert decrypt_secret('') == ''

    def test_unicode_key(self):
        assert decrypt_secret(encrypt_secret('مفتاح-عربي-🔑')) == 'مفتاح-عربي-🔑'


class TestKeyDerivation:
    def test_another_secret_cannot_decrypt(self, monkeypatch):
        token = encrypt_secret(PLAIN)
        monkeypatch.setattr(core_settings, 'SECRET_KEY', 'a-completely-different-secret')
        with pytest.raises(SecretDecryptionError):
            decrypt_secret(token)

    def test_restoring_the_secret_decrypts_again(self, monkeypatch):
        original = core_settings.SECRET_KEY
        token = encrypt_secret(PLAIN)
        monkeypatch.setattr(core_settings, 'SECRET_KEY', 'rotated-away')
        with pytest.raises(SecretDecryptionError):
            decrypt_secret(token)
        monkeypatch.setattr(core_settings, 'SECRET_KEY', original)
        assert decrypt_secret(token) == PLAIN

    def test_tampered_token_is_rejected(self):
        token = encrypt_secret(PLAIN)
        body = token[len(CIPHER_PREFIX):]
        # Flip a character in the middle: still valid base64, different bytes,
        # so Fernet's authentication tag must reject it.
        middle = len(body) // 2
        flipped = 'A' if body[middle] != 'A' else 'B'
        tampered = body[:middle] + flipped + body[middle + 1:]
        with pytest.raises(SecretDecryptionError):
            decrypt_secret(CIPHER_PREFIX + tampered)

    def test_garbage_token_is_rejected_not_crashed(self):
        with pytest.raises(SecretDecryptionError):
            decrypt_secret(CIPHER_PREFIX + 'not-even-base64!!')


class TestLegacyPlaintextRows:
    def test_prefixless_row_is_returned_as_is(self):
        # A database written before encryption existed must keep working.
        assert decrypt_secret(PLAIN) == PLAIN
        assert is_encrypted(PLAIN) is False

    def test_legacy_row_is_re_encrypted_on_next_save(self):
        profile = UserProfile(llm_provider='gemini')
        profile.gemini_api_key = PLAIN          # legacy plaintext write
        assert profile.get_gemini_api_key() == PLAIN
        profile.set_gemini_api_key(profile.get_gemini_api_key())
        assert is_encrypted(profile.gemini_api_key)
        assert profile.get_gemini_api_key() == PLAIN


@pytest.mark.django_db
class TestProfileModel:
    def _profile(self, username='crypto-user'):
        user = User.objects.create_user(username=username, password='StrongPass!123')
        return UserProfile.objects.create(user=user, llm_provider='gemini')

    def test_column_never_holds_the_plaintext(self):
        profile = self._profile()
        profile.set_gemini_api_key(PLAIN)
        profile.save()
        stored = UserProfile.objects.get(pk=profile.pk).gemini_api_key
        assert PLAIN not in stored
        assert stored.startswith(CIPHER_PREFIX)

    def test_getter_returns_the_plaintext(self):
        profile = self._profile()
        profile.set_gemini_api_key(PLAIN)
        assert profile.get_gemini_api_key() == PLAIN

    def test_trailing_whitespace_is_trimmed_before_storing(self):
        profile = self._profile()
        profile.set_gemini_api_key(f'  {PLAIN}\n')
        assert profile.get_gemini_api_key() == PLAIN

    def test_has_gemini_key(self):
        profile = self._profile()
        assert profile.has_gemini_key() is False
        profile.set_gemini_api_key(PLAIN)
        assert profile.has_gemini_key() is True

    def test_saving_an_empty_key_clears_it(self):
        profile = self._profile()
        profile.set_gemini_api_key(PLAIN)
        profile.set_gemini_api_key('')
        assert profile.gemini_api_key == ''
        assert profile.has_gemini_key() is False


@pytest.mark.django_db
class TestApiSurface:
    """End-to-end through the real endpoints: encrypted on disk, usable live."""

    SETTINGS_URL = '/api/settings'
    KEY = 'sk-END-TO-END-REAL-999'

    @pytest.fixture(autouse=True)
    def _isolate_rate_limit(self):
        """Clear the rate-limit counters.

        The limiter counts registrations per IP in the Django cache, which is
        process-wide and therefore shared with every other test file. Without
        this, these three registrations can inherit a counter that
        ``test_auth_contract.py`` already pushed to its limit and be answered
        with 429 — a failure that depends on file order and timing.
        """
        from django.core.cache import cache

        cache.clear()
        yield
        cache.clear()

    def _register_and_save(self, client, username='e2e-user'):
        # `password_confirm` is a REQUIRED field on RegisterIn — omitting it is
        # a 422 from pydantic, not an authentication failure.
        client.post(
            '/api/auth/register',
            data=json.dumps({
                'username': username,
                'password': 'StrongPass!123',
                'password_confirm': 'StrongPass!123',
            }),
            content_type='application/json',
        )
        return client.post(
            self.SETTINGS_URL,
            data=json.dumps({'provider': 'gemini', 'gemini_api_key': self.KEY}),
            content_type='application/json',
        )

    def test_database_row_is_ciphertext(self, client):
        self._register_and_save(client)
        row = UserProfile.objects.get(user__username='e2e-user')
        assert row.gemini_api_key != self.KEY
        assert self.KEY not in row.gemini_api_key
        assert row.get_gemini_api_key() == self.KEY

    def test_oversized_key_is_rejected(self, client):
        client.post(
            '/api/auth/register',
            data=json.dumps({
                'username': 'bigkey',
                'password': 'StrongPass!123',
                'password_confirm': 'StrongPass!123',
            }),
            content_type='application/json',
        )
        resp = client.post(
            self.SETTINGS_URL,
            data=json.dumps({'provider': 'gemini', 'gemini_api_key': 'x' * 500}),
            content_type='application/json',
        )
        assert resp.status_code == 422

    def test_admin_never_renders_the_key(self, client):
        from api.admin import UserProfileAdmin

        self._register_and_save(client)
        row = UserProfile.objects.get(user__username='e2e-user')
        admin = UserProfileAdmin(UserProfile, None)
        # Neither the ciphertext nor the plaintext may be a form field.
        assert 'gemini_api_key' not in admin.fields
        masked = admin.key_masked(row)
        assert self.KEY not in masked
        assert masked != row.gemini_api_key
