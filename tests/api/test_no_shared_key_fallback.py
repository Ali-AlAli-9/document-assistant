"""A chat must never run on a key the ACCOUNT does not own.

The bug this pins
----------------
``application.di.get_llm`` used to build its Gemini client from
``api_key or settings.GEMINI_API_KEY``. One expression, two credentials:

* ``settings.GEMINI_API_KEY`` is the OPERATOR's credential, and it has a
  legitimate job -- it is what pays for embeddings when
  ``EMBEDDING_PROVIDER=gemini``. Funding your own indexing is a choice.
* The account's key is the USER's credential, supplied through the settings
  panel and encrypted at rest.

As an LLM fallback the second collapsed into the first: a visitor who never
entered a key spent the operator's quota. No error, no prompt, and nothing in
the response to say whose account had paid for the answer. Every unregistered
visitor of a public deployment was a free donation.

What is pinned here
-------------------
1. The factory refuses a keyless Gemini call instead of substituting.
2. It refuses even when ``settings.GEMINI_API_KEY`` IS set -- the mutation that
   re-adds the fallback fails THIS test, not some downstream one.
3. The refusal reaches the client as 401 with the one agreed sentence, never as
   5xx and never with the operator key echoed.
4. ``settings.GEMINI_API_KEY`` keeps its embeddings job, so the fix removed a
   fallback and not a feature.
5. The sentence survives frontend/src/api/errors.js: some rule claims it, and
   the FIRST rule to claim it is a passthrough, so neither a broader rule nor the
   generic 401 string ("session expired") can render instead.

No Arabic literals appear in this file on purpose: the wording is imported from
the one place that owns it (``core.exceptions.NO_PERSONAL_KEY_DETAIL``) and
compared structurally, so a rewording there cannot silently orphan the frontend
rule that depends on it.
"""

import json
import re
from pathlib import Path

import pytest
from django.contrib.auth.models import User

from application import di
from api.models import UserProfile
from core.config import settings as core_settings
from core.exceptions import LLMError, MissingLLMCredentials, NO_PERSONAL_KEY_DETAIL

ROOT = Path(__file__).resolve().parents[2]
ERRORS_JS = ROOT / 'frontend' / 'src' / 'api' / 'errors.js'

# Obviously fake, and deliberately NOT shaped like a Gemini key: a realistic
# `AIza...` literal in the repository would make the next audit's grep for that
# prefix return a hit, and the audit could no longer trust its own result.
OPERATOR_KEY = 'OPERATOR-WIDE-KEY-NOT-REAL-000000'
USER_KEY = 'ACCOUNT-OWN-KEY-NOT-REAL-111111'


# --------------------------------------------------------------------------
# 1-2. the factory refuses
# --------------------------------------------------------------------------
class TestFactoryRefuses:

    def test_none_is_refused(self):
        with pytest.raises(MissingLLMCredentials):
            di.get_llm('gemini', None)

    def test_empty_string_is_refused(self):
        with pytest.raises(MissingLLMCredentials):
            di.get_llm('gemini', '')

    def test_whitespace_only_is_refused(self):
        # settings.save_settings strips before storing, but get_llm is reachable
        # from tests, admin paths and future callers. A key of spaces must not be
        # treated as a key.
        with pytest.raises(MissingLLMCredentials):
            di.get_llm('gemini', '   \n\t ')

    @pytest.mark.parametrize('missing', [None, '', '   '])
    def test_operator_key_is_never_substituted(self, missing, monkeypatch):
        """THE regression test.

        Setting the operator key is exactly the situation the fallback was built
        for. With the fallback gone, supplying one must change nothing at all for
        a keyless account -- if this test ever fails, someone has re-opened the
        hole rather than fixed a message.
        """
        monkeypatch.setattr(core_settings, 'GEMINI_API_KEY', OPERATOR_KEY)
        with pytest.raises(MissingLLMCredentials):
            di.get_llm('gemini', missing)

    def test_refusal_is_an_llm_error_so_existing_handlers_still_work(self):
        # Subclassing LLMError is what stops this from becoming an unhandled 500
        # in any handler that predates the change.
        assert issubclass(MissingLLMCredentials, LLMError)
        with pytest.raises(LLMError):
            di.get_llm('gemini', None)

    def test_refusal_carries_the_agreed_sentence(self):
        with pytest.raises(MissingLLMCredentials) as excinfo:
            di.get_llm('gemini', None)
        assert str(excinfo.value) == NO_PERSONAL_KEY_DETAIL

    def test_refusal_happens_before_the_cache_is_consulted(self):
        """A keyless call must not be handed a client cached for someone else.

        Checked by seeding the cache under the bare provider key, which is what
        ``_llm_cache_key`` returns when there is no digest.
        """
        sentinel = object()
        di._llm_cache['gemini'] = sentinel
        try:
            with pytest.raises(MissingLLMCredentials):
                di.get_llm('gemini', None)
        finally:
            di._llm_cache.pop('gemini', None)

    def test_a_real_key_still_builds(self):
        # The guard must not become a blanket refusal.
        llm = di.get_llm('gemini', USER_KEY)
        assert llm is not None
        di._llm_cache.clear()

    def test_ollama_needs_no_key_and_still_works(self):
        # Regression guard on the other side: ollama is local and legitimately
        # keyless, so it must not be swept into the refusal.
        llm = di.get_llm('ollama', None)
        assert llm is not None
        di._llm_cache.clear()


