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
from fastapi import FastAPI, HTTPException, Query
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
    TranslationApiKeyRequest,
    TaskResultResponse,
)
from engine.pipeline import start_pipeline
from engine.progress import ProgressTracker
from engine.json_migration import migrate_tasks_json
from engine.repository import TaskQuery, TaskRepository
from engine.retry import RetryPlanner, RetryPlanningError
from engine.sqlite_repository import SQLiteTaskRepository
from engine.stages import STAGE_NAMES
from engine.workspace import get_log_path, read_log_tail, resolve_workspace

RETRY_STAGES = set(STAGE_NAMES) | {"failed", "all"}

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


def _store() -> TaskRepository:
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
    repository = SQLiteTaskRepository(DATA_DIR)
    migration = migrate_tasks_json(repository)
    _state["task_store"] = repository
    _state["json_migration"] = migration
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

    if os.environ.get("OPENAI_TTS_API_KEY") or os.environ.get("V2S_TTS_API_KEY"):
        capabilities["tts"].append("openai-compatible")

    # Check for qwen3-tts sidecar
    try:
        import urllib.request
        with urllib.request.urlopen(
            "http://127.0.0.1:8767/health", timeout=1.5
        ) as resp:
            if resp.status == 200:
                capabilities["tts"].append("qwen3-tts")
    except Exception:
        pass

    return HealthResponse(
        status="ok",
        service="video2subtitles-localization-engine",
        version=VERSION,
        capabilities=capabilities,
        ffmpeg=ffmpeg_available,
    )


@app.post("/config/translation-api-key")
def update_translation_api_key(req: TranslationApiKeyRequest):
    """Update the in-process translation API key without restarting the engine."""
    key = (req.api_key or "").strip()
    if key:
        os.environ["V2S_TRANSLATION_API_KEY"] = key
    else:
        os.environ.pop("V2S_TRANSLATION_API_KEY", None)
    return {"status": "ok", "translation": bool(key)}


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
        "tts_concurrency": req.tts_concurrency,
        "tts_options": dict(req.tts_options or {}),
        "original_volume": req.original_volume,
        "low_vram_mode": req.low_vram_mode,
        "translation_preset_id": req.translation_preset_id,
        "translation_preset_name": req.translation_preset_name,
        "tts_preset_id": req.tts_preset_id,
        "tts_preset_name": req.tts_preset_name,
    }
    tts_secret_env = {
        "volcengine_api_key": "VOLCENGINE_TTS_API_KEY",
        "volcengine_app_id": "VOLCENGINE_TTS_APP_ID",
        "volcengine_access_key": "VOLCENGINE_TTS_ACCESS_KEY",
        "api_key": "OPENAI_TTS_API_KEY",
        "openai_api_key": "OPENAI_TTS_API_KEY",
        "openai_tts_api_key": "OPENAI_TTS_API_KEY",
    }
    for option_key, env_name in tts_secret_env.items():
        secret = str(request_payload["tts_options"].get(option_key, "") or "").strip()
        if secret:
            os.environ[env_name] = secret
            request_payload["tts_options"].pop(option_key, None)
    if req.translation:
        if req.translation.api_key:
            key_env = req.translation.api_key_env or "V2S_TRANSLATION_API_KEY"
            os.environ[key_env] = req.translation.api_key
        request_payload["translation"] = req.translation.model_dump(exclude={"api_key"})
    if req.style:
        request_payload["style"] = req.style.model_dump()

    # Create task record
    existing = _store().get(job_id)
    if existing is None:
        _store().create(job_id, request_payload)
    else:
        _store().update(
            job_id,
            status="pending",
            stage="prepare",
            progress=0,
            message="",
            error_code=None,
            error_detail=None,
            request_payload=request_payload,
        )

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


@app.get("/jobs")
def list_jobs(
    keyword: str = "",
    status: str = "",
    stage: str = "",
    created_from: str = "",
    created_to: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = "created_at",
    sort_order: str = "desc",
):
    """Search task history with filters, paging, and safe sorting."""
    try:
        result = _store().search(TaskQuery(
            keyword=keyword,
            status=status,
            stage=stage,
            created_from=created_from,
            created_to=created_to,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        ))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    items = []
    for record in result.items:
        item = record.to_dict()
        item.pop("request_payload", None)
        item.pop("current_stage", None)
        items.append(item)
    return {
        "items": items,
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "pages": result.pages,
    }


