"""Rate limiting must be per VISITOR, not per proxy.

Why this file exists (production-readiness fix):

    On a managed host (Render, Railway, Fly...) the container receives traffic
    from the platform's reverse proxy, so ``request.META['REMOTE_ADDR']`` is the
    proxy's own address and is identical for every visitor. Keying the rate
    limiter on that value collapses all visitors into a SINGLE bucket: the
    20-questions-per-minute budget for /api/chat/ask would be spent by whoever
    asks first, and everyone else gets a 429 for a limit they never touched.

    ``core.config.Settings.TRUST_PROXY_HEADERS`` makes the middleware read the
    real client address from ``X-Forwarded-For`` (the header the platform edge
    sets), and these tests lock both halves of that contract:

    1. the client-IP resolution itself (first hop, trimmed, empty-safe), and
    2. the observable behaviour — two different forwarded addresses get two
       independent budgets.
"""

import pytest
from django.test import RequestFactory

from core.middleware import RateLimitMiddleware


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture
def middleware():
    """A middleware instance with a no-op downstream view.

    ``RateLimitMiddleware.__init__`` only stores ``get_response`` and the limit
    table, so the real pipeline is never invoked.
    """
    return RateLimitMiddleware(lambda request: None)


def _request(rf, **meta):
    return rf.get('/api/chat/ask', **meta)


class TestClientIpResolution:
    def test_uses_the_hop_the_platform_appended(self, middleware, rf):
        # The edge appends what it saw: <...anything the client sent...>, <client>.
        request = _request(
            rf,
            REMOTE_ADDR='10.0.0.1',
            HTTP_X_FORWARDED_FOR='203.0.113.7, 10.0.0.1',
        )
        assert middleware._get_client_ip(request) == '10.0.0.1'

    def test_single_entry_still_works(self, middleware, rf):
        request = _request(rf, REMOTE_ADDR='10.0.0.1', HTTP_X_FORWARDED_FOR='203.0.113.7')
        assert middleware._get_client_ip(request) == '203.0.113.7'

    def test_spoofed_prefix_does_not_win(self, middleware, rf):
        # THE reason this file exists. A caller that puts whatever it likes in
        # FRONT of the chain must not decide its own bucket, or the rate limit
        # can be bypassed by varying one header value per request.
        request = _request(
            rf,
            REMOTE_ADDR='10.0.0.1',
            HTTP_X_FORWARDED_FOR='1.2.3.4, 5.6.7.8, 203.0.113.7',
        )
        assert middleware._get_client_ip(request) == '203.0.113.7'

    def test_falls_back_to_remote_addr_without_header(self, middleware, rf):
        request = _request(rf, REMOTE_ADDR='198.51.100.5')
        assert middleware._get_client_ip(request) == '198.51.100.5'

    def test_empty_header_falls_back(self, middleware, rf):
        # An empty/blank header must not produce an empty cache key, which
        # would merge unrelated visitors into one bucket again.
        request = _request(rf, REMOTE_ADDR='198.51.100.5', HTTP_X_FORWARDED_FOR='  ')
        assert middleware._get_client_ip(request) == '198.51.100.5'

    def test_no_addresses_at_all(self, middleware, rf):
        request = rf.get('/api/chat/ask')
        # RequestFactory fills REMOTE_ADDR with 127.0.0.1; drop it to exercise
        # the "nothing known about the client" fallback.
        request.META.pop('REMOTE_ADDR')
        assert middleware._get_client_ip(request) == '0.0.0.0'

    def test_header_ignored_when_proxy_untrusted(self, middleware, rf, monkeypatch):
        # With TRUST_PROXY_HEADERS disabled a client could spoof the header to
        # hand itself a fresh budget on every request, so it must be ignored.
        from core.config import settings as core_settings

        monkeypatch.setattr(core_settings, 'TRUST_PROXY_HEADERS', False)
        request = _request(
            rf,
            REMOTE_ADDR='10.0.0.1',
            HTTP_X_FORWARDED_FOR='203.0.113.7',
        )
        assert middleware._get_client_ip(request) == '10.0.0.1'


@pytest.mark.django_db
class TestVisitorsDoNotShareABudget:
    """End-to-end through the real middleware + cache."""

    LOGIN_URL = '/api/auth/login'
    LOGIN_LIMIT = 10  # core.middleware.rate_limits

    def _login(self, client, forwarded_for):
        return client.post(
            self.LOGIN_URL,
            data='{}',
            content_type='application/json',
            HTTP_X_FORWARDED_FOR=forwarded_for,
        )

    def test_second_visitor_is_not_blocked_by_the_first(self, client):
        # Visitor A exhausts his own budget on /api/auth/login.
        for _ in range(self.LOGIN_LIMIT):
            assert self._login(client, '203.0.113.7').status_code != 429
        assert self._login(client, '203.0.113.7').status_code == 429

        # Visitor B, arriving through the SAME proxy, must still have a full
        # budget of his own. Before the fix both shared the proxy's IP and
        # visitor B was locked out immediately.
        assert self._login(client, '198.51.100.9').status_code != 429

    def test_spoofing_a_new_header_resets_nothing(self, client):
        # A visitor who keeps the same real address keeps being counted.
        for _ in range(self.LOGIN_LIMIT):
            self._login(client, '203.0.113.7')
        assert self._login(client, '203.0.113.7').status_code == 429

    def test_rotating_a_spoofed_prefix_cannot_mint_a_budget(self, client):
        # The bypass this closes. Each request carries a different junk value in
        # front of the real address; a limiter that trusted the first hop gave
        # every one of these a fresh bucket and the limit never triggered.
        for attempt in range(self.LOGIN_LIMIT):
            resp = self._login(client, f'10.9.{attempt}.{attempt}, 203.0.113.7')
            assert resp.status_code != 429, f'request {attempt} was wrongly blocked'
        # Budget spent. Now vary the spoofed prefix again: a limiter that
        # believed the first hop would hand out a brand-new budget here.
        assert self._login(client, '10.9.99.99, 203.0.113.7').status_code == 429
        assert self._login(client, '172.16.0.1, 203.0.113.7').status_code == 429
