"""GeminiEmbeddingAdapter: request shape, retry/backoff, error wrapping.

All HTTP traffic is routed through httpx.MockTransport, so these tests never
touch the network or spend API credits. All non-ASCII fixtures are written as
\\u escapes: the write path on this host has mangled literal Arabic before.
"""
import json
import time

import httpx
import pytest

from infrastructure.embedding.gemini_embedding_adapter import (
    GeminiEmbeddingAdapter,
)
from domain.ports.embedding import EmbeddingPort

MODEL = 'gemini-embedding-001'
KEY = 'test-key'
DIM = 768

# 'النص الأول' and 'النص الثاني' and 'سؤال', as escapes (ASCII-safe source)
TEXT1 = '\u0627\u0644\u0646\u0635 \u0627\u0644\u0623\u0648\u0644'
TEXT2 = '\u0627\u0644\u0646\u0635 \u0627\u0644\u062b\u0627\u0646\u064a'
QUERY = '\u0633\u0624\u0627\u0644'


def make_adapter(handler, **kwargs) -> GeminiEmbeddingAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(
        transport=transport,
        base_url='https://generativelanguage.googleapis.com/v1beta',
    )
    return GeminiEmbeddingAdapter(
        api_key=KEY,
        model=MODEL,
        dimension=DIM,
        client=client,
        **kwargs,
    )


def _ok_values(n=DIM):
    return [0.1 * (i % 7) for i in range(n)]


class TestContract:
    def test_it_is_an_embedding_port(self):
        for name in ('encode', 'encode_query', 'dimension'):
            assert hasattr(GeminiEmbeddingAdapter, name), name
        assert issubclass(GeminiEmbeddingAdapter, EmbeddingPort)

    def test_dimension_property_returns_configured_value(self):
        adapter = make_adapter(lambda req: httpx.Response(200, json={}))
        assert adapter.dimension == DIM

    def test_empty_api_key_is_rejected_at_construction(self):
        with pytest.raises(ValueError, match='GEMINI_API_KEY'):
            GeminiEmbeddingAdapter(api_key='', model=MODEL, dimension=DIM)

    def test_invalid_dimension_is_rejected(self):
        with pytest.raises(ValueError, match='dimension'):
            GeminiEmbeddingAdapter(api_key=KEY, model=MODEL, dimension=5000)


class TestRequestShape:
    def test_encode_sends_embed_content_with_expected_body(self):
        captured = {'bodies': []}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['url'] = str(request.url)
            captured['auth'] = request.headers.get('x-goog-api-key')
            captured['bodies'].append(request.read().decode())
            return httpx.Response(200, json={'embedding': {'values': _ok_values()}})

        adapter = make_adapter(handler)
        out = adapter.encode([TEXT1, TEXT2])
        assert len(out) == 2
        assert len(out[0]) == DIM
        assert captured['auth'] == KEY
        assert '/models/gemini-embedding-001:embedContent' in captured['url']
        assert len(captured['bodies']) == 2
        for sent, expected in zip(captured['bodies'], [TEXT1, TEXT2]):
            body = json.loads(sent)
            assert body['outputDimensionality'] == DIM
            assert body['content']['parts'][0]['text'] == expected

    def test_encode_query_returns_a_single_vector(self):
        adapter = make_adapter(
            lambda req: httpx.Response(200, json={'embedding': {'values': _ok_values()}})
        )
        v = adapter.encode_query(QUERY)
        assert len(v) == DIM


class TestFailureHandling:
    def test_retries_429_then_succeeds(self, monkeypatch):
        calls = {'n': 0}
        sleeps = []
        monkeypatch.setattr(time, 'sleep', sleeps.append)

        def handler(request: httpx.Request) -> httpx.Response:
            calls['n'] += 1
            if calls['n'] == 1:
                # Retry-After is authoritative and must win over the backoff.
                return httpx.Response(429, json={}, headers={'retry-after': '5'})
            return httpx.Response(200, json={'embedding': {'values': _ok_values()}})

        adapter = make_adapter(handler)
        out = adapter.encode_query(QUERY)
        assert len(out) == DIM
        assert calls['n'] == 2
        assert sleeps == [5], f'expected Retry-After to win, got {sleeps}'

    def test_retries_5xx_then_succeeds(self, monkeypatch):
        calls = {'n': 0}
        monkeypatch.setattr(time, 'sleep', lambda s: None)

        def handler(request: httpx.Request) -> httpx.Response:
            calls['n'] += 1
            if calls['n'] == 1:
                return httpx.Response(503, json={})
            return httpx.Response(200, json={'embedding': {'values': _ok_values()}})

        adapter = make_adapter(handler)
        out = adapter.encode_query(QUERY)
        assert len(out) == DIM
        assert calls['n'] == 2

    def test_raises_after_repeated_429s(self, monkeypatch):
        calls = {'n': 0}
        monkeypatch.setattr(time, 'sleep', lambda s: None)

        def handler(request: httpx.Request) -> httpx.Response:
            calls['n'] += 1
            return httpx.Response(429, json={})

        adapter = make_adapter(handler, max_attempts=3)
        with pytest.raises(RuntimeError, match='Gemini embedContent failed'):
            adapter.encode_query(QUERY)
        assert calls['n'] == 3

    def test_reports_table_rate_limit_not_none(self, monkeypatch):
        monkeypatch.setattr(time, 'sleep', lambda s: None)
        adapter = make_adapter(lambda req: httpx.Response(429, json={}), max_attempts=2)
        with pytest.raises(RuntimeError, match='Gemini embedContent failed: retries exhausted'):
            adapter.encode_query(QUERY)

    def test_wraps_server_error_in_runtime_error(self, monkeypatch):
        monkeypatch.setattr(time, 'sleep', lambda s: None)
        # A persistent 5xx must exhaust the retries, not hang and not lie.
        adapter = make_adapter(lambda req: httpx.Response(500, json={}), max_attempts=2)
        with pytest.raises(RuntimeError, match='Gemini embedContent failed'):
            adapter.encode_query(QUERY)

    def test_wraps_network_error_in_runtime_error(self):
        def handler(request: httpx.Request):
            raise httpx.ConnectError('boom')

        adapter = make_adapter(handler)
        with pytest.raises(RuntimeError):
            adapter.encode_query(QUERY)

    def test_does_not_leak_the_api_key_in_errors(self, monkeypatch):
        # A persistent 500 now retries with real backoff (2+4+8+16+30+30s);
        # the async sleep must be neutralised so the test stays instant.
        monkeypatch.setattr(time, 'sleep', lambda s: None)
        adapter = make_adapter(lambda req: httpx.Response(500, json={}))

        with pytest.raises(RuntimeError) as exc:
            adapter.encode_query(QUERY)
        assert KEY not in str(exc.value)