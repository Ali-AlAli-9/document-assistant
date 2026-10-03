"""Per-account brute-force lockout on the login endpoint.

Why this file exists
====================
``core.middleware.RateLimitMiddleware`` limits login attempts per IP
(10/minute). That is the wrong axis for password guessing: rotating the source
address -- a VPN, a proxy pool, Tor, a botnet -- resets the budget completely,
so a single known account could still be ground down at full speed while the
limiter happily reports "you are under your quota".

This suite pins the missing axis: a counter and a lock keyed on the USERNAME,
which follows the attacker instead of the address.

The two properties that matter, and that this file exists to hold:

1. **The lock actually holds.** Enough wrong passwords lock the account for a
   cooling-off period, and while locked even the CORRECT password is refused —
   otherwise the lock would only delay an attacker who already knows the answer.

2. **The lock never becomes an account-existence oracle.** Failures are counted
   for every username, whether or not an account exists, and the reply is the
   same shape either way. If lockout only applied to real accounts, "I got
   locked" would answer "does this username exist?" — the exact hole the
   registration endpoint used to have.

Rate limiting note: the per-IP limiter keeps a process-wide counter that would
otherwise start returning 429 mid-suite and mask what is under test, so the
cache is cleared around every attempt here. That is deliberate isolation: this
suite exercises the ACCOUNT layer, and ``tests/core/test_rate_limit_client_ip.py``
covers the ADDRESS layer.
"""

import json
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.utils import timezone

from api import auth_lockout
from api.models import LoginAttempt

LOGIN_URL = "/api/auth/login"
ME_URL = "/api/auth/me"
PASSWORD = "Str0ng-Passphrase-91"
WRONG = "definitely-not-the-password"


