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
from unittest.mock import MagicMock, patch

# Add localization-engine to path
ENGINE_DIR = Path(__file__).resolve().parent.parent / "localization-engine"
sys.path.insert(0, str(ENGINE_DIR))

from engine.cancellation import CancellationToken, CancellationRegistry
from engine.pipeline import PipelineRunner
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


class TestPipelineTranslationConcurrency(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_translation_batches_run_concurrently(self):
        from job_models import SubtitleSegment

        active = 0
        max_active = 0
        lock = threading.Lock()

        class FakeProvider:
            def translate_batch(self, segments, config, source_lang, target_lang, glossary=None):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.05)
                with lock:
                    active -= 1
                return [{"id": item["id"], "text": f"tx-{item['id']}"} for item in segments]

            def close(self):
                pass

        ws = Path(self.tmpdir)
        (ws / "translation").mkdir(parents=True, exist_ok=True)
        segments = [
            SubtitleSegment(index=i, start=float(i), end=float(i + 1), text="abcd")
            for i in range(1, 6)
        ]
        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())

        with patch("engine.pipeline.get_provider", return_value=FakeProvider()):
            ok = runner._run_translation(
                "job-1",
                ws,
                segments,
                {
                    "translation": {
                        "provider": "openai_compatible",
                        "max_batch_chars": 4,
                        "concurrency": 3,
                    }
                },
                "en",
                "zh-CN",
                CancellationToken(),
            )

        self.assertTrue(ok)
        self.assertGreaterEqual(max_active, 2)
        self.assertEqual([s.translation for s in segments], [f"tx-{i}" for i in range(1, 6)])

    def test_dubbing_fails_when_translation_falls_back_to_source(self):
        from job_models import SubtitleSegment

        class EmptyProvider:
            def translate_batch(self, segments, config, source_lang, target_lang, glossary=None):
                return []

            def close(self):
                pass

        ws = Path(self.tmpdir)
        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        runner._store.create("job-missing-translation", {"workspace_dir": str(ws)})
        segments = [
            SubtitleSegment(index=1, start=0.0, end=1.0, text="source text")
        ]

        with patch("engine.pipeline.get_provider", return_value=EmptyProvider()):
            ok = runner._run_translation(
                "job-missing-translation",
                ws,
                segments,
                {
                    "dubbing_enabled": True,
                    "translation": {
                        "provider": "openai_compatible",
                        "max_batch_items": 1,
                        "retry_count": 0,
                    },
                },
                "ja",
                "zh-CN",
                CancellationToken(),
            )

        self.assertFalse(ok)
        rec = runner._store.get("job-missing-translation")
        self.assertEqual(rec.error_code, "TRANSLATION_INCOMPLETE")
        self.assertFalse((ws / "translation" / "checkpoints" / "completed_ids.json").exists())

    def test_translation_checkpoint_ignored_when_segment_translations_missing(self):
        from job_models import SubtitleSegment

        calls = 0

        class FakeProvider:
            def translate_batch(self, segments, config, source_lang, target_lang, glossary=None):
                nonlocal calls
                calls += 1
                return [{"id": item["id"], "text": f"tx-{item['id']}"} for item in segments]

            def close(self):
                pass

        ws = Path(self.tmpdir)
        checkpoint_dir = ws / "translation" / "checkpoints"
        checkpoint_dir.mkdir(parents=True)
        (checkpoint_dir / "completed_ids.json").write_text(
            json.dumps({"completed_ids": [1]}),
            encoding="utf-8",
        )
        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        segments = [SubtitleSegment(index=1, start=0.0, end=1.0, text="source text")]

        with patch("engine.pipeline.get_provider", return_value=FakeProvider()):
            ok = runner._run_translation(
                "job-checkpoint",
                ws,
                segments,
                {
                    "translation": {
                        "provider": "openai_compatible",
                        "max_batch_items": 1,
                    },
                },
                "ja",
                "zh-CN",
                CancellationToken(),
            )

        self.assertTrue(ok)
        self.assertEqual(calls, 1)
        self.assertEqual(segments[0].translation, "tx-1")


