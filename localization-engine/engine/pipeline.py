"""Pipeline orchestrator for the localization engine.

The first version implements a minimal pipeline:
    prepare -> subtitle_export -> finalize

This validates the task system end-to-end. Later phases will add:
    transcribe, segment, translate, tts, audio_mix, render.
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from engine.cancellation import CancellationToken
from engine.progress import ProgressTracker
from engine.task_store import TaskStore
from engine.workspace import (
    ensure_log_dir,
    get_log_path,
    get_source_subtitle,
    resolve_workspace,
    write_log,
)

logger = logging.getLogger("engine.pipeline")


def _check_ffmpeg() -> bool:
    """Check if ffmpeg is available on PATH."""
    return shutil.which("ffmpeg") is not None


class PipelineRunner:
    """Runs pipeline stages for a single job in a background thread.

    The runner coordinates between the task store, progress tracker,
    and cancellation registry to execute stages sequentially with
    proper error handling and status updates.
    """

    def __init__(
        self,
        task_store: TaskStore,
        progress: ProgressTracker,
    ):
        self._store = task_store
        self._progress = progress

    def run_job(self, job_id: str, request: Dict[str, Any],
                cancel_token: CancellationToken) -> None:
        """Execute the pipeline for a job. Runs in a background thread."""
        try:
            self._execute(job_id, request, cancel_token)
        except Exception as exc:
            logger.exception("Pipeline failed for job %s", job_id)
            self._store.update(
                job_id,
                status="error",
                stage="error",
                message=f"Pipeline error: {exc}",
                error_code="PIPELINE_ERROR",
                error_detail=str(exc),
            )
            self._progress.update(job_id, "error", 0, str(exc))

    def _execute(self, job_id: str, request: Dict[str, Any],
                 cancel_token: CancellationToken) -> None:
        """Internal pipeline execution logic."""
        workspace_dir = request.get("workspace_dir", "")
        ws = resolve_workspace(workspace_dir)
        ensure_log_dir(ws)

        write_log(ws, f"Pipeline started for job {job_id}")
        self._store.update(job_id, status="running", stage="prepare")

        # -- Stage: prepare --
        if cancel_token.is_cancelled():
            self._mark_cancelled(job_id, ws)
            return

        self._progress.update(job_id, "prepare", 0, "准备中...")
        write_log(ws, "Stage: prepare")

        # Validate workspace has required inputs
        source_subtitle = request.get("source_subtitle", "")
        if source_subtitle:
            sub_path = Path(source_subtitle)
            if not sub_path.exists():
                # Try finding in workspace
                sub_path = get_source_subtitle(ws)
                if sub_path is None:
                    self._fail(job_id, ws, "SOURCE_SUBTITLE_NOT_FOUND",
                               "找不到源字幕文件")
                    return
        self._progress.update(job_id, "prepare", 100, "准备完成")

        # -- Stage: subtitle_export --
        if cancel_token.is_cancelled():
            self._mark_cancelled(job_id, ws)
            return

        self._store.update(job_id, stage="subtitle_export")
        self._progress.update(job_id, "subtitle_export", 0, "生成字幕文件...")
        write_log(ws, "Stage: subtitle_export")

        # MVP: copy source subtitle as output artifact
        subtitle_artifact = self._export_subtitles(job_id, ws, request)
        if subtitle_artifact:
            self._store.add_artifact(job_id, subtitle_artifact)

        self._progress.update(job_id, "subtitle_export", 100, "字幕生成完成")

        # -- Stage: finalize --
        if cancel_token.is_cancelled():
            self._mark_cancelled(job_id, ws)
            return

        self._store.update(job_id, stage="finalize")
        self._progress.update(job_id, "finalize", 0, "整理产物...")
        write_log(ws, "Stage: finalize")

        # Mark complete
        self._progress.update(job_id, "finalize", 100, "完成")
        self._store.update(
            job_id,
            status="completed",
            stage="completed",
            progress=100,
            message="处理完成",
        )
        write_log(ws, "Pipeline completed successfully")

    def _export_subtitles(self, job_id: str, ws: Path,
                          request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """MVP subtitle export: register source subtitle as artifact."""
        source_subtitle = request.get("source_subtitle", "")
        if source_subtitle and Path(source_subtitle).exists():
            sub_path = Path(source_subtitle)
            # Copy to workspace subtitles dir
            dest = ws / "subtitles" / sub_path.name
            if sub_path != dest:
                shutil.copy2(str(sub_path), str(dest))
            return {
                "kind": "source_srt",
                "path": f"subtitles/{dest.name}",
                "language": request.get("source_language", "auto"),
            }

        # Try finding in workspace
        found = get_source_subtitle(ws)
        if found:
            return {
                "kind": "source_srt",
                "path": f"subtitles/{found.name}",
                "language": request.get("source_language", "auto"),
            }
        return None

    def _mark_cancelled(self, job_id: str, ws: Path) -> None:
        """Mark a job as cancelled."""
        self._store.update(
            job_id,
            status="cancelled",
            stage="cancelled",
            message="任务已取消",
            error_code="TASK_CANCELLED",
        )
        self._progress.update(job_id, "cancelled", 0, "任务已取消")
        write_log(ws, "Pipeline cancelled")

    def _fail(self, job_id: str, ws: Path, code: str, message: str) -> None:
        """Mark a job as failed with an error code."""
        self._store.update(
            job_id,
            status="error",
            stage="error",
            message=message,
            error_code=code,
            error_detail=message,
        )
        self._progress.update(job_id, "error", 0, message)
        write_log(ws, f"Pipeline failed: [{code}] {message}")


def start_pipeline(
    job_id: str,
    request: Dict[str, Any],
    task_store: TaskStore,
    progress: ProgressTracker,
    cancel_token: CancellationToken,
) -> threading.Thread:
    """Start a pipeline job in a background thread.

    Returns:
        The started thread (for optional joining in tests).
    """
    runner = PipelineRunner(task_store, progress)
    thread = threading.Thread(
        target=runner.run_job,
        args=(job_id, request, cancel_token),
        daemon=True,
        name=f"pipeline-{job_id[:8]}",
    )
    thread.start()
    return thread
