"""Behavioral invariants for rotation safety, scheduling and recovery."""

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from crystal.core import RotationEngine, RuntimeLock, fingerprint


class CoreTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[1] / ".runtime" / "tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.path = Path(self.temp.name) / "audit.sqlite3"
        self.now = 1000.0
        self.engine = RotationEngine(self.path, interval=10, clock=lambda: self.now)

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def test_success_verifies_then_revokes(self):
        old = self.engine.consumer._key
        result = self.engine.rotate()
        self.assertEqual(result["outcome"], "success")
        self.assertFalse(self.engine.provider.accepts(old))
        self.assertTrue(self.engine.consumer.works(self.engine.provider))
        self.assertEqual(self.engine.provider.active_count, 1)
        events = [e["event"] for e in reversed(self.engine.audit())]
        self.assertLess(events.index("candidate_verified"), events.index("consumer_switched"))
        self.assertLess(events.index("consumer_verified"), events.index("old_revoked"))
        revoked = next(e for e in self.engine.audit() if e["event"] == "old_revoked")
        self.assertEqual(revoked["fingerprint"], fingerprint(old))
        finished = next(e for e in self.engine.audit() if e["event"] == "rotation_finished")
        self.assertEqual(finished["fingerprint"], fingerprint(self.engine.consumer._key))

    def test_verification_failures_restore_working_key(self):
        for failure in ["candidate_check", "consumer_check"]:
            with self.subTest(failure=failure):
                old = self.engine.consumer._key
                result = self.engine.rotate(failure=failure)
                self.assertEqual(result["outcome"], "failed")
                self.assertEqual(self.engine.consumer._key, old)
                self.assertTrue(self.engine.consumer.works(self.engine.provider))
                self.assertEqual(self.engine.provider.active_count, 1)

    def test_old_cleanup_failure_keeps_new_consumer_and_blocks(self):
        old = self.engine.consumer._key
        result = self.engine.rotate(failure="old_cleanup")
        self.assertEqual(result["outcome"], "cleanup_required")
        self.assertNotEqual(self.engine.consumer._key, old)
        self.assertTrue(self.engine.provider.accepts(old))
        self.assertTrue(self.engine.consumer.works(self.engine.provider))
        self.assertEqual(self.engine.rotate()["outcome"], "blocked")
        self.now += 100
        self.assertIsNone(self.engine.tick())
        self.assertEqual(self.engine.provider.active_count, 2)

    def test_candidate_cleanup_failure_preserves_old(self):
        old = self.engine.consumer._key
        result = self.engine.rotate(failure="candidate_cleanup")
        self.assertEqual(result["outcome"], "cleanup_required")
        self.assertEqual(self.engine.consumer._key, old)
        self.assertTrue(self.engine.consumer.works(self.engine.provider))
        self.assertEqual(self.engine.provider.active_count, 2)

    def test_idempotent_retry_does_not_create_another_key(self):
        request_id = str(uuid.uuid4())
        first = self.engine.rotate(request_id)
        key = self.engine.consumer._key
        retry = self.engine.rotate(request_id)
        self.assertTrue(retry["duplicate"])
        self.assertEqual(first["outcome"], retry["outcome"])
        self.assertEqual(self.engine.consumer._key, key)
        self.assertEqual(self.engine.status()["successful_rotations"], 1)

    def test_missed_intervals_coalesce_and_manual_resets_schedule(self):
        self.now += 105
        self.assertEqual(self.engine.tick()["outcome"], "success")
        self.assertIsNone(self.engine.tick())
        self.assertEqual(self.engine.status()["schedule"]["due_in"], 10)
        self.now += 5
        self.engine.rotate()
        self.assertEqual(self.engine.status()["schedule"]["due_in"], 10)

    def test_pause_resume_and_invalid_schedule(self):
        self.engine.configure(10, False)
        self.now += 100
        self.assertIsNone(self.engine.tick())
        self.engine.configure(60, True)
        self.assertEqual(self.engine.status()["schedule"]["due_in"], 60)
        for interval in [True, 9, 86401, 10.5, "10"]:
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                self.engine.configure(interval, True)
        with self.assertRaises(ValueError):
            self.engine.configure(10, "yes")

    def test_overlapping_rotation_returns_busy(self):
        entered, finish = threading.Event(), threading.Event()
        original = self.engine.provider.create

        def slow_create():
            entered.set()
            self.assertTrue(finish.wait(3))
            return original()

        with patch.object(self.engine.provider, "create", side_effect=slow_create):
            worker = threading.Thread(target=self.engine.rotate)
            worker.start()
            self.assertTrue(entered.wait(3))
            try:
                self.assertEqual(self.engine.rotate()["outcome"], "busy")
            finally:
                finish.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(self.engine.status()["successful_rotations"], 1)

    def test_restart_marks_unfinished_and_starts_fresh_session(self):
        old_session, old_key = self.engine.session, self.engine.consumer._key
        with patch.object(self.engine, "_phase", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.engine.rotate()
        self.engine.close()
        self.engine = RotationEngine(self.path, interval=10)
        self.assertNotEqual(self.engine.session, old_session)
        self.assertFalse(self.engine.provider.accepts(old_key))
        self.assertTrue(any(e["event"] == "attempt_interrupted" for e in self.engine.audit()))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT outcome FROM attempts").fetchone()[0], "interrupted")

    def test_audit_database_and_status_never_contain_raw_keys(self):
        old = self.engine.consumer._key
        self.engine.rotate()
        current = self.engine.consumer._key
        public = json.dumps({"status": self.engine.status(), "audit": self.engine.audit()})
        for key in [old, current]:
            self.assertNotIn(key, public)
            self.assertNotIn(key.encode(), self.path.read_bytes())
        self.assertNotIn("crystal_demo_", public)

    def test_invalid_options_do_not_change_credentials(self):
        old = self.engine.consumer._key
        for options in [{"request_id": "not-a-uuid"}, {"failure": "arbitrary"}, {"source": "external"}]:
            with self.assertRaises(ValueError):
                self.engine.rotate(**options)
        self.assertEqual(self.engine.consumer._key, old)

    def test_unexpected_error_blocks_retry_without_exposing_details(self):
        with patch.object(self.engine.provider, "create", side_effect=RuntimeError("private-detail")):
            with self.assertRaises(RuntimeError):
                self.engine.rotate()
        self.assertTrue(self.engine.status()["needs_attention"])
        self.assertEqual(self.engine.rotate()["outcome"], "blocked")
        self.assertNotIn("private-detail", json.dumps(self.engine.audit()))

    def test_runtime_owner_lock_is_exclusive(self):
        path = Path(self.temp.name) / "owner.lock"
        first = RuntimeLock(path)
        try:
            with self.assertRaises(RuntimeError):
                RuntimeLock(path)
        finally:
            first.close()
        second = RuntimeLock(path)
        second.close()


if __name__ == "__main__":
    unittest.main()
