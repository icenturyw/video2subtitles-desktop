import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subtitle_utils import (  # noqa: E402
    VIDEO_EXTENSIONS,
    format_subtitle_time,
    parse_srt_file,
    parse_srt_text,
    sanitize_filename,
    save_srt_file,
    subtitles_to_srt,
    subtitles_to_txt,
    subtitles_to_vtt,
)


class SubtitleUtilsTest(unittest.TestCase):
    def test_format_subtitle_time_supports_srt_and_vtt(self):
        self.assertEqual(format_subtitle_time(3661.234), "01:01:01,234")
        self.assertEqual(format_subtitle_time(3661.234, "."), "01:01:01.234")
        self.assertEqual(format_subtitle_time(None), "00:00:00,000")

    def test_subtitles_to_srt_and_parse_text(self):
        subtitles = [
            {"start": 1.23, "end": 2.5, "text": "你好", "translation": "Hello"},
            {"start": 62, "end": 65.01, "text": "第二句"},
        ]

        content = subtitles_to_srt(subtitles)

        self.assertIn("1\n00:00:01,230 --> 00:00:02,500\n你好\nHello", content)
        parsed = parse_srt_text(content)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["text"], "你好\nHello")
        self.assertEqual(parsed[1]["start"], 62.0)

    def test_vtt_and_txt_exports(self):
        subtitles = [{"start": 1, "end": 2.5, "text": "hello", "translation": "你好"}]

        vtt = subtitles_to_vtt(subtitles)
        txt = subtitles_to_txt(subtitles)

        self.assertTrue(vtt.startswith("WEBVTT\n\n"))
        self.assertIn("00:00:01.000 --> 00:00:02.500", vtt)
        self.assertNotIn("你好", vtt)
        self.assertEqual(txt, "hello")

    def test_save_and_parse_srt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            srt_path = Path(tmp) / "sample.srt"
            save_srt_file([{"start": 0, "end": 1.2, "text": "测试"}], srt_path)

            parsed = parse_srt_file(srt_path)

        self.assertEqual(parsed, [{"start": 0.0, "end": 1.2, "text": "测试"}])

    def test_sanitize_filename_and_extensions(self):
        self.assertEqual(sanitize_filename("  a/b:c*?.mp4  "), "a_b_c__.mp4")
        self.assertEqual(sanitize_filename("???"), "video")
        self.assertIn(".mp4", VIDEO_EXTENSIONS)
        self.assertIn(".ts", VIDEO_EXTENSIONS)


if __name__ == "__main__":
    unittest.main()
