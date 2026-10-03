"""Advisory cross-process writer lock for the Chroma vector store.

WHY
===
Chroma persists through SQLite, and two clients writing concurrently corrupt
that file. The guidance used to be "stop the Django server before running an
ingest script" — folklore that nobody remembers, and whose failure mode is an
opaque SQLite error raised from deep inside the driver.

This turns the folklore into a mechanism: an atomically created lock file
records who is writing, and a second writer is refused immediately with a
message naming the holder.

HOW THE LOCK IS DECIDED
=======================
A lock file exists but is not automatically a live lock. A holder that is gone
must not block ingestion for ever, so a lock is stolen when:

* its PID cannot be opened (the process died), or
* it was written by another host (we cannot probe a remote PID), or
* it is older than ``STALE_AFTER_HOURS`` (guards against PID reuse — a
  recycled PID must not pin the store shut).

A lock with a demonstrably live owner on this host is never stolen.

FAILURE MODE, STATED PLAINLY
============================
If the lock file cannot be created at all (read-only mount, wrong permissions)
the lock DEGRADES TO A WARNING and the write proceeds. Availability wins: an
unwritable lock location must not make every upload fail, and the corruption
this guards against needs two *concurrent* writers, which the single-threaded
upload path already makes unlikely. The alternative — refusing all writes — would
trade a rare data risk for a certain outage.
"""

import contextlib
import errno
import json
import logging
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: After this long a lock is assumed abandoned even if its PID still resolves.
STALE_AFTER_HOURS = 6

LOCK_FILENAME = '.writer.lock'


class VectorStoreBusy(RuntimeError):
    """Another process is writing to the vector store."""


@dataclass(frozen=True)
class WriterLockInfo:
    pid: int
    host: str
    acquired_at: float
    purpose: str

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.acquired_at)


def _resolve_lock_path() -> Path:
    from core.config import settings

    return Path(settings.CHROMA_PERSIST_DIR) / LOCK_FILENAME


def read_lock() -> WriterLockInfo | None:
    """Return the current holder, or None when there is no readable lock."""
    try:
        raw = _resolve_lock_path().read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # A truncated file from a half-written lock is not a live lock.
        return None
    try:
        return WriterLockInfo(
            pid=int(payload['pid']),
            host=str(payload['host']),
            acquired_at=float(payload['acquired_at']),
            purpose=str(payload.get('purpose', '')),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _process_alive(pid: int, host: str) -> bool:
    if host and host != socket.gethostname():
        return False
    if pid <= 0:
        return False
    if os.name == 'nt':
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except OSError as exc:
        # EPERM means it exists but belongs to someone else — still alive.
        return exc.errno == errno.EPERM
    return True


def _windows_process_alive(pid: int) -> bool:
    """Windows has no ``kill(pid, 0)``; open the process to test for it.

    PROCESS_QUERY_LIMITED_INFORMATION is the least privilege that answers
    "does this PID exist", which matters because the alternative (full query
    information) fails for processes owned by other accounts.
    """
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _claim(path: Path, purpose: str) -> None:
    """Create the lock file, failing if it already exists."""
    payload = json.dumps(
        {
            'pid': os.getpid(),
            'host': socket.gethostname(),
            'acquired_at': time.time(),
            'purpose': purpose,
        }
    )
    # O_EXCL is the atomic part: the filesystem decides the winner, so two
    # processes racing here cannot both believe they hold the lock.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, payload.encode('utf-8'))
    finally:
        os.close(fd)


def _is_stale(info: WriterLockInfo) -> bool:
    if info.age_seconds > STALE_AFTER_HOURS * 3600:
        return True
    return not _process_alive(info.pid, info.host)


def _release(path: Path) -> None:
    """Remove the lock, but only if we still own it.

    Checking ownership matters: if our lock was already reclaimed as stale by
    someone else, deleting the file would drop THEIR lock.
    """
    info = read_lock()
    if info is not None and info.pid != os.getpid():
        return
    with contextlib.suppress(OSError):
        path.unlink()


def _busy_message(info: WriterLockInfo | None) -> str:
    """Name the holder. "Busy" without saying by whom is the same dead end as
    the SQLite error this replaced."""
    if info is None:
        return 'قاعدة المتجهات مشغولة الآن — أعد المحاولة بعد قليل.'
    detail = f' (عملية {info.pid}' + (f': {info.purpose}' if info.purpose else '') + ')'
    return f'قاعدة المتجهات مشغولة الآن{detail} — أعد المحاولة بعد انتهائها.'


@contextlib.contextmanager
def vector_store_writer(purpose: str = ''):
    """Hold the vector-store writer lock for the duration of the block."""
    path = _resolve_lock_path()
    existing = read_lock()
    if existing is not None and not _is_stale(existing):
        raise VectorStoreBusy(_busy_message(existing))
    if existing is not None or path.exists():
        # Whatever is on disk that is not a live holder — stale, or a file we
        # cannot even parse — must go, or the atomic create below cannot
        # succeed. Between this unlink and the create another process may claim
        # the lock itself, which the O_EXCL create then detects, so no
        # double-writer can slip through the gap.
        with contextlib.suppress(OSError):
            path.unlink()

    # Directory creation is kept separate from the claim so the two failures
    # stay distinguishable: on Windows, mkdir over a FILE also raises
    # FileExistsError, which would otherwise be misread as losing a claim race.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            'Vector store writer lock unavailable at %s (%s); continuing without it',
            path,
            exc,
        )
        yield
        return

    try:
        _claim(path, purpose)
    except FileExistsError:
        # Lost the race for a stale lock: the winner is writing right now.
        raise VectorStoreBusy(_busy_message(read_lock())) from None
    except OSError as exc:
        # See the module docstring: availability over coordination.
        logger.warning(
            'Vector store writer lock unavailable at %s (%s); continuing without it',
            path,
            exc,
        )
        yield
        return

    try:
        yield
    finally:
        _release(path)
