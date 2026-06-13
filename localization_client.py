"""HTTP client for the Localization Engine sidecar service (port 8766).

Provides a high-level Python API for the desktop GUI to:
- Check engine health
- Submit localization jobs (translate, subtitle export, dubbing)
- Poll job progress
- Cancel running jobs
- Retry failed/interrupted jobs
- Retrieve job logs
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

DEFAULT_ENGINE_URL = "http://127.0.0.1:8766"


class LocalizationClient:
    """Client for the Localization Engine HTTP API."""

    def __init__(self, base_url: str | None = None):
        self.base_url = (
            base_url
            or os.environ.get("LOCALIZATION_ENGINE_URL")
            or DEFAULT_ENGINE_URL
        ).rstrip("/")
        self.session = requests.Session()
        self.session.timeout = (5, 120)

    # -- Health -------------------------------------------------------------

    def health_check(self) -> bool:
        """Return True if the engine is healthy and reachable."""
        try:
            r = self.session.get(f"{self.base_url}/health", timeout=3)
            return r.status_code == 200
        except (requests.ConnectionError, requests.Timeout):
            return False

    def get_server_info(self) -> Optional[Dict[str, Any]]:
        """Return engine health info including capabilities, or None."""
        try:
            r = self.session.get(f"{self.base_url}/health", timeout=3)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    # -- Job management -----------------------------------------------------

    def create_job(
        self,
        workspace_dir: str,
        source_video: str = "",
        source_subtitle: str = "",
        *,
        source_language: str = "auto",
        target_language: str = "",
        subtitle_mode: str = "bilingual",
        burn_subtitles: bool = True,
        embed_soft_subtitles: bool = False,
        dubbing_enabled: bool = False,
        tts_provider: str = "edge-tts",
        tts_voice: str = "",
        translation: Optional[Dict[str, Any]] = None,
        style: Optional[Dict[str, Any]] = None,
        job_id: str = "",
    ) -> Dict[str, Any]:
        """Submit a new localization job.

        Returns the initial TaskResult dict, or {"error": ...} on failure.
        """
        payload: Dict[str, Any] = {
            "workspace_dir": workspace_dir,
            "source_video": source_video,
            "source_subtitle": source_subtitle,
            "source_language": source_language,
            "target_language": target_language,
            "subtitle_mode": subtitle_mode,
            "burn_subtitles": burn_subtitles,
            "embed_soft_subtitles": embed_soft_subtitles,
            "dubbing_enabled": dubbing_enabled,
            "tts_provider": tts_provider,
            "tts_voice": tts_voice,
        }
        if job_id:
            payload["job_id"] = job_id
        if translation:
            payload["translation"] = translation
        if style:
            payload["style"] = style

        try:
            r = self.session.post(
                f"{self.base_url}/jobs", json=payload, timeout=10
            )
            if r.status_code in (200, 201):
                return r.json()
            return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
        except requests.exceptions.Timeout:
            return {"error": "请求超时，引擎可能未响应"}
        except requests.exceptions.ConnectionError:
            return {"error": "无法连接本地化引擎，请检查服务是否启动"}
        except Exception as e:
            return {"error": f"请求失败: {e}"}

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Query the status of a job. Returns None on connection failure."""
        try:
            r = self.session.get(f"{self.base_url}/jobs/{job_id}", timeout=10)
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:
            return None

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Request cancellation of a running job."""
        try:
            r = self.session.post(
                f"{self.base_url}/jobs/{job_id}/cancel", timeout=10
            )
            if r.status_code == 200:
                return r.json()
            return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}

    def retry_job(self, job_id: str, from_stage: str = "translate") -> Dict[str, Any]:
        """Retry a failed/interrupted job from a given stage."""
        try:
            r = self.session.post(
                f"{self.base_url}/jobs/{job_id}/retry",
                json={"from_stage": from_stage},
                timeout=10,
            )
            if r.status_code == 200:
                return r.json()
            return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}

    def get_logs(self, job_id: str, tail: int = 100) -> Dict[str, Any]:
        """Retrieve recent log lines for a job."""
        try:
            r = self.session.get(
                f"{self.base_url}/jobs/{job_id}/logs",
                params={"tail": tail},
                timeout=10,
            )
            if r.status_code == 200:
                return r.json()
            return {"error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    # -- Convenience --------------------------------------------------------

    def wait_for_result(
        self,
        job_id: str,
        progress_callback: Callable[[int, str, str], None] | None = None,
        poll_interval: float = 1.0,
        cancel_checker: Callable[[], bool] | None = None,
    ) -> Dict[str, Any]:
        """Poll until a job reaches a terminal state.

        Args:
            job_id: The job to poll.
            progress_callback: Called with (progress, message, status) on each poll.
            poll_interval: Seconds between polls.
            cancel_checker: If returns True, abort polling and return cancelled.

        Returns:
            Final TaskResult dict, or {"status": "cancelled"/"error", ...}.
        """
        while True:
            if cancel_checker and cancel_checker():
                return {"status": "cancelled", "message": "任务已取消"}

            result = self.get_job(job_id)
            if result is None:
                return {"status": "error", "message": "无法连接到引擎"}

            status = result.get("status", "")
            progress = result.get("progress", 0)
            message = result.get("message", "")

            if progress_callback:
                progress_callback(progress, message, status)

            if status in ("completed", "error", "cancelled"):
                return result

            time.sleep(poll_interval)
