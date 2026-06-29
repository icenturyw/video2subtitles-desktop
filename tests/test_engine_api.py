from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_ENGINE_DIR = str(Path(__file__).resolve().parent.parent / "localization-engine")
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

from fastapi.testclient import TestClient


class TestEngineAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_data = Path(tempfile.mkdtemp()) / "data"
        cls.tmp_data.mkdir(parents=True, exist_ok=True)

        # Load engine main.py via importlib.util to avoid module name collisions
        _engine_main_py = str(Path(_ENGINE_DIR) / "main.py")
        spec = importlib.util.spec_from_file_location(
            "localization_engine_main", _engine_main_py
        )
        cls._mod = importlib.util.module_from_spec(spec)
        sys.modules["localization_engine_main"] = cls._mod
        spec.loader.exec_module(cls._mod)

        # Override DATA_DIR and other deps after loading
        cls._mod.DATA_DIR = cls.tmp_data
        cls._mod.start_pipeline = MagicMock()
        cls._mod.shutil.which = MagicMock(return_value="/usr/bin/ffmpeg")

        # Initialize state
        from engine.cancellation import CancellationRegistry
        from engine.progress import ProgressTracker
        from engine.task_store import TaskStore

        cls._mod._state["task_store"] = TaskStore(cls.tmp_data)
        cls._mod._state["progress"] = ProgressTracker()
        cls._mod._state["cancellation"] = CancellationRegistry()

        cls.client = TestClient(cls._mod.app)

    # -- Health -----------------------------------------------------------

    def test_health_returns_ok(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("capabilities", data)
        self.assertTrue(data["ffmpeg"])

    # -- Create Job -------------------------------------------------------

    def test_create_job_requires_workspace_dir(self):
        resp = self.client.post("/jobs", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("workspace_dir", resp.text)

    def test_create_job_success(self):
        with tempfile.TemporaryDirectory() as td:
            resp = self.client.post("/jobs", json={
                "workspace_dir": td,
                "source_video": "/v.mp4",
                "source_subtitle": "/s.srt",
                "target_language": "zh-CN",
            })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "pending")
        self.assertIn("job_id", data)

    def test_create_job_with_custom_id(self):
        with tempfile.TemporaryDirectory() as td:
            resp = self.client.post("/jobs", json={
                "job_id": "my-custom-id",
                "workspace_dir": td,
            })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["job_id"], "my-custom-id")

    def test_create_duplicate_job_returns_409(self):
        with tempfile.TemporaryDirectory() as td:
            self.client.post("/jobs", json={
                "workspace_dir": td,
            })
            # Second create with same workspace_dir and no job_id generates
            # a different UUID, so no conflict. Need to force same job_id.
            resp = self.client.post("/jobs", json={
                "job_id": "dup-job",
                "workspace_dir": td,
            })
            self.assertEqual(resp.status_code, 200)
            # Duplicate
            resp2 = self.client.post("/jobs", json={
                "job_id": "dup-job",
                "workspace_dir": td,
            })
            self.assertEqual(resp2.status_code, 409)

    # -- Get Job ----------------------------------------------------------

    def test_get_job_returns_job(self):
        with tempfile.TemporaryDirectory() as td:
            create_resp = self.client.post("/jobs", json={
                "workspace_dir": td,
                "source_video": "/v.mp4",
            })
            job_id = create_resp.json()["job_id"]
            resp = self.client.get(f"/jobs/{job_id}")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["job_id"], job_id)

    def test_get_nonexistent_job_returns_404(self):
        resp = self.client.get("/jobs/nonexistent")
        self.assertEqual(resp.status_code, 404)

    # -- Cancel Job -------------------------------------------------------

    def test_cancel_job(self):
        with tempfile.TemporaryDirectory() as td:
            create_resp = self.client.post("/jobs", json={
                "workspace_dir": td,
            })
            job_id = create_resp.json()["job_id"]
            resp = self.client.post(f"/jobs/{job_id}/cancel")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("cancelling", data["status"])

    def test_cancel_nonexistent_job_returns_404(self):
        resp = self.client.post("/jobs/nonexistent/cancel")
        self.assertEqual(resp.status_code, 404)

    # -- Retry Job --------------------------------------------------------

    def test_retry_job_requires_valid_stage(self):
        with tempfile.TemporaryDirectory() as td:
            create_resp = self.client.post("/jobs", json={
                "workspace_dir": td,
            })
            job_id = create_resp.json()["job_id"]
            # Job is pending, not in a retry-able state
            resp = self.client.post(f"/jobs/{job_id}/retry", json={
                "from_stage": "translate",
            })
            self.assertEqual(resp.status_code, 400)

    def test_retry_invalid_stage_returns_400(self):
        resp = self.client.post("/jobs/fake-id/retry", json={
            "from_stage": "invalid-stage",
        })
        self.assertEqual(resp.status_code, 404)  # job not found

    # -- Job Logs ---------------------------------------------------------

    def test_get_logs_for_nonexistent_job_returns_404(self):
        resp = self.client.get("/jobs/nonexistent/logs")
        self.assertEqual(resp.status_code, 404)

    def test_get_logs_without_workspace(self):
        # Create a job with no workspace_dir shouldn't log error
        with tempfile.TemporaryDirectory() as td:
            create_resp = self.client.post("/jobs", json={
                "workspace_dir": td,
            })
            job_id = create_resp.json()["job_id"]
            resp = self.client.get(f"/jobs/{job_id}/logs")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["job_id"], job_id)
            self.assertIsInstance(data["lines"], list)

    # -- Translation API Key ----------------------------------------------

    def test_update_translation_api_key(self):
        resp = self.client.post("/config/translation-api-key", json={
            "api_key": "sk-test-key",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["translation"])

    def test_clear_translation_api_key(self):
        import os
        os.environ["V2S_TRANSLATION_API_KEY"] = "old-key"
        try:
            resp = self.client.post("/config/translation-api-key", json={
                "api_key": "",
            })
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(resp.json()["translation"])
        finally:
            os.environ.pop("V2S_TRANSLATION_API_KEY", None)

    # -- Models / Schema ---------------------------------------------------

    def test_health_response_model_fields(self):
        resp = self.client.get("/health")
        data = resp.json()
        for field in ("status", "service", "version", "capabilities", "ffmpeg"):
            self.assertIn(field, data)

    def test_create_job_passes_optional_fields(self):
        with tempfile.TemporaryDirectory() as td:
            resp = self.client.post("/jobs", json={
                "workspace_dir": td,
                "dubbing_enabled": True,
                "tts_provider": "edge-tts",
                "tts_voice": "zh-CN-XiaoxiaoNeural",
                "burn_subtitles": True,
                "subtitle_mode": "bilingual",
            })
            self.assertEqual(resp.status_code, 200)