class TestPipelineTTSConcurrency(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_tts_segments_run_concurrently(self):
        from job_models import SubtitleSegment
        from tts.base import TTSResult

        active = 0
        max_active = 0
        lock = threading.Lock()

        class FakeTTSProvider:
            def synthesize(self, text, language, voice, output_path, options):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.05)
                Path(output_path).write_bytes(b"wav")
                with lock:
                    active -= 1
                return TTSResult(output_path=Path(output_path), duration_seconds=0.5)

            def list_voices(self, language=None):
                return []

        ws = Path(self.tmpdir)
        segments = [
            SubtitleSegment(index=i, start=float(i), end=float(i) + 0.5, text=f"src-{i}", translation=f"tx-{i}")
            for i in range(1, 6)
        ]
        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())

        with patch("tts.get_provider", return_value=FakeTTSProvider()), \
             patch("tts.timing.adjust_timing", return_value=(0.5, "", 1.0)):
             ok = runner._run_tts(
                "job-tts",
                ws,
                segments,
                {"tts_provider": "fake", "tts_voice": "voice", "tts_concurrency": 3, "tts_consistency_mode": "fast"},
                "zh-CN",
                CancellationToken(),
            )

        self.assertTrue(ok)
        self.assertGreaterEqual(max_active, 2)
        self.assertTrue((ws / "audio" / "tts" / "seg_0001.wav").exists())
        index = json.loads((ws / "audio" / "tts" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual([item["index"] for item in index], [1, 2, 3, 4, 5])

    def test_tts_timing_targets_next_segment_window(self):
        from job_models import SubtitleSegment
        from tts.base import TTSResult

        class FakeTTSProvider:
            def synthesize(self, text, language, voice, output_path, options):
                Path(output_path).write_bytes(b"wav")
                return TTSResult(output_path=Path(output_path), duration_seconds=3.0)

            def list_voices(self, language=None):
                return []

        ws = Path(self.tmpdir)
        segments = [
            SubtitleSegment(index=1, start=0.0, end=2.0, text="src-1", translation="tx-1"),
            SubtitleSegment(index=2, start=1.0, end=2.0, text="src-2", translation="tx-2"),
        ]
        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        targets = []

        def fake_adjust(input_audio, output_audio, actual_duration, target_duration):
            targets.append(target_duration)
            return target_duration, "", 1.0

        with patch("tts.get_provider", return_value=FakeTTSProvider()), \
             patch("tts.timing.adjust_timing", side_effect=fake_adjust):
            ok = runner._run_tts(
                "job-tts",
                ws,
                segments,
                {
                    "tts_provider": "fake",
                    "tts_voice": "voice",
                    "tts_concurrency": 1,
                    "tts_consistency_mode": "fast",
                },
                "zh-CN",
                CancellationToken(),
            )

        self.assertTrue(ok)
        self.assertAlmostEqual(targets[0], 1.8, places=2)
        self.assertAlmostEqual(targets[1], 1.0, places=2)

    def test_stable_tts_chunks_extract_segment_windows(self):
        from job_models import SubtitleSegment
        from tts.base import TTSResult

        synthesized_texts = []

        class FakeTTSProvider:
            def synthesize(self, text, language, voice, output_path, options):
                synthesized_texts.append(text)
                Path(output_path).write_bytes(b"chunk")
                return TTSResult(output_path=Path(output_path), duration_seconds=4.0)

            def list_voices(self, language=None):
                return []

        ws = Path(self.tmpdir)
        segments = [
            SubtitleSegment(index=1, start=0.0, end=2.0, text="src-1", translation="hello"),
            SubtitleSegment(index=2, start=2.0, end=4.0, text="src-2", translation="world"),
        ]
        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        extractions = []
        adjusted_inputs = []

        def fake_extract(input_audio, output_audio, start_offset, duration):
            extractions.append((Path(input_audio).name, Path(output_audio).name, start_offset, duration))
            Path(output_audio).write_bytes(b"slice")
            return True

        def fake_adjust(input_audio, output_audio, actual_duration, target_duration):
            adjusted_inputs.append(Path(input_audio).name)
            Path(output_audio).write_bytes(b"seg")
            return target_duration, "", 1.0

        with patch("tts.get_provider", return_value=FakeTTSProvider()), \
             patch("tts.timing.extract_audio_window", side_effect=fake_extract), \
             patch("tts.timing.adjust_timing", side_effect=fake_adjust):
            ok = runner._run_tts(
                "job-tts",
                ws,
                segments,
                {"tts_provider": "fake", "tts_voice": "voice", "tts_consistency_mode": "stable"},
                "zh-CN",
                CancellationToken(),
            )

        self.assertTrue(ok)
        self.assertEqual(synthesized_texts, ["hello world"])
        self.assertEqual(len(extractions), 2)
        self.assertEqual([item[0] for item in extractions], ["chunk_0000.wav", "chunk_0000.wav"])
        self.assertGreater(extractions[1][2], extractions[0][2])
        self.assertEqual(adjusted_inputs, ["chunk_0000_seg_0001.wav", "chunk_0000_seg_0002.wav"])
        self.assertTrue((ws / "audio" / "tts" / "seg_0001.wav").exists())
        self.assertTrue((ws / "audio" / "tts" / "seg_0002.wav").exists())

    def test_tts_skip_removes_stale_segment_audio(self):
        from job_models import SubtitleSegment
        from tts.base import TTSResult

        class FakeTTSProvider:
            def synthesize(self, text, language, voice, output_path, options):
                Path(output_path).write_bytes(b"wav")
                return TTSResult(output_path=Path(output_path), duration_seconds=0.2)

            def list_voices(self, language=None):
                return []

        ws = Path(self.tmpdir)
        tts_dir = ws / "audio" / "tts"
        tts_dir.mkdir(parents=True)
        stale = tts_dir / "seg_0001.wav"
        segment1_path = tts_dir / "seg_0001.wav"
        segments = [
            SubtitleSegment(index=1, start=0.0, end=1.0, text="src-1", translation="tx-1"),
            SubtitleSegment(index=2, start=0.03, end=1.0, text="src-2", translation="tx-2"),
        ]
        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())

        with patch("tts.get_provider", return_value=FakeTTSProvider()), \
             patch("tts.timing.adjust_timing", return_value=(0.2, "", 1.0)):
            ok = runner._run_tts(
                "job-tts",
                ws,
                segments,
                {"tts_provider": "fake", "tts_voice": "voice", "tts_concurrency": 1, "tts_consistency_mode": "fast"},
                "zh-CN",
                CancellationToken(),
            )

        self.assertTrue(ok)
        self.assertTrue(segment1_path.exists())
        self.assertTrue((tts_dir / "seg_0002.wav").exists())

    def test_tts_writes_control_report_with_options(self):
        from job_models import SubtitleSegment
        from tts.base import TTSResult

        class FakeTTSProvider:
            def synthesize(self, text, language, voice, output_path, options):
                Path(output_path).write_bytes(b"wav")
                return TTSResult(
                    output_path=Path(output_path),
                    duration_seconds=0.4,
                    cached=True,
                    mode=options.get("qwen_mode", ""),
                )

            def list_voices(self, language=None):
                return []

        ws = Path(self.tmpdir)
        segments = [
            SubtitleSegment(index=1, start=0.0, end=1.0, text="src-1", translation="tx-1"),
        ]
        store = TaskStore(ws / "data")
        store.create("job-tts-report", {"workspace_dir": str(ws)})
        runner = PipelineRunner(store, ProgressTracker())

        with patch("tts.get_provider", return_value=FakeTTSProvider()), \
             patch("tts.timing.adjust_timing", return_value=(0.4, "", 1.0)):
            ok = runner._run_tts(
                "job-tts-report",
                ws,
                segments,
                {
                    "tts_provider": "fake",
                    "tts_voice": "voice",
                    "tts_options": {"qwen_mode": "voice_design", "instruct": "warm"},
                },
                "zh-CN",
                CancellationToken(),
            )

        self.assertTrue(ok)
        report_path = ws / "audio" / "tts" / "tts_control_report.json"
        self.assertTrue(report_path.exists())
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["options"]["qwen_mode"], "voice_design")
        self.assertEqual(payload["cached_segments"], 1)
        self.assertEqual(payload["segments"][0]["mode"], "voice_design")
        rec = store.get("job-tts-report")
        self.assertTrue(any(a["kind"] == "tts_control_report" for a in rec.artifacts))

    def test_qwen3_tts_defaults_to_stable_seed_in_report(self):
        from job_models import SubtitleSegment
        from tts.base import TTSResult

        seen_options = []

        class FakeQwenProvider:
            supports_concurrency = False

            def synthesize(self, text, language, voice, output_path, options):
                seen_options.append(dict(options))
                Path(output_path).write_bytes(b"wav")
                return TTSResult(
                    output_path=Path(output_path),
                    duration_seconds=0.4,
                    mode=options.get("qwen_mode", ""),
                )

            def list_voices(self, language=None):
                return []

        ws = Path(self.tmpdir)
        segments = [
            SubtitleSegment(index=1, start=0.0, end=1.0, text="src-1", translation="tx-1"),
        ]
        store = TaskStore(ws / "data")
        store.create("job-qwen-seed", {"workspace_dir": str(ws)})
        runner = PipelineRunner(store, ProgressTracker())

        with patch("tts.get_provider", return_value=FakeQwenProvider()), \
             patch("tts.timing.adjust_timing", return_value=(0.4, "", 1.0)), \
             patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"status":"ok","capabilities":{"custom_voice":true}}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp
            ok = runner._run_tts(
                "job-qwen-seed",
                ws,
                segments,
                {
                    "tts_provider": "qwen3-tts",
                    "tts_voice": "Vivian",
                    "tts_options": {"qwen_mode": "custom_voice"},
                    "tts_concurrency": 4,
                },
                "zh-CN",
                CancellationToken(),
            )

        self.assertTrue(ok)
        self.assertEqual(seen_options[0]["seed"], 42)
        self.assertEqual(seen_options[0]["seed_policy"], "default_stable")
        report_path = ws / "audio" / "tts" / "tts_control_report.json"
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["options"]["seed"], 42)
        self.assertEqual(payload["options"]["seed_policy"], "default_stable")

    def test_tts_empty_input_returns_specific_error(self):
        from job_models import SubtitleSegment

        ws = Path(self.tmpdir)
        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        runner._store.create("job-empty-tts", {"workspace_dir": str(ws)})

        with patch("tts.get_provider") as mock_get:
            ok = runner._run_tts(
                "job-empty-tts", ws, [],
                {"tts_provider": "edge-tts", "tts_voice": "voice"},
                "zh-CN", CancellationToken(),
            )

        self.assertFalse(ok)
        rec = runner._store.get("job-empty-tts")
        self.assertEqual(rec.error_code, "TTS_EMPTY_INPUT")

    def test_tts_blank_text_segments_returns_empty_input(self):
        from job_models import SubtitleSegment

        ws = Path(self.tmpdir)
        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        runner._store.create("job-blank-tts", {"workspace_dir": str(ws)})
        segments = [
            SubtitleSegment(index=1, start=0.0, end=1.0, text="   ", translation=""),
            SubtitleSegment(index=2, start=1.0, end=2.0, text="", translation=""),
        ]

        with patch("tts.get_provider") as mock_get:
            ok = runner._run_tts(
                "job-blank-tts", ws, segments,
                {"tts_provider": "edge-tts", "tts_voice": "voice"},
                "zh-CN", CancellationToken(),
            )

        self.assertFalse(ok)
        rec = runner._store.get("job-blank-tts")
        self.assertEqual(rec.error_code, "TTS_EMPTY_INPUT")

    def test_tts_engine_exception_returns_generation_failed(self):
        from job_models import SubtitleSegment

        class BrokenProvider:
            def synthesize(self, text, language, voice, output_path, options):
                raise RuntimeError("model crashed")
            def list_voices(self, language=None):
                return []

        ws = Path(self.tmpdir)
        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        runner._store.create("job-broken-tts", {"workspace_dir": str(ws)})
        segments = [SubtitleSegment(index=1, start=0.0, end=1.0, text="hello", translation="你好")]

        with patch("tts.get_provider", return_value=BrokenProvider()):
            ok = runner._run_tts(
                "job-broken-tts", ws, segments,
                {"tts_provider": "broken", "tts_voice": "v", "tts_concurrency": 1},
                "zh-CN", CancellationToken(),
            )

        self.assertFalse(ok)
        rec = runner._store.get("job-broken-tts")
        self.assertEqual(rec.error_code, "TTS_GENERATION_FAILED")

    def test_tts_auth_error_returns_auth_failed(self):
        from job_models import SubtitleSegment
        from tts.base import TTSAuthError

        class BrokenProvider:
            def synthesize(self, text, language, voice, output_path, options):
                raise TTSAuthError("Invalid API key")
            def list_voices(self, language=None):
                return []

        ws = Path(self.tmpdir)
        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        runner._store.create("job-auth-tts", {"workspace_dir": str(ws)})
        segments = [SubtitleSegment(index=1, start=0.0, end=1.0, text="hello", translation="浣犲ソ")]

        with patch("tts.get_provider", return_value=BrokenProvider()):
            ok = runner._run_tts(
                "job-auth-tts", ws, segments,
                {"tts_provider": "broken", "tts_voice": "v", "tts_concurrency": 1},
                "zh-CN", CancellationToken(),
            )

        self.assertFalse(ok)
        rec = runner._store.get("job-auth-tts")
        self.assertEqual(rec.error_code, "TTS_AUTH_FAILED")
        self.assertIn("Invalid API key", rec.error_detail)

    def test_tts_provider_exception_returns_generation_failed(self):
        from job_models import SubtitleSegment

        class BrokenProvider:
            def synthesize(self, text, language, voice, output_path, options):
                raise RuntimeError("silent failure")
            def list_voices(self, language=None):
                return []

        ws = Path(self.tmpdir)
        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        runner._store.create("job-no-out", {"workspace_dir": str(ws)})
        segments = [SubtitleSegment(index=1, start=0.0, end=1.0, text="hello", translation="你好")]

        with patch("tts.get_provider", return_value=BrokenProvider()):
            ok = runner._run_tts(
                "job-no-out", ws, segments,
                {"tts_provider": "broken", "tts_voice": "v", "tts_concurrency": 1},
                "zh-CN", CancellationToken(),
            )

        self.assertFalse(ok)
        rec = runner._store.get("job-no-out")
        self.assertEqual(rec.error_code, "TTS_GENERATION_FAILED")

    def test_tts_zero_byte_files_returns_zero_byte_audio(self):
        from job_models import SubtitleSegment

        class BrokenProvider:
            def synthesize(self, text, language, voice, output_path, options):
                raise RuntimeError("fail")
            def list_voices(self, language=None):
                return []

        ws = Path(self.tmpdir)
        tts_dir = ws / "audio" / "tts"
        tts_dir.mkdir(parents=True)
        (tts_dir / "seg_9999.wav").write_bytes(b"")
        (tts_dir / "seg_9998.wav").write_bytes(b"")
        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        runner._store.create("job-zero", {"workspace_dir": str(ws)})
        segments = [SubtitleSegment(index=1, start=0.0, end=1.0, text="hello", translation="你好")]

        with patch("tts.get_provider", return_value=BrokenProvider()):
            ok = runner._run_tts(
                "job-zero", ws, segments,
                {"tts_provider": "broken", "tts_voice": "v", "tts_concurrency": 1},
                "zh-CN", CancellationToken(),
            )

        self.assertFalse(ok)
        rec = runner._store.get("job-zero")
        self.assertEqual(rec.error_code, "TTS_ZERO_BYTE_AUDIO")

    def test_qwen3_tts_fails_with_service_down(self):
        from job_models import SubtitleSegment
        from tts.base import TTSResult

        class FakeQwenProvider:
            supports_concurrency = False
            def synthesize(self, text, language, voice, output_path, options):
                Path(output_path).write_bytes(b"wav")
                return TTSResult(
                    output_path=Path(output_path),
                    duration_seconds=0.4,
                    mode=options.get("qwen_mode", ""),
                )
            def list_voices(self, language=None):
                return []

        ws = Path(self.tmpdir)
        segments = [
            SubtitleSegment(index=1, start=0.0, end=1.0, text="src-1", translation="tx-1"),
        ]
        store = TaskStore(ws / "data")
        store.create("job-tts-down", {"workspace_dir": str(ws)})
        runner = PipelineRunner(store, ProgressTracker())

        import urllib.error
        with patch("tts.get_provider", return_value=FakeQwenProvider()), \
             patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("Connection refused")):
            ok = runner._run_tts(
                "job-tts-down",
                ws,
                segments,
                {
                    "tts_provider": "qwen3-tts",
                    "tts_voice": "Vivian",
                    "tts_options": {"qwen_mode": "custom_voice"},
                },
                "zh-CN",
                CancellationToken(),
            )

        self.assertFalse(ok)
        rec = store.get("job-tts-down")
        self.assertEqual(rec.error_code, "TTS_SERVICE_DOWN")