# --------------------------------------------------------------------------
# 4. the operator key keeps its embeddings job
# --------------------------------------------------------------------------
def test_operator_key_still_funds_embeddings(monkeypatch):
    """Removing the LLM fallback must not remove the embedding credential.

    ``settings.GEMINI_API_KEY`` remains the operator's own credential for
    ``EMBEDDING_PROVIDER=gemini``. A future tidy-up that deletes the setting
    because "chat no longer reads it" would silently break indexing for anyone
    who funds it that way.
    """
    from infrastructure.embedding.gemini_embedding_adapter import (
        GeminiEmbeddingAdapter,
    )

    monkeypatch.setattr(core_settings, 'EMBEDDING_PROVIDER', 'gemini')
    monkeypatch.setattr(core_settings, 'GEMINI_API_KEY', OPERATOR_KEY)
    monkeypatch.setattr(core_settings, 'EMBEDDING_MODEL', 'gemini-embedding-001')
    monkeypatch.setattr(core_settings, 'EMBEDDING_DIMENSION', 3072)

    monkeypatch.setattr(di, '_embedder_instance', None)
    try:
        adapter = di.get_embedder()
        assert isinstance(adapter, GeminiEmbeddingAdapter)
        assert adapter._api_key == OPERATOR_KEY
    finally:
        di._embedder_instance = None


# --------------------------------------------------------------------------
# 3. the endpoints answer 401, and never echo the operator key
# --------------------------------------------------------------------------
@pytest.mark.django_db
class TestEndpointsRefuseWithoutAnAccountKey:

    ASK = '/api/chat/ask'
    STREAM = '/api/chat/stream'

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        """No operator key by default; a clean LLM cache; clean rate limits.

        The limiter counts registrations per IP in the process-wide Django cache,
        so without the clear these registrations can inherit a counter another
        test file already pushed to its limit and be refused with a 429 -- a
        failure that depends on file order.
        """
        from django.core.cache import cache

        monkeypatch.setattr(core_settings, 'GEMINI_API_KEY', '')
        monkeypatch.setattr(core_settings, 'LLM_PROVIDER', 'gemini')
        di._llm_cache.clear()
        cache.clear()
        yield
        di._llm_cache.clear()
        cache.clear()

    def _account_without_a_key(self, client, username='nokey'):
        client.post(
            '/api/auth/register',
            data=json.dumps({
                'username': username,
                'password': 'StrongPass!123',
                'password_confirm': 'StrongPass!123',
            }),
            content_type='application/json',
        )
        profile = UserProfile.objects.get(user__username=username)
        profile.llm_provider = 'gemini'
        profile.gemini_api_key = ''
        profile.save(update_fields=['llm_provider', 'gemini_api_key'])
        return profile

    def _ask(self, client, url):
        return client.post(
            url,
            data=json.dumps({'question': 'what does this document say?'}),
            content_type='application/json',
        )

    @pytest.mark.parametrize('url', [ASK, STREAM])
    def test_keyless_account_is_refused(self, client, url):
        self._account_without_a_key(client)
        resp = self._ask(client, url)
        assert resp.status_code == 401, resp.content
        assert resp.json()['detail'] == NO_PERSONAL_KEY_DETAIL

    @pytest.mark.parametrize('url', [ASK, STREAM])
    def test_operator_key_does_not_unlock_the_chat(self, client, url, monkeypatch):
        """The exact incident, end to end.

        The deployment sets a key of its own. A user who never entered one asks a
        question. Before the fix this was answered -- billed to the operator.
        After it, it is refused. If this test fails, the sharing is back.
        """
        self._account_without_a_key(client, username='operator-fallback')
        monkeypatch.setattr(core_settings, 'GEMINI_API_KEY', OPERATOR_KEY)
        resp = self._ask(client, url)
        assert resp.status_code == 401, resp.content

    @pytest.mark.parametrize('url', [ASK, STREAM])
    def test_operator_key_is_never_echoed(self, client, url, monkeypatch):
        self._account_without_a_key(client, username='no-echo')
        monkeypatch.setattr(core_settings, 'GEMINI_API_KEY', OPERATOR_KEY)
        body = self._ask(client, url).content.decode('utf-8')
        assert OPERATOR_KEY not in body

    def test_refusal_is_not_a_server_fault(self, client):
        """5xx would be a lie, and would also read as "retry later".

        Nothing upstream failed and nothing is retryable: the account is missing a
        setting. So the status has to be a client-state one, and the sentence has
        to name the next step.
        """
        self._account_without_a_key(client, username='not-5xx')
        resp = self._ask(client, self.ASK)
        assert 400 <= resp.status_code < 500
        assert resp.status_code != 429

    def test_body_is_arabic(self, client):
        """Guards the 401 collision in frontend/src/api/errors.js.

        ``BY_STATUS[401]`` reads "the session expired -- log in again". That is
        true for /auth/* and wrong here: the user IS logged in. The Arabic body
        is what makes errors.js prefer it, so the property the UI depends on is
        asserted here rather than left implicit.
        """
        self._account_without_a_key(client, username='arabic-body')
        detail = self._ask(client, self.ASK).json()['detail']
        assert re.search(r'[\u0600-\u06ff]', detail), detail


