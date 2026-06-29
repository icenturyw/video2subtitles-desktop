from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.ffmpeg_service import (
    FFmpegProcess,
    _build_hardsub_filter,
    escape_path,
    find_ffmpeg,
    get_ffmpeg_version,
    probe_video,
    render_hardsub,
    render_softsub,
)


class TestFindFfmpeg(unittest.TestCase):
    @patch("services.ffmpeg_service.shutil.which", return_value="/usr/bin/ffmpeg")
    def test_finds_in_path(self, mock_which):
        self.assertEqual(find_ffmpeg(), "/usr/bin/ffmpeg")

    @patch("services.ffmpeg_service.shutil.which", return_value=None)
    @patch("services.ffmpeg_service.Path.exists", return_value=True)
    def test_finds_in_fallback(self, mock_exists, mock_which):
        result = find_ffmpeg()
        self.assertIsNotNone(result)
        self.assertIn("ffmpeg", result)

    @patch("services.ffmpeg_service.shutil.which", return_value=None)
    @patch("services.ffmpeg_service.Path.exists", return_value=False)
    def test_not_found_returns_none(self, mock_exists, mock_which):
        self.assertIsNone(find_ffmpeg())


class TestGetFfmpegVersion(unittest.TestCase):
    @patch("services.ffmpeg_service.subprocess.run")
    @patch("services.ffmpeg_service.find_ffmpeg", return_value="/usr/bin/ffmpeg")
    def test_returns_first_line(self, mock_find, mock_run):
        mock_run.return_value.stdout = "ffmpeg version 6.0\nmore info\n"
        self.assertIn("ffmpeg version", get_ffmpeg_version())

    @patch("services.ffmpeg_service.find_ffmpeg", return_value=None)
    def test_no_ffmpeg_returns_empty(self, mock_find):
        self.assertEqual(get_ffmpeg_version(), "")

    @patch("services.ffmpeg_service.subprocess.run", side_effect=Exception("err"))
    @patch("services.ffmpeg_service.find_ffmpeg", return_value="/usr/bin/ffmpeg")
    def test_exception_returns_empty(self, mock_find, mock_run):
        self.assertEqual(get_ffmpeg_version(), "")


class TestEscapePath(unittest.TestCase):
    def test_returns_str_path(self):
        p = Path(r"C:\Users\test\file.mp4")
        self.assertEqual(escape_path(p), str(p))

    def test_returns_string(self):
        self.assertEqual(escape_path("/path/to/file.mp4"), "/path/to/file.mp4")


class TestBuildHardsubFilter(unittest.TestCase):
    def test_ass_subtitle_format(self):
        result = _build_hardsub_filter(Path("/tmp/sub.ass"))
        self.assertIn("subtitles=", result)
        self.assertNotIn("force_style", result)

    def test_srt_subtitle_has_force_style(self):
        result = _build_hardsub_filter(Path("/tmp/sub.srt"))
        self.assertIn("force_style", result)

    def test_path_escaped_properly(self):
        result = _build_hardsub_filter(Path(r"C:\Users\test\sub.ass"))
        self.assertIn("sub.ass", result)


