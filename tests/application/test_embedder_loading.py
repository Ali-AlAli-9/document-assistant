"""The embedding model is built once, warmed up, and never blocks the health check.

Three properties, each of which is invisible in a passing request/response test:

1. ONE construction. The construction used to happen outside the lock, so two
   callers arriving together each built a full copy of the model. On a 512MB
   instance that is two weight sets and two torch runtimes.
2. The health endpoint stays responsive during that construction. Holding a
   single lock across a 13-second model build would answer /api/health/ only
   after the build — and Render's own probe would then call the service
   unhealthy. Fixing (1) with one lock would have introduced this, so it is
   pinned here even though the old code passed it for the wrong reason.
3. A failed warm-up is not a failure. The model is loaded and usable; only the
   first request loses its head start.

The thread tests use Events and a Barrier rather than sleeps, so they assert a
race that either happens or does not — not a race that usually does.
"""

import sys
import threading
import time
import types
from unittest.mock import MagicMock

import pytest

from application import di
from domain.ports.embedding import EmbeddingPort

ADAPTER_MODULE = 'infrastructure.embedding.sentence_transformer_adapter'


@pytest.fixture(autouse=True)
def reset_embedder_state(monkeypatch):
    """Never let a cached model or a stale status leak between tests.

    Restoring the previous value matters: other packages resolve the embedder
    through this module, and a model cached here would silently satisfy them.

    This suite pins EMBEDDING_PROVIDER to 'local': it tests the local loader's
    lifecycle (single construction, health responsiveness, warm-up). A
    developer's .env with EMBEDDING_PROVIDER=gemini would otherwise redirect
    construction to the API adapter and make every assertion here vacuous.
    """
    saved_instance = di._embedder_instance
    saved_status = di.get_embedder_status()
    monkeypatch.setattr(di.settings, 'EMBEDDING_PROVIDER', 'local')
    di._embedder_instance = None
    di._set_embedder_status('not_loaded')
    yield
    di._embedder_instance = saved_instance
    di._set_embedder_status(saved_status['state'], saved_status['error'])


def _fake_embedder() -> EmbeddingPort:
    embedder = MagicMock(spec=EmbeddingPort)
    embedder.encode.return_value = [[0.1, 0.2, 0.3]]
    embedder.dimension = 3
    return embedder


def _install(monkeypatch, factory) -> list:
    calls: list = []

    def _construct(**kwargs):
        calls.append(kwargs)
        return factory()

    # A stand-in MODULE, not a patched attribute. ``get_embedder`` performs its
    # import inside the function body, and the real adapter module costs 62s to
    # import (it pulls in sentence_transformers, and with it torch) — a price
    # a test about lazy loading has no business paying. The first version of
    # this file did pay it, and the suite read as a 166-second hang. Injecting
    # into sys.modules is also what makes the fake win regardless of what an
    # earlier test already imported.
    module = types.ModuleType(ADAPTER_MODULE)
    module.SentenceTransformerAdapter = _construct
    monkeypatch.setitem(sys.modules, ADAPTER_MODULE, module)
    return calls


class TestOneConstruction:
    def test_callers_arriving_together_share_a_single_model(self, monkeypatch):
        """The defect: both threads saw ``None`` and both built a model."""
        barrier = threading.Barrier(8)

        def slow_factory():
            time.sleep(0.3)
            return _fake_embedder()

        calls = _install(monkeypatch, slow_factory)
        results: list = []
        errors: list = []

        def worker():
            try:
                barrier.wait(timeout=10)
                results.append(di.get_embedder())
            except Exception as exc:  # noqa: BLE001 - failures must be visible
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert errors == []
        assert len(results) == 8
        assert len(calls) == 1, f'the model was built {len(calls)} times'
        assert all(result is results[0] for result in results)

    def test_a_second_call_after_the_first_reuses_it(self, monkeypatch):
        calls = _install(monkeypatch, _fake_embedder)
        assert di.get_embedder() is di.get_embedder()
        assert len(calls) == 1


