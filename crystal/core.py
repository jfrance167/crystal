"""Staged rotation, metadata-only audit storage, and a foreground schedule."""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def fingerprint(key: str) -> str:
    """Return an identifier, never the credential itself."""
    return hashlib.sha256(key.encode("ascii")).hexdigest()[:16]


class DemoProvider:
    """In-memory authority accepting only credentials created by this demo."""

    def __init__(self) -> None:
        self._digests: set[str] = set()

    def create(self) -> str:
        key = "crystal_demo_" + secrets.token_urlsafe(32)
        self._digests.add(hashlib.sha256(key.encode("ascii")).hexdigest())
        return key

    def accepts(self, key: str) -> bool:
        digest = hashlib.sha256(key.encode("ascii")).hexdigest()
        return digest in self._digests

    def revoke(self, key: str) -> None:
        self._digests.discard(hashlib.sha256(key.encode("ascii")).hexdigest())

    @property
    def active_count(self) -> int:
        return len(self._digests)


class DemoConsumer:
    """A demo client whose raw synthetic credential never leaves memory."""

    def __init__(self, key: str) -> None:
        self._key = key

    def switch(self, key: str) -> None:
        self._key = key

    def works(self, provider: DemoProvider) -> bool:
        return provider.accepts(self._key)


class DemoFailure(Exception):
    """A deliberate synthetic failure; no raw exception text is audited."""


