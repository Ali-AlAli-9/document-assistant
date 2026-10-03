"""Encryption of per-account secrets at rest.

WHY
---
Each registered user stores his OWN provider key (``UserProfile.gemini_api_key``)
so the assistant can call the LLM on his behalf. As plaintext in the database
that key is a *working credential*: any database dump, backup file, replica or
staff SQL console hands over a usable Gemini key. Encrypting it at rest means a
leaked database yields ciphertext that is worthless without the application
secret, which lives in the environment (``SECRET_KEY``) and never in the code
or the repository.

KEY DERIVATION
--------------
HKDF-SHA256 (RFC 5869: extract + expand) over ``SECRET_KEY`` with a fixed
application salt and a domain-separation label. This is the right primitive
because ``SECRET_KEY`` is a high-entropy random secret (Render generates it),
not a human password — so an expensive password KDF (PBKDF2/scrypt/argon2)
would add latency without adding meaningful strength. The label means the
derived key can never collide with a key derived for another purpose, and the
cache is keyed by the secret so a rotated ``SECRET_KEY`` yields a new key
instead of a stale one.

CONSEQUENCE (documented, deliberate)
------------------------------------
Rotating ``SECRET_KEY`` makes previously stored keys undecryptable — they must
be re-entered by their owners. That is the correct trade-off: the alternative
would be a second secret whose only job is to be rotated less often.

FORMAT
------
``fernet:v1:<token>``. The prefix is what makes a legacy plaintext row
recognisable: a row without it is returned as-is, so databases written before
encryption existed keep working and are re-encrypted on the next save.
"""

import base64
import hashlib
import hmac
import logging

from cryptography.fernet import Fernet, InvalidToken

from core.config import settings

logger = logging.getLogger(__name__)

CIPHER_PREFIX = 'fernet:v1:'
_HASH = hashlib.sha256
_SALT = b'document-assistant/secret-encryption/v1'
_INFO = b'user-llm-api-key'
_KEY_LENGTH = 32  # Fernet keys are 32 url-safe base64-encoded bytes

# Keyed by the secret itself so a rotated SECRET_KEY never returns a stale
# Fernet, and so tests can swap the secret without cross-test contamination.
_fernet_cache: dict[str, Fernet] = {}


class SecretDecryptionError(Exception):
    """A stored secret could not be decrypted with the current SECRET_KEY."""


def _hkdf(secret: bytes, salt: bytes, info: bytes, length: int = _KEY_LENGTH) -> bytes:
    """RFC 5869 HKDF-SHA256 (extract then expand)."""
    prk = hmac.new(salt, secret, _HASH).digest()
    okm = b''
    block = b''
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), _HASH).digest()
        okm += block
        counter += 1
    return okm[:length]


def _get_fernet() -> Fernet:
    secret = settings.SECRET_KEY
    cached = _fernet_cache.get(secret)
    if cached is None:
        derived = base64.urlsafe_b64encode(_hkdf(secret.encode('utf-8'), _SALT, _INFO))
        cached = Fernet(derived)
        _fernet_cache[secret] = cached
    return cached


def is_encrypted(stored: str) -> bool:
    """True when ``stored`` is a ciphertext produced by :func:`encrypt_secret`."""
    return bool(stored) and stored.startswith(CIPHER_PREFIX)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt for storage. Empty stays empty so "no key" stays distinguishable."""
    if not plaintext:
        return ''
    token = _get_fernet().encrypt(plaintext.encode('utf-8')).decode('ascii')
    return CIPHER_PREFIX + token


def decrypt_secret(stored: str) -> str:
    """Recover the plaintext for an outbound call. Server-side use only.

    A legacy row stored before encryption existed has no prefix and is returned
    unchanged, which keeps existing databases working.

    Raises:
        SecretDecryptionError: the row is tampered with, or ``SECRET_KEY`` was
            rotated since it was written.
    """
    if not stored:
        return ''
    if not is_encrypted(stored):
        return stored
    token = stored[len(CIPHER_PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError) as exc:
        raise SecretDecryptionError(
            'stored secret cannot be decrypted with the current SECRET_KEY'
        ) from exc