# --------------------------------------------------------------------------
# 5. the frontend rule still claims the sentence
# --------------------------------------------------------------------------
def _strip_js_comments(src: str) -> str:
    """Remove ``//`` and ``/* */`` comments that are not inside a string.

    Required because ``BY_DETAIL`` puts explanatory comments INSIDE several rule
    brackets -- including the rule that was added for the missing-key sentence.
    A plain regex over the raw source silently skipped every one of those rules,
    and the skip was invisible: the table still parsed, it just parsed as if
    those rules did not exist. That is precisely how a test written to catch a
    missing frontend mapping comes to pass while the mapping is missing.
    """
    out: list[str] = []
    i, n, quote = 0, len(src), None
    while i < n:
        c = src[i]
        if quote is not None:
            out.append(c)
            if c == '\\' and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in '\'"`':
            quote = c
            out.append(c)
            i += 1
            continue
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            while i < n and src[i] != '\n':
                i += 1
            continue
        if c == '/' and i + 1 < n and src[i + 1] == '*':
            i += 2
            while i + 1 < n and not (src[i] == '*' and src[i + 1] == '/'):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def _by_detail_rules():
    """Parse BY_DETAIL out of errors.js as (needle, raw_value) in source order."""
    src = _strip_js_comments(ERRORS_JS.read_text(encoding='utf-8'))
    start = src.index('const BY_DETAIL = [')
    end = src.index('\n]', start)
    block = src[start:end]
    return [
        (m.group(1).replace("\\'", "'"), m.group(2))
        for m in re.finditer(
            r"\[\s*'((?:[^'\\]|\\.)*)'\s*,\s*(null|'(?:[^'\\]|\\.)*')", block
        )
    ]


class TestFrontendStillRendersIt:
    """errors.js decides the wording; the backend only supplies the sentence."""

    def test_the_table_is_parseable(self):
        """Guard against the two tests below passing vacuously.

        Named in ASCII deliberately: this assertion must not itself be the thing
        that a future encoding problem breaks. The four needles cover both rule
        layouts in the table -- the bare one-line form and the multi-line form
        that carries a comment inside its brackets, which is the layout the
        parser originally mis-read.
        """
        needles = {n for n, _ in _by_detail_rules()}
        assert 'not allowed' in needles               # one line
        assert 'Question cannot be empty' in needles   # one line
        assert 'Too many requests' in needles          # commented multi-line
        assert 'File too large' in needles             # commented multi-line

    def test_some_rule_claims_the_sentence(self):
        detail = NO_PERSONAL_KEY_DETAIL.lower()
        rules = _by_detail_rules()
        claimed = [n for n, _ in rules if n.lower() in detail]
        assert claimed, (
            'no BY_DETAIL rule matches NO_PERSONAL_KEY_DETAIL, so the 401 would '
            'fall through to the generic "session expired" string'
        )

    def test_the_first_rule_to_match_is_a_passthrough(self):
        """ORDER MATTERS: the first match wins.

        A broader rule sitting above this one would rewrite the sentence -- and
        there is a live hazard of exactly that shape in the table, a login rule
        keyed on a short Arabic phrase that this sentence must therefore never
        contain.
        """
        detail = NO_PERSONAL_KEY_DETAIL.lower()
        for needle, raw_value in _by_detail_rules():
            if needle.lower() in detail:
                assert raw_value == 'null', (
                    f'rule {needle!r} claims the sentence but re-words it; a '
                    'broader rule above it would win over the passthrough'
                )
                return
        pytest.fail('no BY_DETAIL rule matched')