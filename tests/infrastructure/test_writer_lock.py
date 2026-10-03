"""Cross-process writer lock for the Chroma vector store.

WHY THIS EXISTS
===============
Two Chroma clients writing at once corrupt the SQLite store that backs it.
The manual rule used to be "stop the Django server before you run an ingest
script" — a rule nobody can remember, and one that produces a lock error rather
than a clear message.

The lock turns that folklore into a mechanism: a lock file created atomically,
carrying the owner's PID and host. A second writer in another process is refused
with a message that names the holder instead of failing later inside SQLite.

Two properties are load-bearing:

1. **A crashed holder must not block forever.** The lock is stolen when its
   owner is demonstrably gone (dead PID, different host, or older than
   ``STALE_AFTER_HOURS``), otherwise one ``kill -9`` would disable ingestion
   until someone deleted a file by hand.
2. **A live holder must not be stolen from.** Stealing from a running process is
   exactly the corruption the lock exists to prevent.
"""

import json
import os
import socket
import time

import pytest

from infrastructure.vector_store import writer_lock as wl


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """Point the lock at a throwaway directory."""
    monkeypatch.setattr(wl, "_resolve_lock_path", lambda: tmp_path / ".writer.lock")
    return tmp_path / ".writer.lock"


def _write_lock(path, **overrides):
    payload = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "acquired_at": time.time(),
        "purpose": "test",
    }
    payload.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


class TestAcquisition:
    def test_an_unlocked_store_can_be_written(self, lock_dir):
        with wl.vector_store_writer("ingest"):
            assert lock_dir.exists()

    def test_the_lock_file_is_removed_on_exit(self, lock_dir):
        with wl.vector_store_writer("ingest"):
            pass
        assert not lock_dir.exists()

    def test_the_lock_is_released_even_when_the_body_raises(self, lock_dir):
        # A crash inside the guarded block must not leave the store locked; the
        # next run would be refused for no visible reason.
        with pytest.raises(RuntimeError, match="boom"):
            with wl.vector_store_writer("ingest"):
                raise RuntimeError("boom")
        assert not lock_dir.exists()

    def test_the_holder_is_recorded_with_its_purpose(self, lock_dir):
        with wl.vector_store_writer("upload final.pdf"):
            info = wl.read_lock()
        assert info is not None
        assert info.pid == os.getpid()
        assert info.purpose == "upload final.pdf"

    def test_nesting_inside_one_process_is_refused(self, lock_dir):
        # Two threads in the same process would corrupt the store just as badly
        # as two processes, so the second acquisition must fail too.
        with wl.vector_store_writer("outer"):
            with pytest.raises(wl.VectorStoreBusy):
                with wl.vector_store_writer("inner"):
                    pass

    def test_a_missing_parent_directory_is_created(self, tmp_path, monkeypatch):
        nested = tmp_path / "deep" / "deeper" / ".writer.lock"
        monkeypatch.setattr(wl, "_resolve_lock_path", lambda: nested)
        with wl.vector_store_writer("ingest"):
            assert nested.exists()


class TestASecondWriterIsRefused:
    def test_a_live_holder_blocks_another_process(self, lock_dir):
        # This process is certainly alive, so the lock must be respected.
        _write_lock(lock_dir, pid=os.getpid(), purpose="another ingest")

        with pytest.raises(wl.VectorStoreBusy) as excinfo:
            with wl.vector_store_writer("mine"):
                pass

        # The refusal must name the holder: "busy" without saying by whom is
        # the same as the SQLite error it replaced.
        assert "another ingest" in str(excinfo.value)

    def test_the_refusal_leaves_the_existing_lock_untouched(self, lock_dir):
        _write_lock(lock_dir, pid=os.getpid(), purpose="another ingest")

        with pytest.raises(wl.VectorStoreBusy):
            with wl.vector_store_writer("mine"):
                pass

        assert wl.read_lock().purpose == "another ingest"


class TestStaleLocksAreReclaimed:
    def test_a_dead_pid_is_stolen(self, lock_dir):
        # A PID that cannot be open is as good as proof the holder is gone.
        _write_lock(lock_dir, pid=999999999, purpose="crashed run")

        with wl.vector_store_writer("mine"):
            assert wl.read_lock().purpose == "mine"

    def test_a_lock_from_another_host_is_stolen(self, lock_dir):
        # A shared/NFS store: we cannot probe a PID on a machine we do not own,
        # so age is the only signal available.
        _write_lock(lock_dir, host="some-other-machine", purpose="remote run")

        with wl.vector_store_writer("mine"):
            assert wl.read_lock().purpose == "mine"

    def test_an_ancient_lock_is_stolen_even_with_a_live_pid(self, lock_dir):
        # Guards against PID reuse: a recycled PID must not pin the store shut.
        _write_lock(
            lock_dir,
            pid=os.getpid(),
            acquired_at=time.time() - (wl.STALE_AFTER_HOURS + 1) * 3600,
        )

        with wl.vector_store_writer("mine"):
            assert wl.read_lock().purpose == "mine"

    def test_a_corrupt_lock_file_does_not_wedge_the_store(self, lock_dir):
        # Truncated JSON from a half-written file must not raise something the
        # caller cannot act on.
        lock_dir.parent.mkdir(parents=True, exist_ok=True)
        lock_dir.write_text("{not json", encoding="utf-8")

        with wl.vector_store_writer("mine"):
            assert wl.read_lock().purpose == "mine"


class TestAvailabilityOverCoordination:
    def test_an_unwritable_location_warns_instead_of_blocking_uploads(self, tmp_path, monkeypatch, caplog):
        # Availability wins here. If the store lives somewhere we cannot write
        # (read-only mount), refusing every upload would be worse than the
        # corruption risk, so the lock degrades to a warning.
        blocker = tmp_path / "blocked"
        blocker.write_text("i am a file, not a directory", encoding="utf-8")
        monkeypatch.setattr(wl, "_resolve_lock_path", lambda: blocker / ".writer.lock")

        with caplog.at_level("WARNING"):
            with wl.vector_store_writer("upload"):
                pass  # must not raise

        assert any("writer lock" in record.message.lower() for record in caplog.records)