class TestTTSTiming(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_adjust_timing_speeds_up_long_audio(self):
        from tts.timing import adjust_timing

        root = Path(self.tmpdir)
        source = root / "source.wav"
        output = root / "out.wav"
        source.write_bytes(b"wav")

        with patch("tts.timing.subprocess.run") as mock_run:
            adjusted, warning, speed = adjust_timing(source, output, 2.0, 1.0)

        cmd = mock_run.call_args.args[0]
        self.assertIn("atempo=1.500", cmd)
        self.assertAlmostEqual(adjusted, 2.0 / 1.5, places=2)
        self.assertIn("sped up", warning)
        self.assertEqual(speed, 1.5)

    def test_adjust_timing_caps_speed_at_max(self):
        from tts.timing import adjust_timing

        root = Path(self.tmpdir)
        source = root / "source.wav"
        output = root / "out.wav"
        source.write_bytes(b"wav")

        with patch("tts.timing.subprocess.run") as mock_run:
            adjusted, warning, speed = adjust_timing(source, output, 5.0, 1.0)

        cmd = mock_run.call_args.args[0]
        filter_arg = cmd[cmd.index("-filter:a") + 1]
        self.assertIn("atempo=1.500", filter_arg)
        self.assertAlmostEqual(adjusted, 5.0 / 1.5, places=2)
        self.assertIn("sped up", warning)
        self.assertEqual(speed, 1.5)


class TestPipelineAudioMix(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_audio_mix_uses_request_original_volume(self):
        from job_models import SubtitleSegment

        ws = Path(self.tmpdir)
        source_video = ws / "source.mp4"
        tts_dir = ws / "audio" / "tts"
        tts_dir.mkdir(parents=True)
        source_video.write_bytes(b"video")
        (tts_dir / "seg_0001.wav").write_bytes(b"wav")

        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        segments = [SubtitleSegment(index=1, start=0.0, end=1.0, text="hello", translation="你好")]

        def fake_mix_audio(**kwargs):
            kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
            kwargs["output_path"].write_bytes(b"mp4")
            return {"success": True}

        with patch("audio.mix.mix_audio", side_effect=fake_mix_audio) as mock_mix:
            ok = runner._run_audio_mix(
                "job-audio",
                ws,
                segments,
                source_video,
                "zh-CN",
                {"original_volume": 0.12},
                CancellationToken(),
            )

        self.assertTrue(ok)
        self.assertEqual(mock_mix.call_args.kwargs["original_volume"], 0.12)
        progress = runner._progress.get("job-audio")
        self.assertIsNotNone(progress)
        self.assertEqual(progress.progress, 100)

    def test_audio_mix_clamps_request_original_volume(self):
        from job_models import SubtitleSegment

        ws = Path(self.tmpdir)
        source_video = ws / "source.mp4"
        tts_dir = ws / "audio" / "tts"
        tts_dir.mkdir(parents=True)
        source_video.write_bytes(b"video")
        (tts_dir / "seg_0001.wav").write_bytes(b"wav")

        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        segments = [SubtitleSegment(index=1, start=0.0, end=1.0, text="hello", translation="你好")]

        def fake_mix_audio(**kwargs):
            kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
            kwargs["output_path"].write_bytes(b"mp4")
            return {"success": True}

        with patch("audio.mix.mix_audio", side_effect=fake_mix_audio) as mock_mix:
            ok = runner._run_audio_mix(
                "job-audio",
                ws,
                segments,
                source_video,
                "zh-CN",
                {"original_volume": 2.5},
                CancellationToken(),
            )

        self.assertTrue(ok)
        self.assertEqual(mock_mix.call_args.kwargs["original_volume"], 1.0)

    def test_audio_mix_defaults_to_muting_original_audio(self):
        from job_models import SubtitleSegment

        ws = Path(self.tmpdir)
        source_video = ws / "source.mp4"
        tts_dir = ws / "audio" / "tts"
        tts_dir.mkdir(parents=True)
        source_video.write_bytes(b"video")
        (tts_dir / "seg_0001.wav").write_bytes(b"wav")

        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        segments = [SubtitleSegment(index=1, start=0.0, end=1.0, text="hello", translation="hi")]

        def fake_mix_audio(**kwargs):
            kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
            kwargs["output_path"].write_bytes(b"mp4")
            return {"success": True}

        with patch("audio.mix.mix_audio", side_effect=fake_mix_audio) as mock_mix:
            ok = runner._run_audio_mix(
                "job-audio",
                ws,
                segments,
                source_video,
                "en",
                {},
                CancellationToken(),
            )

        self.assertTrue(ok)
        self.assertEqual(mock_mix.call_args.kwargs["original_volume"], 0.0)

    def test_dubbed_render_uses_mixed_video_as_input(self):
        ws = Path(self.tmpdir)
        (ws / "source").mkdir(parents=True)
        (ws / "subtitles").mkdir(parents=True)
        source_video = ws / "source" / "source.mp4"
        source_sub = ws / "subtitles" / "source.srt"
        source_video.write_bytes(b"video")
        source_sub.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
            encoding="utf-8",
        )

        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        runner._store.create("job-dub-render", {})

        def fake_audio_mix(job_id, workspace, segments, source, target_lang, request, token):
            dubbed = workspace / "rendered" / f"{workspace.name}_{target_lang}_dubbed.mp4"
            dubbed.parent.mkdir(parents=True, exist_ok=True)
            dubbed.write_bytes(b"dubbed")
            return True

        def fake_render_hardsub(**kwargs):
            kwargs["output_path"].write_bytes(b"rendered")
            return {"success": True, "output_path": str(kwargs["output_path"])}

        with patch.object(PipelineRunner, "_run_tts", return_value=True), \
             patch.object(PipelineRunner, "_run_audio_mix", side_effect=fake_audio_mix), \
             patch("engine.pipeline.render_hardsub", side_effect=fake_render_hardsub) as mock_render:
            runner._execute(
                "job-dub-render",
                {
                    "workspace_dir": str(ws),
                    "source_video": str(source_video),
                    "source_subtitle": str(source_sub),
                    "source_language": "en",
                    "target_language": "en",
                    "subtitle_mode": "source",
                    "dubbing_enabled": True,
                    "burn_subtitles": True,
                },
                CancellationToken(),
            )

        render_video = mock_render.call_args.kwargs["video_path"]
        self.assertEqual(render_video.name, f"{ws.name}_en_dubbed.mp4")
        artifacts = runner._store.get("job-dub-render").artifacts
        burned = [item for item in artifacts if item["kind"] == "burned_video"]
        self.assertTrue(burned)
        self.assertTrue(burned[0]["path"].endswith("_dubbed_hardsub.mp4"))

    def test_resume_tts_loads_existing_translated_srt(self):
        ws = Path(self.tmpdir)
        (ws / "source").mkdir(parents=True)
        (ws / "subtitles").mkdir(parents=True)
        source_video = ws / "source" / "source.mp4"
        source_sub = ws / "subtitles" / "source.srt"
        translated_sub = ws / "subtitles" / "zh-CN.srt"
        source_video.write_bytes(b"video")
        source_sub.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
            encoding="utf-8",
        )
        translated_sub.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n你好\n",
            encoding="utf-8",
        )

        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        runner._store.create("job-resume-tts", {})

        def fake_tts(job_id, workspace, segments, request, target_lang, token):
            self.assertEqual(segments[0].translation, "你好")
            return False

        with patch.object(PipelineRunner, "_run_translation") as mock_translation, \
             patch.object(PipelineRunner, "_run_tts", side_effect=fake_tts) as mock_tts:
            runner._execute(
                "job-resume-tts",
                {
                    "workspace_dir": str(ws),
                    "source_video": str(source_video),
                    "source_subtitle": str(source_sub),
                    "source_language": "en",
                    "target_language": "zh-CN",
                    "subtitle_mode": "bilingual",
                    "dubbing_enabled": True,
                    "burn_subtitles": False,
                    "resume_stage": "tts",
                },
                CancellationToken(),
            )

        mock_translation.assert_not_called()
        self.assertTrue(mock_tts.called)

    def test_resume_render_reuses_existing_dubbed_video(self):
        ws = Path(self.tmpdir)
        (ws / "source").mkdir(parents=True)
        (ws / "subtitles").mkdir(parents=True)
        (ws / "rendered").mkdir(parents=True)
        source_video = ws / "source" / "source.mp4"
        source_sub = ws / "subtitles" / "source.srt"
        source_ass = ws / "subtitles" / "source_en.ass"
        dubbed_video = ws / "rendered" / f"{ws.name}_en_dubbed.mp4"
        source_video.write_bytes(b"video")
        source_sub.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
            encoding="utf-8",
        )
        source_ass.write_text("[Script Info]\n", encoding="utf-8")
        dubbed_video.write_bytes(b"dubbed")

        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        runner._store.create("job-resume-render", {})

        def fake_render_hardsub(**kwargs):
            kwargs["output_path"].write_bytes(b"rendered")
            return {"success": True, "output_path": str(kwargs["output_path"])}

        with patch.object(PipelineRunner, "_run_tts") as mock_tts, \
             patch.object(PipelineRunner, "_run_audio_mix") as mock_mix, \
             patch("engine.pipeline.render_hardsub", side_effect=fake_render_hardsub) as mock_render:
            runner._execute(
                "job-resume-render",
                {
                    "workspace_dir": str(ws),
                    "source_video": str(source_video),
                    "source_subtitle": str(source_sub),
                    "source_language": "en",
                    "target_language": "en",
                    "subtitle_mode": "source",
                    "dubbing_enabled": True,
                    "burn_subtitles": True,
                    "resume_stage": "render",
                },
                CancellationToken(),
            )

        mock_tts.assert_not_called()
        mock_mix.assert_not_called()
        self.assertEqual(mock_render.call_args.kwargs["video_path"], dubbed_video)

    def test_low_vram_qwen_dub_unloads_tts_around_pipeline_stages(self):
        ws = Path(self.tmpdir)
        (ws / "source").mkdir(parents=True)
        (ws / "subtitles").mkdir(parents=True)
        source_video = ws / "source" / "source.mp4"
        source_sub = ws / "subtitles" / "source.srt"
        source_video.write_bytes(b"video")
        source_sub.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
            encoding="utf-8",
        )

        runner = PipelineRunner(TaskStore(ws / "data"), ProgressTracker())
        runner._store.create("job-low-vram", {})

        def fake_audio_mix(job_id, workspace, segments, source, target_lang, request, token):
            dubbed = workspace / "rendered" / f"{workspace.name}_{target_lang}_dubbed.mp4"
            dubbed.parent.mkdir(parents=True, exist_ok=True)
            dubbed.write_bytes(b"dubbed")
            return True

        with patch.object(PipelineRunner, "_run_translation", return_value=True), \
             patch.object(PipelineRunner, "_run_tts", return_value=True), \
             patch.object(PipelineRunner, "_run_audio_mix", side_effect=fake_audio_mix), \
             patch.object(runner, "_unload_qwen3_tts") as mock_unload:
            runner._execute(
                "job-low-vram",
                {
                    "workspace_dir": str(ws),
                    "source_video": str(source_video),
                    "source_subtitle": str(source_sub),
                    "source_language": "en",
                    "target_language": "zh-CN",
                    "subtitle_mode": "source",
                    "dubbing_enabled": True,
                    "tts_provider": "qwen3-tts",
                    "burn_subtitles": False,
                    "low_vram_mode": True,
                },
                CancellationToken(),
            )

        reasons = [call.args[1] for call in mock_unload.call_args_list]
        self.assertEqual(reasons, ["before translation", "after TTS"])


