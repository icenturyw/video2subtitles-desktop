import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_models import SubtitleSegment, SubtitleStyle
from subtitle_ass import segments_to_ass, ass_header, save_ass, _escape_ass, _format_time


class TestAssWriter(unittest.TestCase):
    def setUp(self):
        self.segments = [
            SubtitleSegment(index=1, start=1.0, end=2.5, text="Hello World"),
            SubtitleSegment(index=2, start=3.0, end=4.2, text="Second line",
                            translation="第二行"),
        ]
        self.style = SubtitleStyle()

    def test_ass_header_contains_style(self):
        header = ass_header(self.style)
        self.assertIn("[V4+ Styles]", header)
        self.assertIn("Default", header)
        self.assertIn(self.style.font_family, header)

    def test_ass_source_only(self):
        content = segments_to_ass(self.segments, self.style, mode="source")
        self.assertIn("Hello World", content)
        self.assertNotIn("第二行", content)

    def test_ass_translated_only(self):
        content = segments_to_ass(self.segments, self.style, mode="translated")
        self.assertIn("第二行", content)
        # Target-only output must not fall back to source-language text.
        self.assertNotIn("Hello World", content)

    def test_ass_bilingual(self):
        content = segments_to_ass(self.segments, self.style, mode="bilingual")
        self.assertIn("第二行", content)
        self.assertIn("Hello World", content)

    def test_ass_bilingual_uses_foreground_translation_layer(self):
        segs = [
            SubtitleSegment(index=1, start=0, end=1, text="Source", translation="Target")
        ]
        content = segments_to_ass(segs, self.style, mode="bilingual")
        lines = content.splitlines()
        source_dialog = next(line for line in lines if line.endswith(",Source"))
        translation_dialog = next(line for line in lines if line.endswith(",Target"))

        self.assertTrue(source_dialog.startswith("Dialogue: 10,"))
        self.assertTrue(translation_dialog.startswith("Dialogue: 20,"))
        self.assertLess(lines.index(source_dialog), lines.index(translation_dialog))

        source_style = next(line for line in lines if line.startswith("Style: Source,"))
        translation_style = next(line for line in lines if line.startswith("Style: Translation,"))
        source_fields = source_style.replace("Style: ", "", 1).split(",")
        translation_fields = translation_style.replace("Style: ", "", 1).split(",")
        self.assertGreater(int(source_fields[21]), int(translation_fields[21]))

    def test_ass_bilingual_fallback_no_translation(self):
        segs = [SubtitleSegment(index=1, start=0, end=1, text="Only source")]
        content = segments_to_ass(segs, self.style, mode="bilingual")
        self.assertIn("Only source", content)

    def test_save_ass_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "test.ass"
            content = segments_to_ass(self.segments, self.style)
            save_ass(content, out)
            self.assertTrue(out.exists())
            saved = out.read_text(encoding="utf-8")
            self.assertIn("Hello World", saved)

    def test_escape_ass(self):
        self.assertEqual(_escape_ass("Hello\\World"), "Hello\\\\World")
        self.assertEqual(_escape_ass("{bold}"), "\\{bold\\}")
        self.assertEqual(_escape_ass("Line1\nLine2"), "Line1\\NLine2")

    def test_format_time(self):
        self.assertEqual(_format_time(0), "0:00:00.00")
        self.assertEqual(_format_time(3661.25), "1:01:01.25")
        self.assertEqual(_format_time(59.5), "0:00:59.50")


if __name__ == "__main__":
    unittest.main()
