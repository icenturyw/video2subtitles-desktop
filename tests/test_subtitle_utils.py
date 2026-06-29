import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subtitle_utils import (  # noqa: E402
    VIDEO_EXTENSIONS,
    align_keyframe_points_to_scene_changes,
    choose_subtitle_keyframe_points,
    find_repeated_subtitle_runs,
    format_subtitle_time,
    is_filler_only_text,
    is_punctuation_only_text,
    is_speech_subtitle_text,
    normalize_subtitle_timeline,
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

    def test_normalize_subtitle_timeline_merges_punctuation_and_trims_overlap(self):
        subtitles = [
            {"start": 0.05, "end": 3.97, "text": "在中国，人工智能热潮正在改变就业格局"},
            {"start": 3.87, "end": 4.29, "text": "。"},
            {"start": 4.89, "end": 7.57, "text": "最近，无人企业的单人创业大幅增加"},
            {"start": 7.47, "end": 10.27, "text": "而人工智能就是这些企业的员工。"},
        ]

        normalized = normalize_subtitle_timeline(subtitles)

        self.assertEqual(len(normalized), 3)
        self.assertEqual(normalized[0]["text"], "在中国，人工智能热潮正在改变就业格局。")
        self.assertLessEqual(normalized[1]["end"] + 0.02, normalized[2]["start"])

    def test_text_classifiers_skip_punctuation_and_filler_for_tts(self):
        self.assertTrue(is_punctuation_only_text("。"))
        self.assertTrue(is_filler_only_text("嗯嗯"))
        self.assertFalse(is_speech_subtitle_text("嗯嗯"))
        self.assertTrue(is_speech_subtitle_text("人工智能正在改变就业格局"))

    def test_find_repeated_subtitle_runs_reports_asr_hallucination_pattern(self):
        subtitles = [
            {"start": 1, "end": 2, "text": "生日时不去种田。"},
            {"start": 2.1, "end": 3, "text": "生日时不去种田。"},
            {"start": 3.1, "end": 4, "text": "生日时不去种田。"},
        ]

        runs = find_repeated_subtitle_runs(subtitles)

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["count"], 3)

    def test_choose_subtitle_keyframe_points_prefers_meaningful_subtitles(self):
        subtitles = [
            {"start": 0, "end": 1, "text": "hi"},
            {"start": 3, "end": 8, "text": "This is the main opening sentence."},
            {"start": 12, "end": 13, "text": "um"},
            {"start": 15, "end": 18, "text": "Second important idea."},
            {"start": 31, "end": 35, "text": "Final recap."},
        ]

        points = choose_subtitle_keyframe_points(
            subtitles,
            target_interval=10,
            min_gap=2,
            max_frames=10,
        )

        self.assertEqual([point["subtitle_index"] for point in points], [2, 4, 5])
        self.assertEqual(points[0]["timestamp"], 5.5)
        self.assertEqual(points[1]["timestamp"], 16.5)

    def test_align_keyframe_points_to_scene_changes_uses_nearby_visual_cut(self):
        points = [
            {
                "subtitle_index": 1,
                "timestamp": 10.0,
                "subtitle_start": 9.0,
                "subtitle_end": 13.0,
            },
            {
                "subtitle_index": 2,
                "timestamp": 30.0,
                "subtitle_start": 29.0,
                "subtitle_end": 33.0,
            },
        ]

        aligned = align_keyframe_points_to_scene_changes(points, [8.0, 9.8, 40.0])

        self.assertEqual(aligned[0]["timestamp"], 10.2)
        self.assertEqual(aligned[0]["scene_timestamp"], 9.8)
        self.assertEqual(aligned[0]["visual_anchor"], "scene_change")
        self.assertEqual(aligned[1]["timestamp"], 30.0)
        self.assertEqual(aligned[1]["visual_anchor"], "subtitle_midpoint")


if __name__ == "__main__":
    unittest.main()