# ---------------------------------------------------------------------------
# Audio mix tests
# ---------------------------------------------------------------------------

class TestAudioMix(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_mix_audio_creates_output_directory(self):
        from audio.mix import mix_audio

        root = Path(self.tmpdir)
        video = root / "source.mp4"
        tts = root / "seg_0001.wav"
        output = root / "rendered" / "dubbed.mp4"
        video.write_bytes(b"video")
        tts.write_bytes(b"wav")

        def fake_run(*args, **kwargs):
            output.write_bytes(b"mp4")
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with patch("audio.mix.subprocess.run", side_effect=fake_run):
            result = mix_audio(video, [(tts, 0.0)], output)

        self.assertTrue(result["success"])
        self.assertTrue(output.parent.is_dir())
        self.assertTrue(output.exists())

    def test_mix_audio_defaults_to_skipping_original_audio(self):
        from audio.mix import mix_audio

        root = Path(self.tmpdir)
        video = root / "source.mp4"
        tts = root / "seg_0001.wav"
        output = root / "rendered" / "dubbed.mp4"
        video.write_bytes(b"video")
        tts.write_bytes(b"wav")

        def fake_run(cmd, *args, **kwargs):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"mp4")
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with patch("audio.mix._video_has_audio", return_value=True) as mock_has_audio, \
             patch("audio.mix.subprocess.run", side_effect=fake_run) as mock_run:
            result = mix_audio(video, [(tts, 0.0)], output)

        self.assertTrue(result["success"])
        mock_has_audio.assert_not_called()
        cmd = mock_run.call_args.args[0]
        self.assertNotIn("[0:a]volume", " ".join(cmd))
        self.assertIn("[tts_mix]", cmd)

    def test_mix_audio_returns_stderr_tail_on_failure(self):
        from audio.mix import mix_audio

        root = Path(self.tmpdir)
        video = root / "source.mp4"
        tts = root / "seg_0001.wav"
        output = root / "rendered" / "dubbed.mp4"
        video.write_bytes(b"video")
        tts.write_bytes(b"wav")

        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = ""
        fake_result.stderr = "ffmpeg version banner\n" + ("x" * 1400) + "\nreal error"

        with patch("audio.mix.subprocess.run", return_value=fake_result):
            result = mix_audio(video, [(tts, 0.0)], output)

        self.assertFalse(result["success"])
        self.assertIn("real error", result["error"])
        self.assertNotIn("ffmpeg version banner", result["error"])

    def test_mix_audio_chunks_many_tts_segments(self):
        from audio.mix import mix_audio

        root = Path(self.tmpdir)
        video = root / "source.mp4"
        output = root / "rendered" / "dubbed.mp4"
        video.write_bytes(b"video")
        segments = []
        for i in range(41):
            wav = root / f"seg_{i:04d}.wav"
            wav.write_bytes(b"wav")
            segments.append((wav, float(i) / 10.0))

        def fake_run(cmd, *args, **kwargs):
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"audio-or-video")
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with patch("audio.mix.subprocess.run", side_effect=fake_run) as mock_run:
            result = mix_audio(video, segments, output)

        self.assertTrue(result["success"])
        self.assertTrue(output.exists())
        self.assertGreaterEqual(mock_run.call_count, 4)

    def test_mix_audio_handles_video_without_original_audio(self):
        from audio.mix import mix_audio

        root = Path(self.tmpdir)
        video = root / "source.mp4"
        tts = root / "seg_0001.wav"
        output = root / "rendered" / "dubbed.mp4"
        video.write_bytes(b"video")
        tts.write_bytes(b"wav")

        def fake_run(cmd, *args, **kwargs):
            output.write_bytes(b"mp4")
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with patch("audio.mix._video_has_audio", return_value=False), \
             patch("audio.mix.subprocess.run", side_effect=fake_run) as mock_run:
            result = mix_audio(video, [(tts, 0.0)], output)

        self.assertTrue(result["success"])
        cmd = mock_run.call_args.args[0]
        self.assertNotIn("[0:a]volume", " ".join(cmd))
        self.assertIn("[tts_mix]", cmd)

    def test_mix_audio_skips_original_audio_when_volume_is_zero(self):
        from audio.mix import mix_audio

        root = Path(self.tmpdir)
        video = root / "source.mp4"
        tts = root / "seg_0001.wav"
        output = root / "rendered" / "dubbed.mp4"
        video.write_bytes(b"video")
        tts.write_bytes(b"wav")

        def fake_run(cmd, *args, **kwargs):
            output.write_bytes(b"mp4")
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with patch("audio.mix._video_has_audio", return_value=True), \
             patch("audio.mix.subprocess.run", side_effect=fake_run) as mock_run:
            result = mix_audio(video, [(tts, 0.0)], output, original_volume=0.0)

        self.assertTrue(result["success"])
        cmd = mock_run.call_args.args[0]
        self.assertNotIn("[0:a]volume", " ".join(cmd))
        self.assertIn("[tts_mix]", cmd)

    def test_mix_simple_audio_handles_video_without_original_audio(self):
        from audio.mix import mix_simple_audio

        root = Path(self.tmpdir)
        video = root / "source.mp4"
        dubbed = root / "dub.wav"
        output = root / "rendered" / "dubbed.mp4"
        video.write_bytes(b"video")
        dubbed.write_bytes(b"wav")

        def fake_run(cmd, *args, **kwargs):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"mp4")
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with patch("audio.mix._video_has_audio", return_value=False), \
             patch("audio.mix.subprocess.run", side_effect=fake_run) as mock_run:
            result = mix_simple_audio(video, dubbed, output)

        self.assertTrue(result["success"])
        cmd = mock_run.call_args.args[0]
        self.assertNotIn("[0:a]volume", " ".join(cmd))
        self.assertIn("1:a", cmd)


if __name__ == "__main__":
    unittest.main()