@app.get("/jobs/{job_id}/detail")
def get_job_detail(job_id: str):
    """Return task metadata together with immutable execution history."""
    rec = _store().get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    repository = _store()
    stage_runs = getattr(repository, "list_stage_runs", lambda _job_id: [])(job_id)
    artifacts = getattr(repository, "list_artifacts", lambda _job_id, **_kwargs: rec.artifacts)(
        job_id, current_only=False
    )
    events = getattr(repository, "list_events", lambda _job_id: [])(job_id)
    task = rec.to_dict()
    task.pop("request_payload", None)
    task.pop("artifacts", None)
    return {"task": task, "stage_runs": stage_runs, "artifacts": artifacts, "events": events}


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


def _legacy_retry_job(job_id: str, req: RetryRequest):
    """Retry a failed or interrupted job from a specific stage."""
    rec = _store().get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if req.from_stage not in RETRY_STAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported retry stage: {req.from_stage}",
        )

    if rec.status not in ("completed", "error", "interrupted", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail=f"Job is {rec.status}, can only retry from completed/error/interrupted/cancelled",
        )

    # Reset progress and status
    _progress().reset(job_id)

    # Reset cancellation
    cancel_token = _cancellation().get(job_id)
    if cancel_token:
        cancel_token.reset()
    else:
        cancel_token = _cancellation().register(job_id)

    request_payload = dict(rec.request_payload or {})
    request_payload["resume_stage"] = req.from_stage

    # Update status
    _store().update(
        job_id,
        status="pending",
        stage=req.from_stage,
        progress=0,
        message="重试中...",
        error_code=None,
        error_detail=None,
        request_payload=request_payload,
    )

    # Re-launch pipeline
    start_pipeline(
        job_id=job_id,
        request=request_payload,
        task_store=_store(),
        progress=_progress(),
        cancel_token=cancel_token,
    )

    return {"status": "retrying", "from_stage": req.from_stage}


@app.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, req: RetryRequest):
    """Plan and launch a concurrency-safe stage rerun."""
    if not isinstance(_store(), SQLiteTaskRepository):
        return _legacy_retry_job(job_id, req)
    rec = _store().get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if req.from_stage not in RETRY_STAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported retry stage: {req.from_stage}")

    planner = RetryPlanner(_store())
    try:
        if req.from_stage == "failed":
            plan = planner.retry_latest_failed(job_id)
        elif req.from_stage == "all":
            plan = planner.rerun_all(job_id)
        else:
            plan = planner.plan_from(job_id, req.from_stage)
    except RetryPlanningError as exc:
        status_code = 409 if exc.error_code == "TASK_ALREADY_RUNNING" else 400
        raise HTTPException(
            status_code=status_code,
            detail={"error_code": exc.error_code, "message": str(exc)},
        ) from exc

    _progress().reset(job_id)
    cancel_token = _cancellation().get(job_id)
    if cancel_token:
        cancel_token.reset()
    else:
        cancel_token = _cancellation().register(job_id)
    try:
        start_pipeline(
            job_id=job_id,
            request=plan.request_payload,
            task_store=_store(),
            progress=_progress(),
            cancel_token=cancel_token,
        )
    except Exception:
        _store().release_run_lease(job_id, plan.lease_token)
        raise
    return {
        "status": "retrying",
        "requested_stage": plan.requested_stage,
        "from_stage": plan.start_stage,
        "invalidated_artifacts": plan.invalidated_artifacts,
    }


@app.post("/jobs/{job_id}/retry-failed")
def retry_failed_stage(job_id: str):
    return retry_job(job_id, RetryRequest(from_stage="failed"))


@app.post("/jobs/{job_id}/rerun")
def rerun_job(job_id: str):
    return retry_job(job_id, RetryRequest(from_stage="all"))


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
