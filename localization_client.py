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
import tempfile
import time
import uuid
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
        tts_concurrency: int = 1,
        tts_options: Optional[Dict[str, Any]] = None,
        original_volume: float = 0.0,
        low_vram_mode: bool = True,
        translation: Optional[Dict[str, Any]] = None,
        translation_preset_id: str = "",
        translation_preset_name: str = "",
        tts_preset_id: str = "",
        tts_preset_name: str = "",
        style: Optional[Dict[str, Any]] = None,
        job_id: str = "",
        confirm_preflight_warnings: bool = False,
        enforce_preflight: bool = True,
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
            "tts_concurrency": tts_concurrency,
            "tts_options": tts_options or {},
            "original_volume": original_volume,
            "low_vram_mode": low_vram_mode,
            "translation_preset_id": translation_preset_id,
            "translation_preset_name": translation_preset_name,
            "tts_preset_id": tts_preset_id,
            "tts_preset_name": tts_preset_name,
        }
        if job_id:
            payload["job_id"] = job_id
        if translation:
            payload["translation"] = translation
        if style:
            payload["style"] = style

        try:
            r = self.session.post(
                f"{self.base_url}/jobs",
                params={
                    "enforce_preflight": str(bool(enforce_preflight)).lower(),
                    "confirm_warnings": str(bool(confirm_preflight_warnings)).lower(),
                },
                json=payload,
                timeout=10,
            )
            if r.status_code in (200, 201):
                return r.json()
            if r.status_code in (409, 422):
                try:
                    detail = r.json().get("detail", {})
                except Exception:
                    detail = {}
                if isinstance(detail, dict) and detail.get("error_code"):
                    return {
                        "error": detail.get("error_code"),
                        "error_code": detail.get("error_code"),
                        "preflight": detail,
                    }
            return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
        except requests.exceptions.Timeout:
            return {"error": "请求超时，引擎可能未响应"}
        except requests.exceptions.ConnectionError:
            return {"error": "无法连接本地化引擎，请检查服务是否启动"}
        except Exception as e:
            return {"error": f"请求失败: {e}"}

    def preflight(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate task inputs without creating task history."""
        try:
            response = self.session.post(f"{self.base_url}/preflight", json=payload, timeout=15)
            if response.status_code == 200:
                return response.json()
            return {"error": f"HTTP {response.status_code}: {response.text[:300]}"}
        except Exception as exc:
            return {"error": str(exc)}

    def get_runtime_capabilities(self, workspace_dir: str = "") -> Dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}/runtime/capabilities",
                params={"workspace_dir": workspace_dir} if workspace_dir else None,
                timeout=10,
            )
            return response.json() if response.status_code == 200 else {"error": response.text[:300]}
        except Exception as exc:
            return {"error": str(exc)}

    def get_runtime_metrics(self, refresh: bool = False) -> Dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}/runtime/metrics",
                params={"refresh": str(bool(refresh)).lower()},
                timeout=10,
            )
            return response.json() if response.status_code == 200 else {"error": response.text[:300]}
        except Exception as exc:
            return {"error": str(exc)}

    def get_tts_providers(self) -> Dict[str, Any]:
        try:
            response = self.session.get(f"{self.base_url}/tts/providers", timeout=10)
            return response.json() if response.status_code == 200 else {"error": response.text[:300]}
        except Exception as exc:
            return {"error": str(exc)}

    def get_tts_voices(self, provider: str, language: str = "") -> Dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}/tts/providers/{provider}/voices",
                params={"language": language} if language else None,
                timeout=15,
            )
            return response.json() if response.status_code == 200 else {"error": response.text[:300]}
        except Exception as exc:
            return {"error": str(exc)}

    def preview_tts(self, payload: Dict[str, Any], output_path: str = "") -> Dict[str, Any]:
        preview_id = str(payload.get("preview_id") or uuid.uuid4().hex)
        payload = {**payload, "preview_id": preview_id}
        try:
            response = self.session.post(f"{self.base_url}/tts/preview", json=payload, timeout=310)
            if response.status_code != 200:
                try:
                    detail = response.json().get("detail", {})
                except Exception:
                    detail = {}
                return {
                    "error": detail.get("message") or response.text[:300],
                    "error_code": detail.get("error_code", "TTS_PREVIEW_FAILED"),
                    "preview_id": preview_id,
                }
            suffix = {
                "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/opus": ".opus",
                "audio/ogg": ".ogg", "audio/aac": ".aac", "audio/flac": ".flac",
            }.get(response.headers.get("Content-Type", "").split(";", 1)[0].lower(), ".audio")
            path = Path(output_path) if output_path else Path(tempfile.gettempdir()) / f"v2s-preview-{preview_id}{suffix}"
            path.write_bytes(response.content)
            return {
                "preview_id": response.headers.get("X-Preview-Id", preview_id),
                "path": str(path),
                "cached": response.headers.get("X-Preview-Cached", "false") == "true",
                "duration": float(response.headers.get("X-Preview-Duration", "0") or 0),
            }
        except Exception as exc:
            return {"error": str(exc), "preview_id": preview_id}

    def cancel_tts_preview(self, preview_id: str) -> bool:
        try:
            response = self.session.delete(f"{self.base_url}/tts/previews/{preview_id}", timeout=5)
            return response.status_code == 200 and bool(response.json().get("cancelled"))
        except Exception:
            return False

    def list_voice_presets(self) -> Dict[str, Any]:
        return self._voice_preset_request("GET", "/voice-presets")

    def create_voice_preset(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._voice_preset_request("POST", "/voice-presets", payload)

    def update_voice_preset(self, preset_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._voice_preset_request("PUT", f"/voice-presets/{preset_id}", payload)

    def delete_voice_preset(self, preset_id: str) -> Dict[str, Any]:
        return self._voice_preset_request("DELETE", f"/voice-presets/{preset_id}")

    def set_default_voice_preset(self, preset_id: str) -> Dict[str, Any]:
        return self._voice_preset_request("POST", f"/voice-presets/{preset_id}/default")

    def _voice_preset_request(self, method: str, path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        try:
            response = self.session.request(method, f"{self.base_url}{path}", json=payload, timeout=10)
            return response.json() if response.status_code < 300 else {"error": response.text[:300]}
        except Exception as exc:
            return {"error": str(exc)}

    def get_subtitle_document(self, job_id: str) -> Dict[str, Any]:
        return self._subtitle_request("GET", f"/jobs/{job_id}/subtitles")

    def save_subtitle_draft(self, job_id: str, document: Dict[str, Any], base_version: int) -> Dict[str, Any]:
        return self._subtitle_request(
            "PUT", f"/jobs/{job_id}/subtitles/draft",
            {"document": document, "base_version": base_version},
        )

    def validate_subtitle_document(self, job_id: str, document: Dict[str, Any]) -> Dict[str, Any]:
        return self._subtitle_request(
            "POST", f"/jobs/{job_id}/subtitles/validate", {"document": document}
        )

    def save_subtitle_revision(
        self, job_id: str, document: Dict[str, Any], base_version: int, *, regenerate: bool = False
    ) -> Dict[str, Any]:
        return self._subtitle_request(
            "POST", f"/jobs/{job_id}/subtitles/revisions",
            {"document": document, "base_version": base_version, "regenerate": regenerate},
        )

    def list_subtitle_revisions(self, job_id: str) -> Dict[str, Any]:
        return self._subtitle_request("GET", f"/jobs/{job_id}/subtitles/revisions")

    def restore_subtitle_revision(
        self, job_id: str, revision_id: str, base_version: int, *, regenerate: bool = False
    ) -> Dict[str, Any]:
        return self._subtitle_request(
            "POST", f"/jobs/{job_id}/subtitles/revisions/{revision_id}/restore",
            {"base_version": base_version, "regenerate": regenerate},
        )

    def _subtitle_request(self, method: str, path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        try:
            response = self.session.request(method, f"{self.base_url}{path}", json=payload, timeout=30)
            if response.status_code < 300:
                return response.json()
            try:
                detail = response.json().get("detail", {})
            except Exception:
                detail = {}
            return {
                "error": detail.get("message") or response.text[:300],
                "error_code": detail.get("error_code", "SUBTITLE_REQUEST_FAILED"),
            }
        except Exception as exc:
            return {"error": str(exc)}

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Query the status of a job. Returns None on connection failure."""
        try:
            r = self.session.get(f"{self.base_url}/jobs/{job_id}", timeout=10)
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:
            return None

    def list_jobs(self, **filters: Any) -> Dict[str, Any]:
        """Search persisted task history with server-side pagination."""
        params = {key: value for key, value in filters.items() if value not in (None, "")}
        try:
            response = self.session.get(f"{self.base_url}/jobs", params=params, timeout=10)
            if response.status_code == 200:
                return response.json()
            return {"error": f"HTTP {response.status_code}: {response.text[:200]}"}
        except Exception as exc:
            return {"error": str(exc)}

    def get_job_detail(self, job_id: str) -> Dict[str, Any]:
        """Return stage attempts, all artifacts, and recovery events."""
        try:
            response = self.session.get(f"{self.base_url}/jobs/{job_id}/detail", timeout=10)
            if response.status_code == 200:
                return response.json()
            return {"error": f"HTTP {response.status_code}: {response.text[:200]}"}
        except Exception as exc:
            return {"error": str(exc)}

    def retry_failed_stage(self, job_id: str) -> Dict[str, Any]:
        return self._post_job_action(job_id, "retry-failed")

    def rerun_job(self, job_id: str) -> Dict[str, Any]:
        return self._post_job_action(job_id, "rerun")

    def resume_job(self, job_id: str) -> Dict[str, Any]:
        return self._post_job_action(job_id, "resume")

    def _post_job_action(self, job_id: str, action: str) -> Dict[str, Any]:
        try:
            response = self.session.post(f"{self.base_url}/jobs/{job_id}/{action}", timeout=10)
            if response.status_code == 200:
                return response.json()
            return {"error": f"HTTP {response.status_code}: {response.text[:200]}"}
        except Exception as exc:
            return {"error": str(exc)}

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
        progress_callback: Callable[..., None] | None = None,
        poll_interval: float = 1.0,
        cancel_checker: Callable[[], bool] | None = None,
    ) -> Dict[str, Any]:
        """Poll until a job reaches a terminal state.

        Args:
            job_id: The job to poll.
            progress_callback: Called with (progress, message, status, stage) on each poll. Older 3-argument callbacks remain supported.
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
            stage = result.get("stage", "")

            if progress_callback:
                try:
                    progress_callback(progress, message, status, stage)
                except TypeError:
                    progress_callback(progress, message, status)

            if status in ("completed", "error", "cancelled", "interrupted"):
                return result

            time.sleep(poll_interval)
