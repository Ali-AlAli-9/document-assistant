"""The production password hasher must stay Argon2id, and stay this expensive.

Every number here is load-bearing in a way that is invisible at runtime, which
is exactly why they need an assertion nobody can skip by accident.

The failure this prevents
=========================
``PASSWORD_HASHERS`` in ``rag_project/settings.py`` silently changed once: the
Django 6.0 upgrade raised PBKDF2 to 1,200,000 iterations, and a login went from
instant to **8.29s**. No test failed, no traceback appeared, and the code diff
that caused it was one framework version. Nothing in the suite measured login
latency, so the regression shipped.

Two ways that can happen again, both silent:

* someone sets ``PASSWORD_HASHERS`` back to the PBKDF2 default, or
* someone edits ``core/password_hashers.py`` and lowers a cost to "make login
  faster" — the classic trade that quietly removes the memory-hardness Argon2
  was adopted for.

So the assertions below cover both the ALGORITHM and each individual parameter.
Lowering ``memory_cost`` is the tempting one (19MiB is measurably faster) and is
also the one that removes what makes Argon2 worth using over PBKDF2.

OWASP's Password Storage Cheat Sheet puts the Argon2id floor at 19 MiB of
memory, 2 iterations, parallelism 1. Every value asserted here is at or above
that floor; the margins over it are the point.
"""

import pytest
from django.conf import settings
from django.contrib.auth.hashers import (
    get_hasher,
    identify_hasher,
    make_password,
)

from core.password_hashers import Argon2idHasher

# OWASP's argon2id minimum. Asserted against, not merely documented: a value at
# or under this line would be a downgrade dressed up as a speed fix.
OWASP_MIN_MEMORY_KIB = 19 * 1024
OWASP_MIN_TIME_COST = 2
OWASP_MIN_PARALLELISM = 1


class TestTheDefaultHasherIsArgon2id:
    def test_argon2id_is_first_in_the_chain(self):
        """Position zero is what ``get_hasher('default')`` returns, and therefore
        what every NEW password is hashed with.

        ``must_update`` in check_password compares the stored algorithm against
        this one, so anything after it is a legacy verifier, never a writer.
        """
        assert settings.PASSWORD_HASHERS[0] == 'core.password_hashers.Argon2idHasher'
        assert isinstance(get_hasher('default'), Argon2idHasher)

    def test_argon2_cffi_is_installed_rather_than_only_named(self):
        """PASSWORD_HASHERS is resolved lazily, per use — a missing package does
        not fail at boot, it fails on the first login, as a 500.

        Django ships Argon2PasswordHasher in its hashers module without
        depending on argon2-cffi, so naming it is not proof it can load.
        """
        pytest.importorskip  # keep the intent explicit; the import below is the test
        try:
            import argon2  # noqa: F401
        except ImportError:
            pytest.fail(
                'argon2-cffi is not installed but PASSWORD_HASHERS selects '
                'Argon2idHasher; logins would fail at the first attempt'
            )

    def test_requirements_pins_argon2_for_a_fresh_deploy(self):
        """A clean CI or a rebuilt image installs from requirements.txt alone, so
        a hasher that is configured but not declared produces an environment
        where every login 500s and only production discovers it."""
        from pathlib import Path

        reqs = (Path(__file__).resolve().parents[2] / 'requirements.txt').read_text(
            encoding='utf-8'
        )
        assert 'argon2-cffi' in reqs, (
            'argon2-cffi is missing from requirements.txt; a fresh install '
            'cannot hash a single password'
        )

    def test_the_legacy_hashers_remain_verifiable(self):
        """The PBKDF2 entry is not dead config — it is the only reason an account
        created before this change can log in at all.

        Django verifies against the algorithm encoded in the STORED hash, not
        the configured default. Delete the legacy entries and every existing
        account is locked out permanently, with no error to explain it.
        """
        assert 'django.contrib.auth.hashers.PBKDF2PasswordHasher' in settings.PASSWORD_HASHERS
        legacy = make_password('Str0ngPass!x', hasher='pbkdf2_sha256')
        assert identify_hasher(legacy).algorithm == 'pbkdf2_sha256'


class TestTheCostParameters:
    def test_memory_meets_the_owasp_floor_with_margin(self):
        """The memory is the whole point.

        PBKDF2 is cheap per byte and embarrassingly parallel, so a GPU rig grinds
        it fast. Argon2 fills 64MiB per guess, which is why an attacker has to
        buy memory rather than only cores. Lowering memory_cost past the OWASP
        floor does not just cost security margin — it removes the reason this
        algorithm was chosen over what was already there.
        """
        assert Argon2idHasher.memory_cost >= OWASP_MIN_MEMORY_KIB, (
            f'memory_cost={Argon2idHasher.memory_cost}KiB is below OWASP\'s '
            f'{OWASP_MIN_MEMORY_KIB}KiB floor for Argon2id'
        )

    def test_iterations_meet_the_owasp_floor(self):
        assert Argon2idHasher.time_cost >= OWASP_MIN_TIME_COST, (
            f'time_cost={Argon2idHasher.time_cost} is below OWASP\'s '
            f'{OWASP_MIN_TIME_COST}'
        )

    def test_parallelism_is_not_oversubscribing_a_small_host(self):
        """Unlike every other parameter here, a HIGH parallelism is the risk.

        Django 6.0 defaults to ``parallelism=8``. On the 1-2 vCPU container that
        actually serves requests, eight Argon2 threads contend for fewer cores:
        measured 1.72s median with a 5.76s worst case, against 0.59s / 1.10s at
        p=2. Login is the first thing every user does, so the worst case is the
        one a demo audience feels.
        """
        assert 1 <= Argon2idHasher.parallelism <= 4, (
            f'parallelism={Argon2idHasher.parallelism} is tuned for a developer '
            'laptop with spare cores; the service runs on 1-2 vCPUs'
        )

    def test_the_three_costs_are_the_measured_ones(self):
        """Pins the exact numbers rather than the floor.

        A floor test alone would allow a slow drift — each individual edit can
        pass while the pair together is one that made login unusable. These are
        the values that measured 0.59s median / 1.10s worst case against
        PBKDF2's 8.29s.
        """
        assert (Argon2idHasher.time_cost, Argon2idHasher.memory_cost,
                Argon2idHasher.parallelism) == (2, 65536, 2)


class TestLoginActuallyStaysFast:
    def test_verifying_a_hash_is_fast_enough_for_a_login(self):
        """The measurement that started all of this, as an executable bound.

        Not a benchmark — a smoke guard. The 8.29s regression was invisible to
        the suite; this fails if hashing creeps back past roughly a second on a
        machine no slower than the one that recorded it. On a faster CI box it
        passes trivially, which is the correct direction for a ceiling.
        """
        import time

        encoded = make_password('Str0ngPass!x', hasher=Argon2idHasher())
        start = time.perf_counter()
        assert Argon2idHasher().verify('Str0ngPass!x', encoded)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, (
            f'password verification took {elapsed:.2f}s; the PBKDF2 regression '
            'was 8.29s and passed every other test'
        )