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
import httpx

from job_models import TranslationConfig
from translation.base import AuthError, TranslationError
from translation.openai_compatible import (
    OpenAICompatibleProvider,
    _extract_response_content,
    _response_error_detail,
)


class TestTranslationParser(unittest.TestCase):
    def test_parse_valid_response(self):
        response = '[{"id": 1, "text": "你好"}, {"id": 2, "text": "世界"}]'
        translations, errors = parse_translation_response(response, [1, 2])
        self.assertEqual(len(translations), 2)
        self.assertEqual(translations[0]["text"], "你好")
        self.assertEqual(errors, [])

    def test_parse_wrapped_translations_response(self):
        response = '{"translations": [{"id": 1, "text": "你好"}]}'
        translations, errors = parse_translation_response(response, [1])
        self.assertEqual(translations, [{"id": 1, "text": "你好"}])
        self.assertEqual(errors, [])

    def test_extract_local_llm_text_response(self):
        result = {"text": '[{"id": 1, "text": "你好"}]'}
        self.assertEqual(_extract_response_content(result), result["text"])

    def test_extract_wrapped_translation_response(self):
        result = {"translations": [{"id": 1, "text": "你好"}]}
        self.assertIn("translations", _extract_response_content(result))

    def test_extract_newapi_data_wrapped_choices_response(self):
        result = {
            "data": {
                "choices": [
                    {
                        "message": {
                            "content": '[{"id": 1, "text": "你好"}]',
                        }
                    }
                ]
            }
        }
        self.assertEqual(_extract_response_content(result), '[{"id": 1, "text": "你好"}]')

    def test_response_error_detail_includes_json_body(self):
        response = httpx.Response(
            503,
            json={
                "error": {
                    "code": "model_not_found",
                    "message": "No available channel",
                }
            },
        )
        detail = _response_error_detail(response)
        self.assertIn("HTTP 503", detail)
        self.assertIn("model_not_found", detail)

    def test_auth_error_includes_response_body(self):
        def handler(request):
            return httpx.Response(
                401,
                json={"error": {"message": "invalid api key"}},
            )

        provider = OpenAICompatibleProvider()
        provider._client = httpx.Client(transport=httpx.MockTransport(handler))
        config = TranslationConfig(
            base_url="https://example.test/v1",
            model="test-model",
            retry_count=0,
        )

        with self.assertRaises(AuthError) as ctx:
            provider.translate_batch(
                [{"id": 1, "text": "hello"}],
                config,
                "en",
                "zh-CN",
            )

        self.assertIn("HTTP 401", str(ctx.exception))
        self.assertIn("invalid api key", str(ctx.exception))

    def test_non_recoverable_http_error_is_not_retried(self):
        calls = {"count": 0}

        def handler(request):
            calls["count"] += 1
            return httpx.Response(
                404,
                json={"error": "model not supported"},
            )

        provider = OpenAICompatibleProvider()
        provider._client = httpx.Client(transport=httpx.MockTransport(handler))
        config = TranslationConfig(
            base_url="https://example.test/v1",
            model="test-model",
            retry_count=3,
        )

        with self.assertRaises(TranslationError) as ctx:
            provider.translate_batch([{"id": 1, "text": "hello"}], config, "en", "zh-CN")

        self.assertEqual(calls["count"], 1)
        self.assertIn("HTTP 404", str(ctx.exception))
        self.assertIn("model not supported", str(ctx.exception))

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
