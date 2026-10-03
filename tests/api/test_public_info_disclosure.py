"""Two endpoints answer without a session, because the platform and the setup
wizard need them. Neither may hand an anonymous caller a raw exception string.

Why this file exists (audit finding, MEDIUM):

    ``application.di`` captures the embedder failure as ``str(exc)`` and stores
    it in ``get_embedder_status()['error']``. Both ``/api/health/`` and
    ``/api/status/`` were serialising that dict back to whoever asked, and both
    routes sit outside the authenticated Ninja router — so an unauthenticated
    visitor could read whatever the model loader put in its exception message:
    absolute filesystem paths, model/cache locations, library versions. That is
    reconnaissance, not a breach, but none of it belongs in a public response.

The contract pinned here:

1. ``/api/health/`` (which Render polls) exposes state only — never ``error``.
2. ``/api/status/`` keeps the same JSON shape for anonymous and logged-in
    callers, so the frontend's polling needs no change, but an anonymous
    caller never receives ``error``.
3. A logged-in user still gets the full detail, because the setup wizard is
    where a failure reason is actually useful.
"""

import pytest
from django.contrib.auth.models import User
from django.test import Client

from application import di


class _Boom(Exception):
    def __str__(self):
        return (
            "No such file or directory: '/opt/render/project/src/venv/"
            "lib/python3.11/site-packages/sentence_transformers/models/"
            "all-MiniLM-L6-v2' while resolving the local cache"
        )


LEAKY = str(_Boom())


def _login(client, username='diag'):
    """force_login, not a POST to /api/auth/login: the subject here is the
    response payload, and the login endpoint's rate limit is a process-wide
    cache shared with every other test file."""
    user = User.objects.create_user(username=username, password='StrongPass!123')
    client.force_login(user)
    return user


@pytest.fixture
def embedder_in_error(monkeypatch):
    """Put the process in exactly the state the leak needs: model failed, and a
    raw exception string parked in the status dict."""
    monkeypatch.setattr(di, '_embedder_status', {
        'state': 'error',
        'error': LEAKY,
    })


@pytest.mark.django_db
class TestHealthEndpoint:
    def test_anonymous_caller_never_receives_the_error_string(self, client, embedder_in_error):
        resp = client.get('/api/health/')

        # Render only needs the status code, so nothing here requires auth.
        assert resp.status_code == 200
        assert LEAKY not in resp.content.decode()

    def test_the_public_payload_is_minimal(self, client, embedder_in_error):
        body = client.get('/api/health/').json()

        assert 'error' not in body
        assert 'embedder' not in body
        assert body['embedder_state'] == 'error'
        assert body['database'] in ('ok', 'error')


@pytest.mark.django_db
class TestStatusEndpoint:
    def test_anonymous_caller_never_receives_the_error_string(self, client, embedder_in_error):
        resp = client.get('/api/status/')

        assert resp.status_code == 200
        assert LEAKY not in resp.content.decode()

    def test_shape_is_identical_for_both_callers(self, client, embedder_in_error):
        """The frontend polls this before login and reads ``embedder.state``;
        a 401 or a changed shape would break the setup wizard for a visitor
        who has not signed in yet."""
        anonymous = client.get('/api/status/')

        _login(client)
        authenticated = client.get('/api/status/')

        assert set(anonymous.json()) == set(authenticated.json()) == {'embedder'}
        assert 'state' in anonymous.json()['embedder']

    def test_logged_in_user_still_gets_the_failure_reason(self, client, embedder_in_error):
        _login(client)

        body = client.get('/api/status/').json()

        assert body['embedder']['error'] == LEAKY


@pytest.mark.django_db
def test_health_stays_reachable_for_the_platform(client):
    """Guard against over-correcting: /api/health/ must never acquire auth,
    because the platform's health check would then restart-loop the service."""
    assert Client().get('/api/health/').status_code == 200