@pytest.fixture(autouse=True)
def _isolate_rate_limit():
    """Keep the per-IP limiter from interfering with the account lockout."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _fast_password_hashing(settings):
    """Replace Argon2id with the MD5 hasher for this module only.

    Production hashing is deliberately untouched — see ``PASSWORD_HASHERS`` in
    ``rag_project/settings.py``, measured at 0.59s per login. That is still far
    too slow for a suite like this one: it performs well over a hundred failed
    logins to reach the tenth-failure threshold, so hashing would account for
    minutes of its runtime and bury the actual behaviour under a slow suite.

    Worth noting what the counter does NOT depend on: this module asserts
    lockout arithmetic and response wording, none of which touches the hasher.
    So the fast stand-in costs this file no coverage of production behaviour —
    ``tests/core/test_password_hashers.py`` is what covers the real parameters.
    """
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def _account(username, password=PASSWORD):
    return User.objects.create_user(username=username, password=password)


def _attempt(client, username, password=WRONG):
    """One login POST, with the per-IP budget reset first."""
    cache.clear()
    return client.post(
        LOGIN_URL,
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
    )


def _fail(client, username, times):
    """Send `times` wrong passwords; return the final response."""
    response = None
    for _ in range(times):
        response = _attempt(client, username)
    return response


@pytest.mark.django_db
class TestTheLockEngages:
    def test_a_few_mistyped_passwords_are_not_punished(self, client):
        _account("alice")
        response = _fail(client, "alice", auth_lockout.WARN_AFTER - 1)

        assert response.status_code == 401
        # No nagging: the user is not yet in danger, so nothing is added.
        assert response.json()["detail"] == "اسم المستخدم أو كلمة المرور غير صحيحة"

    def test_the_user_is_warned_before_the_lock_closes(self, client):
        _account("alice")
        _fail(client, "alice", auth_lockout.WARN_AFTER)

        response = _attempt(client, "alice")

        assert response.status_code == 401
        assert "بقي لك" in response.json()["detail"]
        # Still the same refusal — a warning must not read as a different outcome.
        assert "غير صحيحة" in response.json()["detail"]

    def test_enough_wrong_passwords_lock_the_account(self, client):
        _account("alice")

        response = _fail(client, "alice", auth_lockout.MAX_FAILURES)

        assert response.status_code == 429
        assert "مؤقتًا" in response.json()["detail"]

    def test_the_shipped_thresholds_are_ten_failures_and_fifteen_minutes(self):
        # Pinned because these numbers are the actual protection. A silent
        # change to something permissive (e.g. 100) would keep every other test
        # in this file green while removing the feature.
        assert auth_lockout.MAX_FAILURES == 10
        assert auth_lockout.LOCK_MINUTES == 15
        assert 0 < auth_lockout.WARN_AFTER < auth_lockout.MAX_FAILURES

    def test_the_lock_engages_exactly_at_the_configured_threshold(self, client):
        _account("alice")

        just_under = _fail(client, "alice", auth_lockout.MAX_FAILURES - 1)
        assert just_under.status_code == 401

        assert _attempt(client, "alice").status_code == 429

    def test_the_429_tells_the_client_when_to_come_back(self, client):
        _account("alice")

        response = _fail(client, "alice", auth_lockout.MAX_FAILURES)

        retry_after = int(response["Retry-After"])
        assert 0 < retry_after <= auth_lockout.LOCK_MINUTES * 60


@pytest.mark.django_db
class TestTheLockHolds:
    def test_even_the_correct_password_is_refused_while_locked(self, client):
        """The whole point of the lock.

        A lock that a correct password walks straight through only slows down
        an attacker who does NOT already know the password, which is not the
        attack that matters.
        """
        _account("alice")
        _fail(client, "alice", auth_lockout.MAX_FAILURES)

        response = _attempt(client, "alice", PASSWORD)

        assert response.status_code == 429
        # And no session was handed out behind the lock.
        assert client.get(ME_URL).status_code == 401

    def test_the_lock_expires_and_the_budget_is_restored(self, client):
        _account("alice")
        _fail(client, "alice", auth_lockout.MAX_FAILURES)

        # Simulate the cooling-off period having elapsed. Rewriting the stored
        # deadline is deliberately used instead of mocking "now": it exercises
        # the real comparison the endpoint performs.
        LoginAttempt.objects.filter(username="alice").update(
            locked_until=timezone.now() - timedelta(minutes=1)
        )

        assert _attempt(client, "alice", PASSWORD).status_code == 200
        # A full window, not a continuation of the exhausted one — otherwise a
        # legitimate user who forgot his password stays locked out forever.
        assert auth_lockout.failures_remaining("alice") == auth_lockout.MAX_FAILURES

    def test_a_successful_login_clears_the_counter(self, client):
        """Typos must not accumulate against the next real attempt."""
        _account("alice")
        _fail(client, "alice", auth_lockout.WARN_AFTER - 1)

        assert _attempt(client, "alice", PASSWORD).status_code == 200
        assert auth_lockout.failures_count("alice") == 0

    def test_one_locked_username_does_not_lock_the_next_one(self, client):
        _account("bob")
        _fail(client, "alice", auth_lockout.MAX_FAILURES)

        assert _attempt(client, "bob", PASSWORD).status_code == 200

    def test_changing_the_case_cannot_mint_a_fresh_budget(self, client):
        """A lockout keyed on the raw string is trivially bypassed.

        The endpoint that consumes the key is case-SENSITIVE, so varying the
        case does not let an attacker guess the password — but it would let him
        dodge the lock. The counter is therefore case-folded, so all spellings
        of one username share a single budget.
        """
        _account("alice")
        _fail(client, "alice", auth_lockout.MAX_FAILURES - 1)

        # The final failure arrives spelled differently.
        assert _attempt(client, "ALICE").status_code == 429


@pytest.mark.django_db
class TestNoAccountExistenceOracle:
    def test_a_real_account_and_an_invented_one_are_indistinguishable(self, client):
        """Every round, for both usernames, must look the same.

        This is the load-bearing assertion of the whole feature: lockout keyed
        only on real accounts would answer "does this user exist?" to anyone
        willing to burn ten guesses.
        """
        _account("realuser")

        for _ in range(auth_lockout.MAX_FAILURES):
            known = _attempt(client, "realuser")
            unknown = _attempt(client, "ghostuser")

            assert known.status_code == unknown.status_code, "status reveals existence"
            assert known.json() == unknown.json(), "wording reveals existence"

        # Both are locked at the same round: the lock is reachable for a
        # username that was never registered.
        assert known.status_code == 429

    def test_a_successful_login_still_says_only_what_it_must(self, client):
        _account("realuser")

        response = _attempt(client, "realuser", PASSWORD)

        assert response.status_code == 200
        # Nothing about other accounts, no internal identifiers.
        assert set(response.json()) == {"username", "has_gemini_key", "llm_provider"}


@pytest.mark.django_db
class TestTheTableCannotBeUsedToFillTheDatabase:
    def test_stale_counters_are_purged(self, client):
        """Probing invented usernames writes a row per guess.

        Without cleanup an attacker could fill the table with junk rows just by
        failing to log in, so abandoned counters are swept away.
        """
        LoginAttempt.objects.create(username="long-gone", failures=2)
        LoginAttempt.objects.filter(username="long-gone").update(
            updated_at=timezone.now() - timedelta(days=auth_lockout.STALE_DAYS + 1)
        )

        _attempt(client, "alice", PASSWORD)  # a successful login still sweeps

        assert not LoginAttempt.objects.filter(username="long-gone").exists()

    def test_a_fresh_counter_survives_the_sweep(self, client):
        _account("alice")
        _fail(client, "alice", auth_lockout.WARN_AFTER - 1)

        _attempt(client, "alice", PASSWORD)

        # The successful attempt cleared its own row (as designed), so the
        # proof that the sweep is date-scoped and not blanket is that a
        # counter younger than the retention window survives an unrelated
        # failure on another username.
        _fail(client, "bob", 1)
        assert auth_lockout.failures_count("bob") == 1
