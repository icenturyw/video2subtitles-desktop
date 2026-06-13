import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "localization-engine"))

from translation.response_parser import (
    parse_translation_response,
    sanitize_for_log,
    check_response_safety,
)


class TestTranslationParser(unittest.TestCase):
    def test_parse_valid_response(self):
        response = '[{"id": 1, "text": "你好"}, {"id": 2, "text": "世界"}]'
        translations, errors = parse_translation_response(response, [1, 2])
        self.assertEqual(len(translations), 2)
        self.assertEqual(translations[0]["text"], "你好")
        self.assertEqual(errors, [])

    def test_parse_missing_id(self):
        response = '[{"id": 1, "text": "你好"}]'
        translations, errors = parse_translation_response(response, [1, 2])
        self.assertEqual(len(translations), 1)
        self.assertTrue(any("Missing" in e for e in errors))

    def test_parse_extra_id(self):
        response = '[{"id": 1, "text": "你好"}, {"id": 3, "text": "额外"}]'
        translations, errors = parse_translation_response(response, [1])
        self.assertEqual(len(translations), 2)
        self.assertTrue(any("Unexpected" in e for e in errors))

    def test_parse_markdown_code_block(self):
        response = '```json\n[{"id": 1, "text": "你好"}]\n```'
        translations, errors = parse_translation_response(response, [1])
        self.assertEqual(len(translations), 1)

    def test_parse_invalid_json(self):
        response = "not json at all"
        translations, errors = parse_translation_response(response, [1])
        self.assertEqual(translations, [])
        self.assertTrue(len(errors) > 0)

    def test_parse_empty_text_warning(self):
        response = '[{"id": 1, "text": ""}]'
        translations, errors = parse_translation_response(response, [1])
        self.assertTrue(any("Empty" in e for e in errors))

    def test_sanitize_api_key(self):
        safe = sanitize_for_log('{"api_key": "sk-test1234567890"}')
        self.assertNotIn("sk-test1234567890", safe)
        self.assertIn("[REDACTED]", safe)

    def test_sanitize_truncation(self):
        long_text = "a" * 1000
        safe = sanitize_for_log(long_text, max_length=100)
        self.assertTrue(len(safe) <= 100 + 3)  # +3 for "..."

    def test_check_response_safety(self):
        warnings = check_response_safety('{"key": "sk-abc123"}')
        self.assertTrue(len(warnings) > 0)

        warnings = check_response_safety('{"text": "hello"}')
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
