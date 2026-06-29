from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from localization_client import LocalizationClient


def _mock_response(status=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


class TestHealthCheck(unittest.TestCase):
    def test_healthy_returns_true(self):
        client = LocalizationClient("http://localhost:8766")
        with patch.object(client.session, "get", return_value=_mock_response(200)):
            self.assertTrue(client.health_check())

    def test_unhealthy_returns_false(self):
        client = LocalizationClient("http://localhost:8766")
        with patch.object(client.session, "get", return_value=_mock_response(503)):
            self.assertFalse(client.health_check())

    def test_connection_error_returns_false(self):
        import requests
        client = LocalizationClient("http://localhost:8766")
        with patch.object(client.session, "get", side_effect=requests.exceptions.ConnectionError("refused")):
            self.assertFalse(client.health_check())


class TestGetServerInfo(unittest.TestCase):
    def test_returns_json(self):
        client = LocalizationClient("http://localhost:8766")
        info = {"name": "engine", "version": "1.0"}
        with patch.object(client.session, "get", return_value=_mock_response(200, info)):
            result = client.get_server_info()
            self.assertEqual(result, info)

    def test_non_200_returns_none(self):
        client = LocalizationClient("http://localhost:8766")
        with patch.object(client.session, "get", return_value=_mock_response(503)):
            self.assertIsNone(client.get_server_info())

    def test_exception_returns_none(self):
        client = LocalizationClient("http://localhost:8766")
        with patch.object(client.session, "get", side_effect=Exception("err")):
            self.assertIsNone(client.get_server_info())


class TestCreateJob(unittest.TestCase):
    def test_success_returns_job(self):
        client = LocalizationClient("http://localhost:8766")
        job = {"job_id": "abc", "status": "pending"}
        with patch.object(client.session, "post", return_value=_mock_response(201, job)):
            result = client.create_job(workspace_dir="/tmp/ws", source_video="/tmp/v.mp4")
            self.assertEqual(result, job)

    def test_http_error_returns_error_dict(self):
        client = LocalizationClient("http://localhost:8766")
        with patch.object(client.session, "post", return_value=_mock_response(400, text="bad request")):
            result = client.create_job(workspace_dir="/tmp/ws")
            self.assertIn("error", result)
            self.assertIn("bad request", result["error"])

    def test_timeout_returns_error(self):
        client = LocalizationClient("http://localhost:8766")
        with patch.object(client.session, "post", side_effect=TimeoutError("timed out")):
            result = client.create_job(workspace_dir="/tmp/ws")
            self.assertIn("error", result)

    def test_connection_error_returns_error(self):
        client = LocalizationClient("http://localhost:8766")
        with patch.object(client.session, "post", side_effect=ConnectionError("refused")):
            result = client.create_job(workspace_dir="/tmp/ws")
            self.assertIn("error", result)

    def test_passes_all_payload_fields(self):
        client = LocalizationClient("http://localhost:8766")
        with patch.object(client.session, "post", return_value=_mock_response(201, {})) as mock_post:
            client.create_job(
                workspace_dir="/ws",
                source_video="/v.mp4",
                source_subtitle="/s.srt",
                source_language="ja",
                target_language="zh-CN",
                subtitle_mode="bilingual",
                burn_subtitles=True,
                embed_soft_subtitles=False,
                dubbing_enabled=True,
                tts_provider="edge-tts",
                tts_voice="zh-CN-XiaoxiaoNeural",
                tts_concurrency=2,
                translation={"provider": "openai"},
                job_id="custom-id",
            )
            payload = mock_post.call_args[1]["json"]
            self.assertEqual(payload["workspace_dir"], "/ws")
            self.assertEqual(payload["source_video"], "/v.mp4")
            self.assertEqual(payload["source_subtitle"], "/s.srt")
            self.assertEqual(payload["source_language"], "ja")
            self.assertEqual(payload["target_language"], "zh-CN")
            self.assertEqual(payload["subtitle_mode"], "bilingual")
            self.assertEqual(payload["burn_subtitles"], True)
            self.assertEqual(payload["embed_soft_subtitles"], False)
            self.assertEqual(payload["dubbing_enabled"], True)
            self.assertEqual(payload["tts_provider"], "edge-tts")
            self.assertEqual(payload["tts_voice"], "zh-CN-XiaoxiaoNeural")
            self.assertEqual(payload["tts_concurrency"], 2)
            self.assertEqual(payload["translation"], {"provider": "openai"})
            self.assertEqual(payload["job_id"], "custom-id")


class TestGetJob(unittest.TestCase):
    def test_returns_job(self):
        client = LocalizationClient()
        with patch.object(client.session, "get", return_value=_mock_response(200, {"status": "running"})):
            result = client.get_job("job-1")
            self.assertEqual(result["status"], "running")

    def test_non_200_returns_none(self):
        client = LocalizationClient()
        with patch.object(client.session, "get", return_value=_mock_response(404)):
            self.assertIsNone(client.get_job("job-1"))

    def test_exception_returns_none(self):
        client = LocalizationClient()
        with patch.object(client.session, "get", side_effect=Exception("err")):
            self.assertIsNone(client.get_job("job-1"))


class TestCancelJob(unittest.TestCase):
    def test_cancel_success(self):
        client = LocalizationClient()
        with patch.object(client.session, "post", return_value=_mock_response(200, {"status": "cancelled"})):
            result = client.cancel_job("job-1")
            self.assertEqual(result["status"], "cancelled")

    def test_cancel_failure_returns_error(self):
        client = LocalizationClient()
        with patch.object(client.session, "post", return_value=_mock_response(500, text="server error")):
            result = client.cancel_job("job-1")
            self.assertIn("error", result)

    def test_cancel_exception_returns_error(self):
        client = LocalizationClient()
        with patch.object(client.session, "post", side_effect=Exception("network")):
            result = client.cancel_job("job-1")
            self.assertIn("error", result)


class TestRetryJob(unittest.TestCase):
    def test_retry_success(self):
        client = LocalizationClient()
        with patch.object(client.session, "post", return_value=_mock_response(200, {"status": "running"})):
            result = client.retry_job("job-1", from_stage="translate")
            self.assertEqual(result["status"], "running")

    def test_retry_default_stage(self):
        client = LocalizationClient()
        with patch.object(client.session, "post", return_value=_mock_response(200, {})) as mock_post:
            client.retry_job("job-1")
            payload = mock_post.call_args[1]["json"]
            self.assertEqual(payload["from_stage"], "translate")

    def test_retry_failure_returns_error(self):
        client = LocalizationClient()
        with patch.object(client.session, "post", side_effect=Exception("err")):
            result = client.retry_job("job-1")
            self.assertIn("error", result)


class TestGetLogs(unittest.TestCase):
    def test_returns_logs(self):
        client = LocalizationClient()
        logs = {"lines": ["info: started"], "tail": 100}
        with patch.object(client.session, "get", return_value=_mock_response(200, logs)):
            result = client.get_logs("job-1", tail=50)
            self.assertEqual(result, logs)

    def test_http_error_returns_error(self):
        client = LocalizationClient()
        with patch.object(client.session, "get", return_value=_mock_response(403)):
            result = client.get_logs("job-1")
            self.assertIn("error", result)


class TestWaitForResult(unittest.TestCase):
    def setUp(self):
        self.client = LocalizationClient("http://localhost:8766")

    def test_polls_until_completed(self):
        responses = [
            _mock_response(200, {"status": "running", "progress": 50, "message": "translating"}),
            _mock_response(200, {"status": "completed", "progress": 100, "message": "done"}),
        ]
        mock_get = MagicMock(side_effect=responses)
        with patch.object(self.client.session, "get", mock_get):
            result = self.client.wait_for_result("job-1", poll_interval=0.01)
            self.assertEqual(result["status"], "completed")

    def test_cancelled(self):
        mock_get = MagicMock(return_value=_mock_response(200, {"status": "running"}))
        with patch.object(self.client.session, "get", mock_get):
            result = self.client.wait_for_result(
                "job-1", poll_interval=0.01, cancel_checker=lambda: True
            )
            self.assertEqual(result["status"], "cancelled")

    def test_connection_lost_returns_error(self):
        mock_get = MagicMock(return_value=None)
        with patch.object(self.client.session, "get", mock_get):
            result = self.client.wait_for_result("job-1", poll_interval=0.01)
            self.assertEqual(result["status"], "error")

    def test_polls_error_status(self):
        mock_get = MagicMock(return_value=_mock_response(200, {"status": "error", "message": "failed"}))
        with patch.object(self.client.session, "get", mock_get):
            result = self.client.wait_for_result("job-1", poll_interval=0.01)
            self.assertEqual(result["status"], "error")

    def test_calls_progress_callback(self):
        calls = []
        responses = [
            _mock_response(200, {"status": "running", "progress": 30, "message": "working"}),
            _mock_response(200, {"status": "completed", "progress": 100, "message": "done"}),
        ]
        mock_get = MagicMock(side_effect=responses)
        with patch.object(self.client.session, "get", mock_get):
            self.client.wait_for_result(
                "job-1", poll_interval=0.01,
                progress_callback=lambda p, m, s: calls.append((p, m, s)),
            )
            self.assertGreaterEqual(len(calls), 1)
            self.assertEqual(calls[0], (30, "working", "running"))


class TestBaseUrlResolution(unittest.TestCase):
    def test_default_url(self):
        client = LocalizationClient()
        self.assertEqual(client.base_url, "http://127.0.0.1:8766")

    def test_custom_url(self):
        client = LocalizationClient("http://localhost:9999")
        self.assertEqual(client.base_url, "http://localhost:9999")

    @patch.dict("os.environ", {"LOCALIZATION_ENGINE_URL": "http://engine:8766"})
    def test_env_var_overrides_default(self):
        client = LocalizationClient()
        self.assertEqual(client.base_url, "http://engine:8766")

    def test_env_var_can_be_overridden_by_explicit(self):
        import os
        os.environ["LOCALIZATION_ENGINE_URL"] = "http://engine:8766"
        try:
            client = LocalizationClient("http://override:8766")
            self.assertEqual(client.base_url, "http://override:8766")
        finally:
            del os.environ["LOCALIZATION_ENGINE_URL"]