class TestHealthStaysResponsive:
    def test_status_is_readable_while_a_load_is_in_progress(self, monkeypatch):
        entered, release = threading.Event(), threading.Event()

        def blocking_factory():
            entered.set()
            assert release.wait(timeout=10), 'the test never released the loader'
            return _fake_embedder()

        _install(monkeypatch, blocking_factory)
        loader = threading.Thread(target=di.get_embedder, daemon=True)
        loader.start()
        try:
            assert entered.wait(timeout=10), 'the loader never started'
            started = time.perf_counter()
            status = di.get_embedder_status()
            elapsed = time.perf_counter() - started
            assert elapsed < 0.5, f'get_embedder_status() blocked {elapsed:.1f}s'
            assert status['state'] == 'loading'
        finally:
            release.set()
            loader.join(timeout=10)

    def test_a_failed_load_is_reported_and_not_cached(self, monkeypatch):
        _install(monkeypatch, MagicMock(side_effect=OSError('no such model dir')))
        with pytest.raises(OSError):
            di.get_embedder()
        assert di.get_embedder_status()['state'] == 'error'
        assert di._embedder_instance is None, 'a broken model must not be cached'

        calls = _install(monkeypatch, _fake_embedder)
        di.get_embedder()
        assert len(calls) == 1, 'the retry after a failure did not actually build'
        assert di.get_embedder_status()['state'] == 'ready'


class TestWarmUp:
    def test_preload_warms_the_model_and_leaves_it_ready(self, monkeypatch):
        embedder = _fake_embedder()
        _install(monkeypatch, lambda: embedder)

        di.preload_embedder()

        embedder.encode.assert_called_once_with(['warmup'])
        status = di.get_embedder_status()
        assert status['state'] == 'ready'
        assert status['error'] == ''

    def test_a_failed_warmup_leaves_the_model_usable(self, monkeypatch):
        """The warm-up is an optimisation, so its failure must not raise the
        state to 'error' — the model IS loaded and will encode on demand."""
        embedder = _fake_embedder()
        embedder.encode.side_effect = RuntimeError('warm-up kernel unavailable')
        _install(monkeypatch, lambda: embedder)

        di.preload_embedder()  # must not raise

        status = di.get_embedder_status()
        assert status['state'] == 'ready', status
        assert embedder.encode.call_count == 1

    def test_a_failed_load_leaves_the_preload_quiet(self, monkeypatch):
        _install(monkeypatch, MagicMock(side_effect=OSError('disk gone')))
        di.preload_embedder()  # must not raise
        assert di.get_embedder_status()['state'] == 'error'


class TestGeminiProviderBranch:
    def test_gemini_provider_builds_the_api_adapter_without_io(self, monkeypatch):
        """The gemini branch must construct GeminiEmbeddingAdapter cheaply —
        no weights, no network at build time — and report ready. The key is a
        dummy, never the developer's real one."""
        from infrastructure.embedding.gemini_embedding_adapter import (
            GeminiEmbeddingAdapter,
        )

        monkeypatch.setattr(di.settings, 'EMBEDDING_PROVIDER', 'gemini')
        monkeypatch.setattr(di.settings, 'GEMINI_API_KEY', 'test-key')
        adapter = di.get_embedder()
        assert isinstance(adapter, GeminiEmbeddingAdapter)
        assert di.get_embedder_status()['state'] == 'ready'

    def test_gemini_provider_preload_stays_quiet(self, monkeypatch):
        """preload_embedder must not fire an API warm-up call for gemini
        (a boot-time network dependency). It should construct cheaply and
        return without touching the adapter's encode."""
        from infrastructure.embedding.gemini_embedding_adapter import (
            GeminiEmbeddingAdapter,
        )
        import unittest.mock as mock

        monkeypatch.setattr(di.settings, 'EMBEDDING_PROVIDER', 'gemini')
        monkeypatch.setattr(di.settings, 'GEMINI_API_KEY', 'test-key')
        with mock.patch.object(
            GeminiEmbeddingAdapter, 'encode', side_effect=AssertionError('must not be called')
        ):
            di.preload_embedder()  # must not raise
        assert di.get_embedder_status()['state'] == 'ready'