class TestProbeVideo(unittest.TestCase):
    @patch("services.ffmpeg_service.find_ffmpeg", return_value="/usr/bin/ffmpeg")
    @patch("services.ffmpeg_service.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("services.ffmpeg_service.subprocess.run")
    def test_returns_video_info(self, mock_run, mock_which, mock_find):
        probe_data = {
            "format": {"duration": "120.5", "size": "1024000", "bit_rate": "2000000"},
            "streams": [
                {"codec_type": "video", "width": 1920, "height": 1080, "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = json.dumps(probe_data)

        info = probe_video("/tmp/video.mp4")
        self.assertAlmostEqual(info["duration"], 120.5)
        self.assertEqual(info["width"], 1920)
        self.assertEqual(info["height"], 1080)
        self.assertEqual(info["video_codec"], "h264")
        self.assertEqual(info["audio_codec"], "aac")
        self.assertTrue(info.get("has_video"))
        self.assertTrue(info.get("has_audio"))

    @patch("services.ffmpeg_service.find_ffmpeg", return_value=None)
    def test_no_ffmpeg_returns_error(self, mock_find):
        info = probe_video("/tmp/video.mp4")
        self.assertIn("error", info)

    @patch("services.ffmpeg_service.subprocess.run", side_effect=Exception("error"))
    @patch("services.ffmpeg_service.find_ffmpeg", return_value="/usr/bin/ffmpeg")
    @patch("services.ffmpeg_service.shutil.which", return_value="/usr/bin/ffprobe")
    def test_exception_returns_error_dict(self, mock_which, mock_find, mock_run):
        info = probe_video("/tmp/video.mp4")
        self.assertIn("error", info)


class TestFFmpegProcess(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    @patch("services.ffmpeg_service.subprocess.Popen")
    def test_start_creates_process(self, mock_popen):
        proc = FFmpegProcess(["ffmpeg", "-i", "in.mp4"])
        proc.start()
        mock_popen.assert_called_once()
        self.assertIsNotNone(proc._process)

    @patch("services.ffmpeg_service.subprocess.Popen")
    def test_wait_success(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.side_effect = [(b"", b"")]
        mock_popen.return_value = mock_proc

        proc = FFmpegProcess(["ffmpeg", "-i", "in.mp4"])
        proc.start()
        success, stdout, stderr = proc.wait(timeout=10)
        self.assertTrue(success)

    @patch("services.ffmpeg_service.subprocess.Popen")
    def test_wait_failure(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate.side_effect = [(b"", b"error message")]
        mock_popen.return_value = mock_proc

        proc = FFmpegProcess(["ffmpeg", "-i", "in.mp4"])
        proc.start()
        success, stdout, stderr = proc.wait(timeout=10)
        self.assertFalse(success)
        self.assertIn("error", stderr)

    @patch("services.ffmpeg_service.subprocess.Popen")
    def test_cancel(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.communicate.side_effect = [(b"", b"")]
        mock_popen.return_value = mock_proc

        proc = FFmpegProcess(["ffmpeg", "-i", "in.mp4"])
        proc.start()
        proc.cancel()
        self.assertTrue(proc.is_cancelled())
        mock_proc.terminate.assert_called_once()

    @patch("services.ffmpeg_service.subprocess.Popen")
    def test_cancel_checks_cancellation_in_wait(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.side_effect = [(b"", b"")]
        mock_popen.return_value = mock_proc

        proc = FFmpegProcess(["ffmpeg", "-i", "in.mp4"])
        proc.start()
        success, stdout, stderr = proc.wait(cancel_checker=lambda: True)
        self.assertFalse(success)
        self.assertEqual(stderr, "Cancelled")


class TestRenderHardsub(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    @patch("services.ffmpeg_service.find_ffmpeg", return_value="/usr/bin/ffmpeg")
    @patch("services.ffmpeg_service.FFmpegProcess")
    def test_successful_render(self, mock_process_class, mock_find):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = (True, "", "")
        mock_proc.is_cancelled.return_value = False
        mock_process_class.return_value = mock_proc

        video = self.tmp / "input.mp4"
        sub = self.tmp / "sub.srt"
        output = self.tmp / "output.mp4"
        video.write_text("")
        sub.write_text("")

        result = render_hardsub(video, sub, output)
        self.assertTrue(result["success"])
        self.assertEqual(result["output_path"], str(output))

    @patch("services.ffmpeg_service.find_ffmpeg", return_value=None)
    def test_no_ffmpeg(self, mock_find):
        result = render_hardsub("in.mp4", "sub.srt", "out.mp4")
        self.assertFalse(result["success"])
        self.assertIn("FFmpeg not found", result["error"])

    @patch("services.ffmpeg_service.find_ffmpeg", return_value="/usr/bin/ffmpeg")
    @patch("services.ffmpeg_service.FFmpegProcess")
    def test_cancelled(self, mock_process_class, mock_find):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = (False, "", "Cancelled")
        mock_proc.is_cancelled.return_value = True
        mock_process_class.return_value = mock_proc

        video = self.tmp / "input.mp4"
        sub = self.tmp / "sub.srt"
        output = self.tmp / "out.mp4"
        video.write_text("")
        sub.write_text("")

        result = render_hardsub(video, sub, output, cancel_checker=lambda: True)
        self.assertFalse(result["success"])
        self.assertTrue(result.get("cancelled"))

    @patch("services.ffmpeg_service.find_ffmpeg", return_value="/usr/bin/ffmpeg")
    @patch("services.ffmpeg_service.FFmpegProcess")
    def test_ffmpeg_failure(self, mock_process_class, mock_find):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = (False, "", "encode error: something went wrong")
        mock_proc.is_cancelled.return_value = False
        mock_process_class.return_value = mock_proc

        video = self.tmp / "input.mp4"
        sub = self.tmp / "sub.srt"
        output = self.tmp / "out.mp4"
        video.write_text("")
        sub.write_text("")

        result = render_hardsub(video, sub, output)
        self.assertFalse(result["success"])
        self.assertIn("FFmpeg failed", result["error"])


class TestRenderSoftsub(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    @patch("services.ffmpeg_service.find_ffmpeg", return_value="/usr/bin/ffmpeg")
    @patch("services.ffmpeg_service.FFmpegProcess")
    def test_successful_softsub(self, mock_process_class, mock_find):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = (True, "", "")
        mock_proc.is_cancelled.return_value = False
        mock_process_class.return_value = mock_proc

        video = self.tmp / "input.mp4"
        sub = self.tmp / "sub.srt"
        output = self.tmp / "out.mp4"
        video.write_text("")
        sub.write_text("")

        result = render_softsub(video, sub, output)
        self.assertTrue(result["success"])
        self.assertEqual(result["output_path"], str(output))

    @patch("services.ffmpeg_service.find_ffmpeg", return_value=None)
    def test_no_ffmpeg(self, mock_find):
        result = render_softsub("in.mp4", "sub.srt", "out.mp4")
        self.assertFalse(result["success"])
        self.assertIn("FFmpeg not found", result["error"])

    @patch("services.ffmpeg_service.find_ffmpeg", return_value="/usr/bin/ffmpeg")
    def test_unsupported_subtitle_format(self, mock_find):
        result = render_softsub("in.mp4", "sub.txt", "out.mp4")
        self.assertFalse(result["success"])
        self.assertIn("Unsupported subtitle format", result["error"])

    @patch("services.ffmpeg_service.find_ffmpeg", return_value="/usr/bin/ffmpeg")
    @patch("services.ffmpeg_service.FFmpegProcess")
    def test_cancelled(self, mock_process_class, mock_find):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = (False, "", "Cancelled")
        mock_proc.is_cancelled.return_value = True
        mock_process_class.return_value = mock_proc

        video = self.tmp / "input.mp4"
        sub = self.tmp / "sub.srt"
        output = self.tmp / "out.mp4"
        video.write_text("")
        sub.write_text("")

        result = render_softsub(video, sub, output, cancel_checker=lambda: True)
        self.assertFalse(result["success"])
        self.assertTrue(result.get("cancelled"))
