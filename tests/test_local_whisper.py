from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from local_whisper import LocalWhisperTranscriber


def _make_mock_process(stdout_lines=None, returncode=0, stderr_text=""):
    process = MagicMock()
    process.returncode = returncode
    process.stdout = io.StringIO("\n".join(stdout_lines or []) + "\n")
    process.stderr = io.StringIO(stderr_text)
    return process


class TestLocalWhisperTranscriberInit(unittest.TestCase):
    @patch("local_whisper._create_transcribe_script")
    @patch("local_whisper.HELPER_DIR", autospec=True)
    def test_init_creates_script(self, mock_dir, mock_create):
        LocalWhisperTranscriber()
        mock_create.assert_called_once()


class TestLocalWhisperCancel(unittest.TestCase):
    @patch("local_whisper._create_transcribe_script")
    def setUp(self, mock_create):
        self.transcriber = LocalWhisperTranscriber()
        self.transcriber._process = MagicMock()
        self.transcriber._process.poll.return_value = None

    def test_cancel_terminates_process(self):
        self.transcriber.cancel()
        self.transcriber._process.terminate.assert_called_once()

    def test_cancel_already_done_does_nothing(self):
        self.transcriber._process.poll.return_value = 0
        self.transcriber.cancel()
        self.transcriber._process.terminate.assert_not_called()

    def test_cancel_no_process(self):
        self.transcriber._process = None
        self.transcriber.cancel()


class TestLocalWhisperTranscribe(unittest.TestCase):
    def setUp(self):
        self.create_patcher = patch("local_whisper._create_transcribe_script")
        self.create_patcher.start()
        self.find_python = patch("local_whisper.find_python_executable", return_value="python")
        self.find_python.start()
        self.subprocess_patcher = patch("local_whisper.subprocess.Popen")
        self.mock_popen = self.subprocess_patcher.start()
        self.transcriber = LocalWhisperTranscriber()

    def tearDown(self):
        self.create_patcher.stop()
        self.find_python.stop()
        self.subprocess_patcher.stop()

    def test_transcribe_returns_complete_result(self):
        lines = [
            json.dumps({"type": "status", "progress": 50, "message": "transcribing..."}),
            json.dumps({"type": "complete", "subtitles": [
                {"index": 1, "start": 0, "end": 1.5, "text": "Hello"},
                {"index": 2, "start": 2, "end": 3.5, "text": "World"},
            ], "language": "en"}),
        ]
        self.mock_popen.return_value = _make_mock_process(lines, returncode=0)

        subtitles, lang = self.transcriber.transcribe("test.mp3")
        self.assertEqual(len(subtitles), 2)
        self.assertEqual(subtitles[0]["text"], "Hello")
        self.assertEqual(lang, "en")

    def test_transcribe_reports_progress(self):
        calls = []
        lines = [
            json.dumps({"type": "status", "progress": 50, "message": "working"}),
            json.dumps({"type": "complete", "subtitles": [], "language": "en"}),
        ]
        self.mock_popen.return_value = _make_mock_process(lines, returncode=0)

        self.transcriber.transcribe("test.mp3", progress_callback=lambda p, m, s: calls.append((p, m, s)))
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(calls[0], (50, "working", "processing"))

    def test_transcribe_error_message(self):
        lines = [
            json.dumps({"type": "error", "message": "Model not found"}),
        ]
        self.mock_popen.return_value = _make_mock_process(lines, returncode=1, stderr_text="Model error")

        subtitles, lang = self.transcriber.transcribe("test.mp3")
        self.assertEqual(lang, "error")
        self.assertTrue(any("Model" in str(s) for s in subtitles))

    def test_transcribe_cancelled_returns_cancelled(self):
        lines = [
            json.dumps({"type": "status", "progress": 50, "message": "working"}),
        ]
        self.mock_popen.return_value = _make_mock_process(lines, returncode=1)

        def trigger_cancel(progress, message, status):
            self.transcriber._cancel_requested = True

        subtitles, lang = self.transcriber.transcribe("test.mp3", progress_callback=trigger_cancel)
        self.assertEqual(lang, "cancelled")
        self.assertEqual(subtitles, [])

    def test_transcribe_malformed_json_skipped(self):
        lines = [
            "not valid json",
            json.dumps({"type": "complete", "subtitles": [{"index": 1, "start": 0, "end": 1, "text": "Hi"}], "language": "en"}),
        ]
        self.mock_popen.return_value = _make_mock_process(lines, returncode=0)

        subtitles, lang = self.transcriber.transcribe("test.mp3")
        self.assertEqual(len(subtitles), 1)
        self.assertEqual(subtitles[0]["text"], "Hi")

    def test_transcribe_subprocess_error_without_json_error(self):
        self.mock_popen.return_value = _make_mock_process([], returncode=1, stderr_text="CUDA out of memory")

        subtitles, lang = self.transcriber.transcribe("test.mp3")
        self.assertEqual(lang, "error")
        self.assertTrue(any("CUDA" in str(s) for s in subtitles))
