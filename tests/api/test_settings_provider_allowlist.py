"""The provider name is a dispatcher, not free text.

Why this file exists (audit finding, MEDIUM):

    ``application.di.get_llm(provider, ...)`` is a factory: anything that is not
    ``gemini`` falls through to the Ollama adapter. Its base URL comes from
    settings, not from the request, so this was never an SSRF — but
    ``validate_api_key`` took ``provider`` straight from the payload and used it
    as the cache identity of the client it built. A caller could therefore post
    ten junk provider strings, get ten cache entries built, and have the LRU
    evict every real visitor's cached client.

    ``save_settings`` already enforced the allowlist; this endpoint did not. The
    contract pinned here is that both paths enforce the same list, and that a
    rejected value never reaches the factory.
"""

import json

import pytest
from django.contrib.auth.models import User

from api.endpoints.settings import PROVIDER_CHOICES

JUNK_PROVIDERS = [
    'http://169.254.169.254/latest/meta-data/',
    'https://attacker.example/steal',
    'ollama\nGEMINI',
    ' gemini ',  # un-normalised: the allowlist is strict, not forgiving
    'GEMINI',
    'gemini;ollama',
    'gemini ollama',
    'ollama\n',
]

# Deliberately NOT shaped like a credential. A placeholder that starts with a
# real provider prefix is a trap for the next person who greps for leaked keys.
FAKE_KEY = 'not-a-real-key'


class _Ok:
    def generate(self, prompt):
        return 'ok'


@pytest.fixture
def authed(client):
    user = User.objects.create_user(username='p', password='StrongPass!123')
    client.force_login(user)
    return user


def _validate(client, provider):
    return client.post(
        '/api/settings/validate-key',
        data=json.dumps({'api_key': FAKE_KEY, 'provider': provider}),
        content_type='application/json',
    )


@pytest.mark.django_db
class TestValidateKeyProviderAllowlist:
    @pytest.mark.parametrize('provider', JUNK_PROVIDERS)
    def test_junk_provider_is_rejected(self, client, authed, provider):
        resp = _validate(client, provider)

        assert resp.status_code == 422, resp.content
        assert 'غير مدعوم' in resp.json().get('detail', '')

    def test_a_rejected_value_never_reaches_the_factory(self, client, authed, monkeypatch):
        """The point of the allowlist: nothing caller-controlled becomes a
        provider identity, so a URL can never end up anywhere near a client."""
        from application import di

        called_with = []

        def _record(provider=None, api_key=None):
            called_with.append(provider)
            raise AssertionError(f'factory called with {provider!r}')

        monkeypatch.setattr(di, 'get_llm', _record)

        resp = _validate(client, 'http://169.254.169.254/latest/meta-data/')

        assert resp.status_code == 422
        assert called_with == []

    @pytest.mark.parametrize('provider', PROVIDER_CHOICES)
    def test_supported_providers_still_reach_the_factory(
        self, client, authed, monkeypatch, provider,
    ):
        from application import di

        seen = []
        monkeypatch.setattr(
            di, 'get_llm',
            lambda provider=None, api_key=None: seen.append(provider) or _Ok(),
        )

        resp = _validate(client, provider)

        assert resp.status_code == 200
        assert resp.json()['valid'] is True
        assert seen == [provider]

    @pytest.mark.parametrize('payload', [{}, {'provider': ''}], ids=['omitted', 'empty'])
    def test_a_blank_provider_falls_back_to_the_default(
        self, client, authed, monkeypatch, payload,
    ):
        """``provider or "gemini"`` means a blank value is "unspecified", not
        "junk" — so it is the default that must reach the factory, and it must
        not be counted against the allowlist rejections."""
        from application import di

        seen = []
        monkeypatch.setattr(
            di, 'get_llm',
            lambda provider=None, api_key=None: seen.append(provider) or _Ok(),
        )

        body = {'api_key': FAKE_KEY, **payload}
        resp = client.post(
            '/api/settings/validate-key',
            data=json.dumps(body),
            content_type='application/json',
        )

        assert resp.status_code == 200
        assert seen == ['gemini']


@pytest.mark.django_db
class TestBothEndpointsShareOneList:
    def test_save_settings_and_validate_key_agree(self, client, authed, monkeypatch):
        from application import di

        # A provider the save endpoint accepts must not be rejected by the
        # validate endpoint, and vice versa — otherwise the UI accepts a value
        # the server refuses on the next call.
        saved = client.post(
            '/api/settings',
            data=json.dumps({'provider': 'ollama'}),
            content_type='application/json',
        )
        assert saved.status_code == 200, saved.content

        assert PROVIDER_CHOICES == ('gemini', 'ollama')

        monkeypatch.setattr(di, 'get_llm', lambda provider=None, api_key=None: _Ok())
        for provider in PROVIDER_CHOICES:
            assert _validate(client, provider).status_code == 200
        assert _validate(client, 'not-a-provider').status_code == 422
