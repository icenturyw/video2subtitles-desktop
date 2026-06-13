#!/usr/bin/env python3
"""Video2Subtitles Localization Engine - lightweight sidecar service.

A FastAPI-based HTTP service that handles subtitle translation, rendering,
and other localization tasks offloaded from the desktop client.

Default bind: 127.0.0.1:8766 (loopback only)
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# Engine imports
# ---------------------------------------------------------------------------
ENGINE_DIR = Path(__file__).resolve().parent
DATA_DIR = ENGINE_DIR / "data"

from engine.cancellation import CancellationRegistry
from engine.models import (
    CreateJobRequest,
    HealthResponse,
    LogResponse,
    RetryRequest,
    TaskResultResponse,
)
from engine.pipeline import start_pipeline
from engine.progress import ProgressTracker
from engine.task_store import TaskStore
from engine.workspace import get_log_path, read_log_tail, resolve_workspace

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("localization-engine")

# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------

VERSION = "0.1.0"

# Shared state container (initialized on startup)
_state: dict = {}


def _store() -> TaskStore:
    return _state["task_store"]


def _progress() -> ProgressTracker:
    return _state["progress"]


def _cancellation() -> CancellationRegistry:
    return _state["cancellation"]


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Manage startup and shutdown resources."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _state["task_store"] = TaskStore(DATA_DIR)
    _state["progress"] = ProgressTracker()
    _state["cancellation"] = CancellationRegistry()
    logger.info(
        "Localization Engine v%s started (data_dir=%s)", VERSION, DATA_DIR
    )
    yield
    logger.info("Localization Engine shutting down")


app = FastAPI(
    title="Video2Subtitles Localization Engine",
    version=VERSION,
    lifespan=lifespan,
)

# CORS: only loopback origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:*", "http://localhost:*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health():
    """Health check endpoint."""
    ffmpeg_available = shutil.which("ffmpeg") is not None

    # Check optional capabilities
    capabilities: Dict[str, Any] = {
        "translation": False,
        "rendering": ffmpeg_available,
        "whisperx": False,
        "tts": [],
    }

    # Check for translation provider availability (env var)
    if os.environ.get("V2S_TRANSLATION_API_KEY"):
        capabilities["translation"] = True

    # Check for edge-tts
    try:
        import importlib.util
        if importlib.util.find_spec("edge_tts") is not None:
            capabilities["tts"].append("edge-tts")
    except Exception:
        pass

    return HealthResponse(
        status="ok",
        service="video2subtitles-localization-engine",
        version=VERSION,
        capabilities=capabilities,
        ffmpeg=ffmpeg_available,
    )


@app.post("/jobs", response_model=TaskResultResponse)
def create_job(req: CreateJobRequest):
    """Create and start a new localization job."""
    job_id = req.job_id or str(uuid.uuid4())

    if _store().exists(job_id):
        existing = _store().get(job_id)
        if existing and existing.status in ("running", "pending"):
            raise HTTPException(
                status_code=409,
                detail=f"Job {job_id} is already {existing.status}",
            )

    # Validate workspace
    if not req.workspace_dir:
        raise HTTPException(status_code=400, detail="workspace_dir is required")

    # Build request payload for pipeline
    request_payload: Dict[str, Any] = {
        "job_id": job_id,
        "workspace_dir": req.workspace_dir,
        "source_video": req.source_video,
        "source_subtitle": req.source_subtitle,
        "source_language": req.source_language,
        "target_language": req.target_language,
        "subtitle_mode": req.subtitle_mode,
        "burn_subtitles": req.burn_subtitles,
        "embed_soft_subtitles": req.embed_soft_subtitles,
        "dubbing_enabled": req.dubbing_enabled,
        "tts_provider": req.tts_provider,
        "tts_voice": req.tts_voice,
    }
    if req.translation:
        request_payload["translation"] = req.translation.model_dump()
    if req.style:
        request_payload["style"] = req.style.model_dump()

    # Create task record
    _store().create(job_id, request_payload)

    # Register cancellation token and start pipeline
    cancel_token = _cancellation().register(job_id)
    start_pipeline(
        job_id=job_id,
        request=request_payload,
        task_store=_store(),
        progress=_progress(),
        cancel_token=cancel_token,
    )

    rec = _store().get(job_id)
    return _record_to_response(rec)


@app.get("/jobs/{job_id}", response_model=TaskResultResponse)
def get_job(job_id: str):
    """Query job status and progress."""
    rec = _store().get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Merge live progress from tracker
    progress_entry = _progress().get(job_id)
    if progress_entry and rec.status == "running":
        rec.stage = progress_entry.stage
        rec.progress = progress_entry.progress
        rec.message = progress_entry.message

    return _record_to_response(rec)


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    """Request cancellation of a running job."""
    rec = _store().get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if rec.status not in ("running", "pending"):
        return {"status": rec.status, "message": f"Job is {rec.status}, cannot cancel"}

    # Set cancellation flag
    _cancellation().cancel(job_id)

    # Update store status immediately for responsive UI
    _store().update(
        job_id,
        status="cancelled",
        stage="cancelled",
        message="取消请求已发送",
        error_code="TASK_CANCELLED",
    )

    return {"status": "cancelling", "message": "取消请求已发送"}


@app.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, req: RetryRequest):
    """Retry a failed or interrupted job from a specific stage."""
    rec = _store().get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if rec.status not in ("error", "interrupted", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail=f"Job is {rec.status}, can only retry from error/interrupted/cancelled",
        )

    # Reset progress and status
    _progress().reset(job_id)

    # Reset cancellation
    cancel_token = _cancellation().get(job_id)
    if cancel_token:
        cancel_token.reset()
    else:
        cancel_token = _cancellation().register(job_id)

    # Update status
    _store().update(
        job_id,
        status="pending",
        stage=req.from_stage,
        progress=0,
        message="重试中...",
        error_code=None,
        error_detail=None,
    )

    # Re-launch pipeline
    request_payload = rec.request_payload
    start_pipeline(
        job_id=job_id,
        request=request_payload,
        task_store=_store(),
        progress=_progress(),
        cancel_token=cancel_token,
    )

    return {"status": "retrying", "from_stage": req.from_stage}


@app.get("/jobs/{job_id}/logs", response_model=LogResponse)
def get_job_logs(job_id: str, tail: int = 100):
    """Get recent log lines for a job."""
    rec = _store().get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    workspace_dir = rec.request_payload.get("workspace_dir", "")
    log_path_str = ""
    lines: list[str] = []
    truncated = False

    if workspace_dir:
        try:
            ws = Path(workspace_dir).resolve()
            log_path = get_log_path(ws)
            log_path_str = str(log_path)
            lines, truncated = read_log_tail(ws, max_lines=max(1, min(tail, 500)))
        except Exception:
            pass

    return LogResponse(
        job_id=job_id,
        log_path=log_path_str,
        lines=lines,
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_to_response(rec) -> TaskResultResponse:
    """Convert a TaskRecord to an API response."""
    if rec is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return TaskResultResponse(
        job_id=rec.job_id,
        status=rec.status,
        stage=rec.stage,
        progress=rec.progress,
        message=rec.message,
        detected_language=rec.detected_language,
        artifacts=rec.artifacts,
        error_code=rec.error_code,
        error_detail=rec.error_detail,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    host = os.environ.get("LOCALIZATION_ENGINE_HOST", "127.0.0.1")
    port = int(os.environ.get("LOCALIZATION_ENGINE_PORT", "8766"))

    logger.info("Starting Localization Engine on %s:%d", host, port)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
