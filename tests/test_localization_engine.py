"""Unit tests for the Localization Engine components.

Tests cover:
- CancellationRegistry (thread-safe cancellation tokens)
- ProgressTracker (thread-safe progress, monotonic guarantee)
- TaskStore (CRUD, persistence, interrupted recovery)
- Engine workspace helpers (log operations)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Add localization-engine to path
ENGINE_DIR = Path(__file__).resolve().parent.parent / "localization-engine"
sys.path.insert(0, str(ENGINE_DIR))

from engine.cancellation import CancellationToken, CancellationRegistry
from engine.progress import ProgressEntry, ProgressTracker
from engine.task_store import TaskRecord, TaskStore
from engine.workspace import (
    ensure_log_dir,
    get_log_path,
    read_log_tail,
    resolve_workspace,
    write_log,
)


# ---------------------------------------------------------------------------
# CancellationToken tests
# ---------------------------------------------------------------------------

class TestCancellationToken(unittest.TestCase):

    def test_initial_state(self):
        token = CancellationToken()
        self.assertFalse(token.is_cancelled())

    def test_cancel(self):
        token = CancellationToken()
        token.cancel()
        self.assertTrue(token.is_cancelled())

    def test_reset(self):
        token = CancellationToken()
        token.cancel()
        self.assertTrue(token.is_cancelled())
        token.reset()
        self.assertFalse(token.is_cancelled())

    def test_thread_safety(self):
        token = CancellationToken()
        errors = []

        def cancel_loop():
            for _ in range(100):
                token.cancel()

        def check_loop():
            for _ in range(100):
                token.is_cancelled()

        threads = [
            threading.Thread(target=cancel_loop) for _ in range(5)
        ] + [
            threading.Thread(target=check_loop) for _ in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertTrue(token.is_cancelled())


# ---------------------------------------------------------------------------
# CancellationRegistry tests
# ---------------------------------------------------------------------------

class TestCancellationRegistry(unittest.TestCase):

    def test_register_and_get(self):
        reg = CancellationRegistry()
        token = reg.register("job-1")
        self.assertIsInstance(token, CancellationToken)
        self.assertIs(reg.get("job-1"), token)

    def test_cancel(self):
        reg = CancellationRegistry()
        reg.register("job-1")
        self.assertTrue(reg.cancel("job-1"))
        self.assertTrue(reg.is_cancelled("job-1"))

    def test_cancel_nonexistent(self):
        reg = CancellationRegistry()
        self.assertFalse(reg.cancel("nonexistent"))
        self.assertFalse(reg.is_cancelled("nonexistent"))

    def test_remove(self):
        reg = CancellationRegistry()
        reg.register("job-1")
        reg.remove("job-1")
        self.assertIsNone(reg.get("job-1"))

    def test_get_nonexistent(self):
        reg = CancellationRegistry()
        self.assertIsNone(reg.get("nonexistent"))

    def test_multiple_jobs(self):
        reg = CancellationRegistry()
        reg.register("job-1")
        reg.register("job-2")
        reg.cancel("job-1")
        self.assertTrue(reg.is_cancelled("job-1"))
        self.assertFalse(reg.is_cancelled("job-2"))


# ---------------------------------------------------------------------------
# ProgressTracker tests
# ---------------------------------------------------------------------------

class TestProgressTracker(unittest.TestCase):

    def test_update_and_get(self):
        tracker = ProgressTracker()
        tracker.update("job-1", "prepare", 50, "preparing...")
        entry = tracker.get("job-1")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.job_id, "job-1")
        self.assertEqual(entry.stage, "prepare")
        self.assertEqual(entry.progress, 50)
        self.assertEqual(entry.message, "preparing...")

    def test_get_nonexistent(self):
        tracker = ProgressTracker()
        self.assertIsNone(tracker.get("nonexistent"))

    def test_monotonic_progress(self):
        """Progress should not decrease within the same stage."""
        tracker = ProgressTracker()
        tracker.update("job-1", "translate", 50, "50%")
        tracker.update("job-1", "translate", 30, "30%")
        entry = tracker.get("job-1")
        self.assertEqual(entry.progress, 50)  # should not go backward

    def test_stage_change_allows_lower(self):
        """Different stage can have lower progress value."""
        tracker = ProgressTracker()
        tracker.update("job-1", "transcribe", 90, "transcribing")
        tracker.update("job-1", "translate", 10, "translating")
        entry = tracker.get("job-1")
        self.assertEqual(entry.stage, "translate")
        self.assertEqual(entry.progress, 10)

    def test_clamp_progress(self):
        tracker = ProgressTracker()
        tracker.update("job-1", "prepare", 150, "over")
        entry = tracker.get("job-1")
        self.assertEqual(entry.progress, 100)

        tracker.update("job-1", "prepare", -10, "under")
        entry = tracker.get("job-1")
        self.assertEqual(entry.progress, 100)  # monotonic: 100 > -10

    def test_remove(self):
        tracker = ProgressTracker()
        tracker.update("job-1", "prepare", 50)
        tracker.remove("job-1")
        self.assertIsNone(tracker.get("job-1"))

    def test_reset(self):
        tracker = ProgressTracker()
        tracker.update("job-1", "translate", 75)
        tracker.reset("job-1")
        self.assertIsNone(tracker.get("job-1"))

    def test_thread_safety(self):
        tracker = ProgressTracker()
        errors = []

        def writer(job_id):
            for i in range(100):
                tracker.update(job_id, "translate", i, f"step {i}")

        threads = [threading.Thread(target=writer, args=(f"job-{j}",))
                    for j in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # All jobs should have entries
        for j in range(10):
            entry = tracker.get(f"job-{j}")
            self.assertIsNotNone(entry)


# ---------------------------------------------------------------------------
# TaskRecord tests
# ---------------------------------------------------------------------------

class TestTaskRecord(unittest.TestCase):

    def test_create(self):
        rec = TaskRecord("job-1", {"source": "test.mp4"})
        self.assertEqual(rec.job_id, "job-1")
        self.assertEqual(rec.status, "pending")
        self.assertEqual(rec.stage, "prepare")
        self.assertEqual(rec.progress, 0)
        self.assertEqual(rec.request_payload["source"], "test.mp4")

    def test_to_dict_roundtrip(self):
        rec = TaskRecord("job-1", {"source": "test.mp4"})
        rec.status = "completed"
        rec.progress = 100
        rec.artifacts = [{"kind": "source_srt", "path": "subtitles/source.srt"}]
        d = rec.to_dict()
        rec2 = TaskRecord.from_dict(d)
        self.assertEqual(rec2.job_id, "job-1")
        self.assertEqual(rec2.status, "completed")
        self.assertEqual(rec2.progress, 100)
        self.assertEqual(len(rec2.artifacts), 1)

    def test_to_api_dict_excludes_payload(self):
        rec = TaskRecord("job-1", {"secret": "api-key"})
        api_dict = rec.to_api_dict()
        self.assertNotIn("request_payload", api_dict)
        self.assertNotIn("secret", api_dict)

    def test_error_fields_optional(self):
        rec = TaskRecord("job-1")
        d = rec.to_dict()
        self.assertNotIn("error_code", d)
        self.assertNotIn("error_detail", d)

        rec.error_code = "FFMPEG_FAILED"
        rec.error_detail = "exit code 1"
        d = rec.to_dict()
        self.assertEqual(d["error_code"], "FFMPEG_FAILED")


# ---------------------------------------------------------------------------
# TaskStore tests
# ---------------------------------------------------------------------------

class TestTaskStore(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_and_get(self):
        store = TaskStore(Path(self.tmpdir))
        rec = store.create("job-1", {"source": "test.mp4"})
        self.assertEqual(rec.job_id, "job-1")

        fetched = store.get("job-1")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.status, "pending")

    def test_exists(self):
        store = TaskStore(Path(self.tmpdir))
        self.assertFalse(store.exists("job-1"))
        store.create("job-1")
        self.assertTrue(store.exists("job-1"))

    def test_update(self):
        store = TaskStore(Path(self.tmpdir))
        store.create("job-1")
        store.update("job-1", status="running", stage="translate", progress=50)
        rec = store.get("job-1")
        self.assertEqual(rec.status, "running")
        self.assertEqual(rec.stage, "translate")
        self.assertEqual(rec.progress, 50)

    def test_add_artifact(self):
        store = TaskStore(Path(self.tmpdir))
        store.create("job-1")
        store.add_artifact("job-1", {"kind": "source_srt", "path": "sub.srt"})
        rec = store.get("job-1")
        self.assertEqual(len(rec.artifacts), 1)
        self.assertEqual(rec.artifacts[0]["kind"], "source_srt")

    def test_delete(self):
        store = TaskStore(Path(self.tmpdir))
        store.create("job-1")
        self.assertTrue(store.delete("job-1"))
        self.assertFalse(store.exists("job-1"))
        self.assertFalse(store.delete("nonexistent"))

    def test_list_all(self):
        store = TaskStore(Path(self.tmpdir))
        store.create("job-1")
        store.create("job-2")
        store.create("job-3")
        tasks = store.list_all()
        self.assertEqual(len(tasks), 3)

    def test_persistence(self):
        """Tasks should survive store re-creation (simulating restart)."""
        store1 = TaskStore(Path(self.tmpdir))
        store1.create("job-1", {"source": "test.mp4"})
        store1.update("job-1", status="completed", progress=100)

        # Create a new store from the same directory
        store2 = TaskStore(Path(self.tmpdir))
        rec = store2.get("job-1")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, "completed")
        self.assertEqual(rec.progress, 100)

    def test_interrupted_recovery(self):
        """In-flight tasks should be marked interrupted on restart."""
        store1 = TaskStore(Path(self.tmpdir))
        store1.create("job-running")
        store1.update("job-running", status="running")
        store1.create("job-pending")
        store1.update("job-pending", status="pending")
        store1.create("job-completed")
        store1.update("job-completed", status="completed")

        # Simulate restart
        store2 = TaskStore(Path(self.tmpdir))
        self.assertEqual(store2.get("job-running").status, "interrupted")
        self.assertEqual(store2.get("job-pending").status, "interrupted")
        self.assertEqual(store2.get("job-completed").status, "completed")

    def test_update_nonexistent(self):
        store = TaskStore(Path(self.tmpdir))
        result = store.update("nonexistent", status="error")
        self.assertIsNone(result)

    def test_thread_safety(self):
        store = TaskStore(Path(self.tmpdir))
        errors = []

        def create_and_update(i):
            job_id = f"job-{i}"
            store.create(job_id)
            store.update(job_id, status="running", progress=i * 10)

        threads = [threading.Thread(target=create_and_update, args=(i,))
                    for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        tasks = store.list_all()
        self.assertEqual(len(tasks), 10)


# ---------------------------------------------------------------------------
# Workspace helper tests
# ---------------------------------------------------------------------------

class TestWorkspaceHelpers(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_resolve_workspace(self):
        ws = resolve_workspace(self.tmpdir)
        self.assertTrue(ws.exists())

    def test_resolve_nonexistent(self):
        with self.assertRaises(ValueError):
            resolve_workspace("/nonexistent/path/xyz")

    def test_resolve_empty(self):
        with self.assertRaises(ValueError):
            resolve_workspace("")

    def test_ensure_log_dir(self):
        log_dir = ensure_log_dir(Path(self.tmpdir))
        self.assertTrue(log_dir.exists())
        self.assertEqual(log_dir.name, "logs")

    def test_get_log_path(self):
        path = get_log_path(Path(self.tmpdir), "test.log")
        self.assertEqual(path.name, "test.log")

    def test_write_and_read_log(self):
        ensure_log_dir(Path(self.tmpdir))
        write_log(Path(self.tmpdir), "Line 1")
        write_log(Path(self.tmpdir), "Line 2")
        write_log(Path(self.tmpdir), "Line 3")

        lines, truncated = read_log_tail(Path(self.tmpdir), max_lines=10)
        self.assertEqual(len(lines), 3)
        self.assertFalse(truncated)
        self.assertIn("Line 1", lines[0])

    def test_log_tail_truncation(self):
        ensure_log_dir(Path(self.tmpdir))
        for i in range(50):
            write_log(Path(self.tmpdir), f"Line {i}")

        lines, truncated = read_log_tail(Path(self.tmpdir), max_lines=10)
        self.assertEqual(len(lines), 10)
        self.assertTrue(truncated)
        self.assertIn("Line 49", lines[-1])

    def test_read_nonexistent_log(self):
        lines, truncated = read_log_tail(Path(self.tmpdir), max_lines=10)
        self.assertEqual(lines, [])
        self.assertFalse(truncated)


if __name__ == "__main__":
    unittest.main()
