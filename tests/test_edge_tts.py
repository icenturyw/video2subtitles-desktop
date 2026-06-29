from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ENGINE_DIR = Path(__file__).resolve().parent.parent / "localization-engine"
sys.path.insert(0, str(ENGINE_DIR))

from tts.base import TTSResult, TTSUnavailableError
from tts.edge_tts import EdgeTTSProvider, _get_audio_duration


async def _fake_save(*args, **kwargs):
    return None


def _make_mock_communicate():
    comm = MagicMock()
    comm.save = _fake_save
    return comm


class TestEdgeTTSListVoices(unittest.TestCase):
    def test_list_voices_returns_empty_when_not_available(self):
        with patch("tts.edge_tts._EDGE_TTS_AVAILABLE", False):
            provider = EdgeTTSProvider()
            self.assertEqual(provider.list_voices(), [])

    def test_list_voices_caches_and_filters_by_language(self):
        fake_voices = [
            {"ShortName": "zh-CN-XiaoxiaoNeural", "Locale": "zh-CN", "Gender": "Female"},
            {"ShortName": "en-US-JennyNeural", "Locale": "en-US", "Gender": "Female"},
            {"ShortName": "ja-JP-NanamiNeural", "Locale": "ja-JP", "Gender": "Female"},
        ]

        with patch("tts.edge_tts.edge_tts.list_voices", return_value=fake_voices) as mock_list:
            provider = EdgeTTSProvider()

            voices = provider.list_voices(language="zh")
            self.assertEqual(len(voices), 1)
            self.assertEqual(voices[0]["name"], "zh-CN-XiaoxiaoNeural")
            mock_list.assert_called_once()

            voices2 = provider.list_voices(language="en")
            self.assertEqual(len(voices2), 1)
            self.assertEqual(voices2[0]["name"], "en-US-JennyNeural")
            mock_list.assert_called_once()

    def test_list_voices_returns_empty_on_exception(self):
        with patch("tts.edge_tts.edge_tts.list_voices", side_effect=Exception("API error")):
            provider = EdgeTTSProvider()
            self.assertEqual(provider.list_voices(), [])

    def test_list_voices_no_language_returns_all(self):
        fake_voices = [
            {"ShortName": "zh-CN-XiaoxiaoNeural", "Locale": "zh-CN", "Gender": "Female"},
            {"ShortName": "en-US-JennyNeural", "Locale": "en-US", "Gender": "Female"},
        ]
        with patch("tts.edge_tts.edge_tts.list_voices", return_value=fake_voices):
            provider = EdgeTTSProvider()
            voices = provider.list_voices()
            self.assertEqual(len(voices), 2)


class TestEdgeTTSSynthesize(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.output = self.tmp / "test.mp3"

    def test_synthesize_raises_when_not_available(self):
        with patch("tts.edge_tts._EDGE_TTS_AVAILABLE", False):
            provider = EdgeTTSProvider()
            with self.assertRaises(TTSUnavailableError):
                provider.synthesize("你好", "zh-CN", "zh-CN-XiaoxiaoNeural",
                                    self.output, {})

    @patch("tts.edge_tts._get_audio_duration", return_value=2.5)
    @patch("tts.edge_tts.edge_tts.Communicate", return_value=_make_mock_communicate())
    def test_synthesize_returns_tts_result(self, mock_comm, mock_dur):
        provider = EdgeTTSProvider()
        result = provider.synthesize("你好", "zh-CN", "zh-CN-XiaoxiaoNeural",
                                     self.output, {"rate": "+0%", "pitch": "+0Hz", "volume": "+0%"})
        self.assertIsInstance(result, TTSResult)
        self.assertEqual(result.duration_seconds, 2.5)
        self.assertEqual(result.output_path, self.output)

    @patch("tts.edge_tts._get_audio_duration", return_value=3.0)
    @patch("tts.edge_tts.edge_tts.Communicate", return_value=_make_mock_communicate())
    def test_synthesize_passes_options(self, mock_comm, mock_dur):
        provider = EdgeTTSProvider()
        provider.synthesize("Hello", "en-US", "en-US-JennyNeural",
                            self.output, {"rate": "-50%", "pitch": "-10Hz", "volume": "+10%"})
        mock_comm.assert_called_once_with(
            "Hello", "en-US-JennyNeural",
            rate="-50%", pitch="-10Hz", volume="+10%",
        )

    def test_synthesize_hits_cache(self):
        from tts.base import TTSCache
        cache = TTSCache(self.tmp / "tts_cache")
        cache_path = self.tmp / "cached.wav"
        cache_path.write_text("fake audio", encoding="utf-8")
        cache.put("你好", "zh-CN-XiaoxiaoNeural", "zh-CN", cache_path)

        provider = EdgeTTSProvider(cache=cache)

        with patch("tts.edge_tts.edge_tts.Communicate") as mock_comm:
            with patch("tts.edge_tts._get_audio_duration", return_value=1.5):
                result = provider.synthesize("你好", "zh-CN", "zh-CN-XiaoxiaoNeural",
                                             self.output, {})
                self.assertEqual(result.duration_seconds, 1.5)
                mock_comm.assert_not_called()

    @patch("tts.edge_tts._get_audio_duration", return_value=0.0)
    @patch("tts.edge_tts.edge_tts.Communicate", return_value=_make_mock_communicate())
    def test_synthesize_zero_duration_does_not_cache(self, mock_comm, mock_dur):
        from tts.base import TTSCache
        cache_dir = self.tmp / "cache_dir"
        cache = TTSCache(cache_dir)
        provider = EdgeTTSProvider(cache=cache)
        provider.synthesize("test", "en", "en-US-JennyNeural", self.output, {})
        cached = cache.get("test", "en-US-JennyNeural", "en")
        self.assertIsNone(cached)


class TestGetAudioDuration(unittest.TestCase):
    @patch("tts.edge_tts.subprocess.run")
    def test_returns_duration(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "3.14\n"
        dur = _get_audio_duration(Path("/fake/test.mp3"))
        self.assertAlmostEqual(dur, 3.14)

    @patch("tts.edge_tts.subprocess.run", side_effect=Exception("ffprobe not found"))
    def test_returns_zero_on_error(self, mock_run):
        dur = _get_audio_duration(Path("/fake/test.mp3"))
        self.assertEqual(dur, 0.0)

    @patch("tts.edge_tts.subprocess.run")
    def test_nonzero_returncode_returns_zero(self, mock_run):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        dur = _get_audio_duration(Path("/fake/test.mp3"))
        self.assertEqual(dur, 0.0)