class RuntimeLock:
    """Prevent two demo processes from owning the same metadata store."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a+b")
        self._file.seek(0, 2)
        if self._file.tell() == 0:
            self._file.write(b"0")
            self._file.flush()
        self._file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._file.close()
            raise RuntimeError("This runtime is already in use. Choose another --data-dir.") from None

    def close(self) -> None:
        self._file.close()


class RotationEngine:
    """Serialize staged rotations and persist only allowlisted metadata.

    A full restart starts a new disposable credential session. SQLite preserves
    evidence of unfinished attempts, but is not a production secret store.
    """

    FAILURES = frozenset({"none", "candidate_check", "consumer_check", "old_cleanup", "candidate_cleanup"})

    def __init__(self, db_path: Path | str, interval: int = 300, enabled: bool = True, clock=time.monotonic) -> None:
        self._validate_interval(interval)
        self._lock = threading.RLock()
        self._clock = clock
        self.interval = interval
        self.enabled = enabled
        self.next_due = clock() + interval
        self.session = str(uuid.uuid4())
        self.last_result: dict | None = None
        self.provider = DemoProvider()
        self.consumer = DemoConsumer(self.provider.create())
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, session TEXT NOT NULL, source TEXT NOT NULL,
                started TEXT NOT NULL, finished TEXT, phase TEXT NOT NULL,
                outcome TEXT NOT NULL, fingerprint TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, session TEXT NOT NULL, attempt TEXT,
                timestamp TEXT NOT NULL, event TEXT NOT NULL, outcome TEXT NOT NULL,
                fingerprint TEXT
            );
        """)
        unfinished = self._db.execute("SELECT id FROM attempts WHERE outcome='running'").fetchall()
        for row in unfinished:
            self._db.execute("UPDATE attempts SET outcome='interrupted', finished=? WHERE id=?", (utc_now(), row["id"]))
            self._event("attempt_interrupted", "interrupted", row["id"])
        self._db.commit()
        self._event("session_started", "ready", fp=fingerprint(self.consumer._key))

    @staticmethod
    def _validate_interval(interval: int) -> None:
        if type(interval) is not int or not 10 <= interval <= 86400:
            raise ValueError("Interval must be an integer from 10 to 86400 seconds.")

    def _event(self, event: str, outcome: str, attempt: str | None = None, fp: str | None = None) -> None:
        # Only internal identifiers reach this method: no keys, request bodies,
        # arbitrary exception strings, or user-supplied descriptions.
        if attempt and fp is None:
            row = self._db.execute("SELECT fingerprint FROM attempts WHERE id=?", (attempt,)).fetchone()
            fp = row[0] if row else None
        self._db.execute(
            "INSERT INTO events(session,attempt,timestamp,event,outcome,fingerprint) VALUES(?,?,?,?,?,?)",
            (self.session, attempt, utc_now(), event, outcome, fp),
        )
        self._db.commit()

    def _phase(self, attempt: str, phase: str, fp: str | None = None) -> None:
        self._db.execute("UPDATE attempts SET phase=?, fingerprint=COALESCE(fingerprint,?) WHERE id=?", (phase, fp, attempt))
        self._event(phase, "running", attempt, fp)

    def rotate(self, request_id: str | None = None, failure: str = "none", source: str = "manual") -> dict:
        """Rotate once; an idempotency identifier makes a retry read its result."""
        if failure not in self.FAILURES or source not in {"manual", "scheduled"}:
            raise ValueError("Unsupported rotation option.")
        attempt = request_id or str(uuid.uuid4())
        try:
            if str(uuid.UUID(attempt)) != attempt:
                raise ValueError
        except (ValueError, AttributeError, TypeError):
            raise ValueError("Request identifier must be a canonical UUID.") from None
        if not self._lock.acquire(blocking=False):
            return {"outcome": "busy"}
        try:
            previous = self._db.execute("SELECT * FROM attempts WHERE id=?", (attempt,)).fetchone()
            if previous:
                return {"attempt": attempt, "outcome": previous["outcome"], "duplicate": True}
            if self.last_result and self.last_result["outcome"] == "cleanup_required":
                return {"outcome": "blocked", "reason": "Restart the disposable demo to clear pending cleanup."}
            old = self.consumer._key
            candidate = None
            switched = False
            phase = "creating"
            self._db.execute(
                "INSERT INTO attempts(id,session,source,started,phase,outcome) VALUES(?,?,?,?,?,?)",
                (attempt, self.session, source, utc_now(), phase, "running"),
            )
            self._db.commit()
            try:
                self._phase(attempt, phase)
                candidate = self.provider.create()
                phase = "candidate_created"
                self._phase(attempt, phase, fingerprint(candidate))
                if failure in {"candidate_check", "candidate_cleanup"} or not self.provider.accepts(candidate):
                    raise DemoFailure
                phase = "candidate_verified"
                self._phase(attempt, phase)
                self.consumer.switch(candidate)
                switched = True
                phase = "consumer_switched"
                self._phase(attempt, phase)
                if failure == "consumer_check" or not self.consumer.works(self.provider):
                    raise DemoFailure
                phase = "consumer_verified"
                self._phase(attempt, phase)
                if failure == "old_cleanup":
                    raise DemoFailure
                self.provider.revoke(old)
                phase = "old_revoked"
                self._phase(attempt, phase, fingerprint(old))
                outcome = "success"
            except DemoFailure:
                # After successful consumer verification, keep the working new
                # credential and expose failed old-key cleanup as actionable.
                if phase == "consumer_verified":
                    outcome = "cleanup_required"
                else:
                    if switched:
                        self.consumer.switch(old)
                    if candidate and failure != "candidate_cleanup":
                        self.provider.revoke(candidate)
                    outcome = "cleanup_required" if failure == "candidate_cleanup" else "failed"
                self._event("rotation_failed", outcome, attempt)
            self._db.execute("UPDATE attempts SET outcome=?, finished=? WHERE id=?", (outcome, utc_now(), attempt))
            self._event("rotation_finished", outcome, attempt)
            self.last_result = {
                "attempt": attempt, "outcome": outcome, "phase": phase,
                "consumer_healthy": self.consumer.works(self.provider),
                "old_key_rejected": not self.provider.accepts(old),
                "active_keys": self.provider.active_count,
            }
            self.next_due = self._clock() + self.interval
            return dict(self.last_result)
        except Exception:
            # An unexpected storage/provider error leaves the attempt evidence
            # intact and blocks further rotation. A restart resets the lab.
            self.last_result = {"outcome": "cleanup_required", "phase": "internal_error"}
            raise
        finally:
            self._lock.release()

    def configure(self, interval: int, enabled: bool) -> dict:
        self._validate_interval(interval)
        if type(enabled) is not bool:
            raise ValueError("Enabled must be a boolean.")
        with self._lock:
            self.interval, self.enabled = interval, enabled
            self.next_due = self._clock() + interval
            self._event("schedule_updated", "enabled" if enabled else "paused")
            return self.status()

    def tick(self) -> dict | None:
        """Coalesce missed intervals into one due rotation, without a backlog."""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            if not self.enabled or self._clock() < self.next_due:
                return None
            if self.last_result and self.last_result["outcome"] == "cleanup_required":
                return None
            return self.rotate(source="scheduled")
        finally:
            self._lock.release()

    def status(self) -> dict:
        with self._lock:
            count = self._db.execute("SELECT COUNT(*) FROM attempts WHERE session=? AND outcome='success'", (self.session,)).fetchone()[0]
            return {
                "session": self.session, "fingerprint": fingerprint(self.consumer._key),
                "consumer_healthy": self.consumer.works(self.provider),
                "active_keys": self.provider.active_count, "successful_rotations": count,
                "schedule": {"enabled": self.enabled, "interval": self.interval,
                             "due_in": max(0, round(self.next_due - self._clock())) if self.enabled else None},
                "last_result": self.last_result,
                "needs_attention": bool(self.last_result and self.last_result["outcome"] == "cleanup_required"),
            }

    def audit(self, limit: int = 80) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]

    def close(self) -> None:
        with self._lock:
            self._db.close()
