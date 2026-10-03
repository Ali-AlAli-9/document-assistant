import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'rag_project.settings'

import django
django.setup()

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache_between_tests():
    """Deterministic rate limiting across the whole suite.

    The RateLimitMiddleware counts requests in the in-process locmem cache,
    and that cache is NOT automatically cleared between django test methods in
    this setup. Without this, per-IP counters (e.g. 5/min for registration)
    would accumulate across tests and cause false 429s.
    """
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _isolate_vector_store_writer_lock(tmp_path, monkeypatch):
    """Keep the suite out of the real writer lock.

    The write paths now take a cross-process lock whose file lives next to the
    Chroma database. Left alone, a test that uploads would write into the
    developer's real ``chroma_db`` — and, worse, block a real dev server that
    happens to be running: the test would get a spurious 409 and the server a
    spurious refusal. Every test therefore locks inside its own tmp dir.
    """
    from infrastructure.vector_store import writer_lock

    path = tmp_path / 'vector-store' / '.writer.lock'
    monkeypatch.setattr(writer_lock, '_resolve_lock_path', lambda: path)
