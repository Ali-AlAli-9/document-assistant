"""Per-account brute-force lockout (state only — no user-facing wording).

WHY THIS EXISTS
===============
``core.middleware.RateLimitMiddleware`` caps login attempts per IP. That is the
wrong axis for password guessing: an attacker who rotates the source address
(a VPN, a proxy pool, a botnet) gets a brand-new budget every time, so a single
known account can still be ground down at full speed while the limiter reports
"you are within your quota".

This module adds the missing axis — a counter and a lock keyed on the USERNAME,
which follows the attacker rather than the address.

WHY THE DATABASE AND NOT THE CACHE
==================================
The obvious home for a counter is Django's cache, which is what the middleware
uses. That would be a mistake here:

* ``LocMemCache`` is PER PROCESS, so with several gunicorn workers each worker
  would keep its own tally — the effective threshold would silently multiply by
  the worker count.
* It is wiped on every restart or deploy, so an attacker could simply wait for
  the next deploy to get a fresh allowance.

A row is shared by every worker and survives restarts. Migrating the *rate
limiter* to Redis stays a separate concern; this table does not depend on it.

WHY EVERY USERNAME IS COUNTED, NOT ONLY REAL ONES
==================================================
Locking only accounts that exist would make "I got locked" a reliable answer to
"does this username exist?" — an account-existence oracle, on a PUBLIC login
form, for free. So a counter is written for every failed attempt, whether or not
an account is behind it, and the reply is identical either way. The endpoint
(``api.endpoints.auth``) owns the wording; this module deliberately knows
nothing about what the user is told.

STALE ROWS
==========
Probing invented usernames writes a row per guess, so abandoned counters are
swept after ``STALE_DAYS``. Otherwise failing to log in would be a way to fill
the table.
"""

import logging
from datetime import timedelta
from typing import NamedTuple

from django.db import connection, transaction
from django.utils import timezone

from api.models import LoginAttempt

logger = logging.getLogger(__name__)

#: Wrong passwords tolerated before the username is locked.
MAX_FAILURES = 10
#: How long the lock lasts, in minutes.
LOCK_MINUTES = 15
#: From this many failures left, the reply starts warning the user.
WARN_AFTER = 5
#: A counter untouched for this many days is swept away.
STALE_DAYS = 1

_STALE_AFTER = timedelta(days=STALE_DAYS)


class LockoutState(NamedTuple):
    """Where a username stands after a failed attempt."""

    failures: int
    remaining: int
    locked_seconds: int

    @property
    def locked(self) -> bool:
        return self.locked_seconds > 0


def _key(username: str) -> str:
    """Normalise a username into its counter key.

    Case folding is not cosmetic. The endpoint that consumes this key is
    case-SENSITIVE, so varying the case cannot get an attacker closer to the
    password — but it would let him shake off the lock entirely. Folding merges
    the spellings, which can only ever make the lock stricter.
    """
    return (username or "").strip().casefold()[:150]


def failures_count(username: str) -> int:
    row = LoginAttempt.objects.filter(username=_key(username)).first()
    return row.failures if row else 0


def failures_remaining(username: str) -> int:
    return max(0, MAX_FAILURES - failures_count(username))


def locked_seconds(username: str) -> int:
    """Seconds left on the lock, or 0 when the username may attempt a login."""
    row = LoginAttempt.objects.filter(username=_key(username)).first()
    if row is None or row.locked_until is None:
        return 0
    remaining = (row.locked_until - timezone.now()).total_seconds()
    return int(remaining) if remaining > 0 else 0


def clear_failures(username: str) -> None:
    """Forget the counter — called after a successful login.

    Without this a user who fumbled his password a few times would carry those
    typos against his next attempt.
    """
    LoginAttempt.objects.filter(username=_key(username)).delete()


def record_failure(username: str) -> LockoutState:
    """Count one wrong password and lock the username if the limit is reached."""
    key = _key(username)
    now = timezone.now()

    with transaction.atomic():
        _purge_stale(now)

        queryset = LoginAttempt.objects.filter(username=key)
        if connection.features.has_select_for_update:
            # Serialises concurrent guesses on PostgreSQL so two parallel
            # requests cannot both read "9 failures" and slip past the limit.
            # SQLite reports no support for it; there a lost update only shifts
            # the lock by a single attempt.
            queryset = queryset.select_for_update()

        row = queryset.first()
        if row is None:
            row = LoginAttempt(username=key, failures=0)

        if row.failures + 1 >= MAX_FAILURES:
            row.failures = 0
            row.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            logger.warning("Login locked for username %r after repeated failures", key)
        else:
            row.failures += 1
            # Clears an expired lock left over from an earlier window, so a new
            # failure starts from a clean slate.
            row.locked_until = None

        row.save()

    # Computed from the row just written rather than re-read, so one failed
    # login costs one query, not two.
    locked_for = int((row.locked_until - now).total_seconds()) if row.locked_until else 0

    return LockoutState(
        failures=row.failures,
        remaining=max(0, MAX_FAILURES - row.failures),
        locked_seconds=max(0, locked_for),
    )


def _purge_stale(now) -> None:
    """Drop counters nobody has touched for ``STALE_DAYS``."""
    LoginAttempt.objects.filter(updated_at__lt=now - _STALE_AFTER).delete()
