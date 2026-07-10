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
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

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
from engine.workspace import get_log_path, read_log_tail
from engine.runtime import (
    ModelResourceManager,
    PreflightChecker,
    RuntimeCapabilities,
    RuntimeMonitor,
    create_gpu_monitor,
)
from engine.events import ErrorGuidanceRegistry, PipelineEventPublisher
from tts import get_provider, list_available_providers, provider_registry
from tts.preview import TTSPreviewError, TTSPreviewService
from subtitles import (
    DeleteCue,
    FindReplace,
    InsertCue,
    MergeCues,
    ShiftCues,
    SplitCue,
    SubtitleDocument,
    SubtitleDocumentError,
    SubtitleDocumentService,
    SubtitleEditor,
    SubtitleVersionConflictError,
    UpdateCue,
)
from subtitles.normalize import read_subtitle_file

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


def _runtime_monitor() -> RuntimeMonitor:
    monitor = _state.get("runtime_monitor")
    if monitor is None:
        monitor = RuntimeMonitor(DATA_DIR)
        _state["runtime_monitor"] = monitor
    return monitor


def _model_resources() -> ModelResourceManager:
    manager = _state.get("model_resources")
    if manager is None:
        manager = ModelResourceManager()
        _state["model_resources"] = manager
    return manager


def _preflight_checker() -> PreflightChecker:
    checker = _state.get("preflight_checker")
    if checker is None:
        checker = PreflightChecker(tts_provider_exists=_tts_provider_exists)
        _state["preflight_checker"] = checker
    return checker


def _tts_preview() -> TTSPreviewService:
    service = _state.get("tts_preview")
    if service is None:
        service = TTSPreviewService(
            DATA_DIR / "tts_previews", provider_registry,
            lambda name: get_provider(name, DATA_DIR / "tts_preview_provider_cache"),
            model_resources=_model_resources(),
        )
        _state["tts_preview"] = service
    return service


def _subtitle_documents() -> SubtitleDocumentService:
    service = _state.get("subtitle_documents")
    if service is None:
        service = SubtitleDocumentService(DATA_DIR / "task_artifacts", _store())
        _state["subtitle_documents"] = service
    return service


def _tts_provider_exists(name: str) -> bool:
    try:
        from tts.registry import provider_registry
        provider_registry.canonical_name(name)
        return True
    except (ImportError, ValueError):
        return False


def _safe_preset_parameters(parameters: Any) -> Dict[str, Any]:
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be an object")
    sensitive = (
        "api_key", "apikey", "secret", "token", "access_key", "credential", "password"
    )
    blocked = []

    def inspect(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child = f"{path}.{key}" if path else str(key)
                normalized = str(key).lower().replace("-", "_")
                if any(marker in normalized for marker in sensitive):
                    blocked.append(child)
                inspect(item, child)
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                inspect(item, f"{path}[{index}]")

    inspect(parameters)
    if blocked:
        raise ValueError(f"Sensitive parameters cannot be saved in presets: {sorted(blocked)}")
    return dict(parameters)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Manage startup and shutdown resources."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    repository = SQLiteTaskRepository(DATA_DIR)
    migration = migrate_tasks_json(repository)
    recovered = repository.recover_interrupted()
    _state["task_store"] = repository
    _state["json_migration"] = migration
    _state["recovered_tasks"] = recovered
    _state["cancellation"] = CancellationRegistry()
    gpu_monitor = create_gpu_monitor()

    def available_vram_mb() -> int:
        latest = _state.get("runtime_monitor")
        snapshot = latest.latest() if latest else None
        return max((gpu.memory_free_mb for gpu in snapshot.gpus), default=0) if snapshot else 0

    models = ModelResourceManager(available_vram_mb=available_vram_mb)

    def enforce_model_policy(snapshot) -> None:
        models.evict_idle()
        if any(
            gpu.memory_total_mb > 0 and gpu.memory_free_mb / gpu.memory_total_mb < 0.1
            for gpu in snapshot.gpus
        ):
            models.relieve_memory_pressure(512)

    monitor = RuntimeMonitor(
        DATA_DIR,
        active_task_checker=lambda: any(
            record.status in {"pending", "running"} for record in repository.list_all()
        ),
        loaded_models_provider=models.loaded_models,
        gpu_monitor=gpu_monitor,
        sample_observer=enforce_model_policy,
    )
    _state["model_resources"] = models
    _state["runtime_monitor"] = monitor
    events = PipelineEventPublisher(
        repository,
        resource_summary_provider=monitor.summary,
    )
    _state["events"] = events
    _state["guidance"] = ErrorGuidanceRegistry()
    _state["progress"] = ProgressTracker(events.publish_progress)
    _state["preflight_checker"] = PreflightChecker(
        gpu_monitor=gpu_monitor,
        tts_provider_exists=_tts_provider_exists,
    )
    _state["tts_preview"] = TTSPreviewService(
        DATA_DIR / "tts_previews", provider_registry,
        lambda name: get_provider(name, DATA_DIR / "tts_preview_provider_cache"),
        model_resources=models,
    )
    _state["subtitle_documents"] = SubtitleDocumentService(
        DATA_DIR / "task_artifacts", repository
    )
    cleanup_preview = getattr(_state.get("tts_preview"), "cleanup", None)
    if cleanup_preview:
        cleanup_preview()
    monitor.start()
    logger.info(
        "Localization Engine v%s started (data_dir=%s)", VERSION, DATA_DIR
    )
    yield
    monitor.stop()
    cleanup_preview = getattr(_state.get("tts_preview"), "cleanup", None)
    if cleanup_preview:
        cleanup_preview()
    blocked_models = models.shutdown()
    if blocked_models:
        logger.warning("Models still leased during shutdown: %s", blocked_models)
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


@app.get("/runtime/capabilities")
def runtime_capabilities(workspace_dir: str = ""):
    """Return stable environment capabilities; GPU failures are non-fatal."""
    workspace = Path(workspace_dir).expanduser() if workspace_dir else DATA_DIR
    return RuntimeCapabilities.detect(
        workspace,
        gpu_monitor=_runtime_monitor().gpu_monitor,
    ).to_dict()


@app.get("/runtime/metrics")
def runtime_metrics(refresh: bool = False):
    """Return the latest in-memory sample without writing it to SQLite."""
    monitor = _runtime_monitor()
    snapshot = monitor.sample_once() if refresh or monitor.latest() is None else monitor.latest()
    return {
        "metrics": snapshot.to_dict() if snapshot else None,
        "summary": monitor.summary(),
        "models": _model_resources().status(),
    }


@app.get("/runtime/models")
def runtime_models():
    return {"models": _model_resources().status()}


@app.post("/preflight")
def preflight(req: CreateJobRequest):
    """Inspect a request without creating task history or starting work."""
    payload = req.model_dump()
    translation = payload.get("translation")
    if hasattr(translation, "model_dump"):
        payload["translation"] = translation.model_dump()
    return _preflight_checker().check(payload).to_dict()


@app.get("/tts/providers")
def tts_providers():
    availability = {item["name"]: item for item in list_available_providers()}
    providers = []
    for name in provider_registry.names():
        item = dict(availability.get(name, {"name": name, "available": True}))
        item["capabilities"] = provider_registry.capabilities(name).to_dict()
        providers.append(item)
    return {"providers": providers}


@app.get("/tts/providers/{provider_name}/voices")
def tts_provider_voices(provider_name: str, language: str = ""):
    try:
        canonical = provider_registry.canonical_name(provider_name)
        voices = get_provider(canonical, DATA_DIR / "tts_preview_provider_cache").list_voices(language or None)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "TTS_PROVIDER_NOT_FOUND", "message": str(exc)}) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"error_code": "TTS_VOICE_LIST_FAILED", "message": str(exc)}) from exc
    return {"provider": canonical, "voices": voices}


@app.post("/tts/preview")
def create_tts_preview(payload: Dict[str, Any]):
    options = dict(payload.get("options") or {})
    secret_env = {
        "api_key": "OPENAI_TTS_API_KEY",
        "openai_api_key": "OPENAI_TTS_API_KEY",
        "openai_tts_api_key": "OPENAI_TTS_API_KEY",
        "fish_api_key": "FISH_TTS_API_KEY",
        "fish_audio_api_key": "FISH_TTS_API_KEY",
        "volcengine_api_key": "VOLCENGINE_TTS_API_KEY",
        "volcengine_app_id": "VOLCENGINE_TTS_APP_ID",
        "volcengine_access_key": "VOLCENGINE_TTS_ACCESS_KEY",
    }
    for key, env_name in secret_env.items():
        secret = str(options.pop(key, "") or "").strip()
        if secret:
            os.environ[env_name] = secret
    try:
        result = _tts_preview().preview(
            text=str(payload.get("text") or ""),
            provider_name=str(payload.get("provider") or ""),
            voice=str(payload.get("voice") or ""),
            language=str(payload.get("language") or ""),
            options=options,
            preview_id=str(payload.get("preview_id") or ""),
            timeout_seconds=float(payload.get("timeout_seconds") or 60),
        )
    except TTSPreviewError as exc:
        status = 404 if exc.error_code in {"TTS_PROVIDER_NOT_FOUND", "TTS_VOICE_NOT_FOUND"} else 400
        if exc.error_code == "TTS_PREVIEW_TIMEOUT":
            status = 504
        raise HTTPException(status_code=status, detail={"error_code": exc.error_code, "message": str(exc)}) from exc
    return FileResponse(
        result.path,
        media_type=result.media_type,
        filename=result.path.name,
        headers={
            "X-Preview-Id": result.preview_id,
            "X-Preview-Cached": str(result.cached).lower(),
            "X-Preview-Duration": str(result.duration_seconds),
        },
    )


@app.delete("/tts/previews/{preview_id}")
def cancel_tts_preview(preview_id: str):
    return {"preview_id": preview_id, "cancelled": _tts_preview().cancel(preview_id)}


@app.get("/voice-presets")
def list_voice_presets():
    return {"presets": getattr(_store(), "list_voice_presets")()}


@app.post("/voice-presets")
def create_voice_preset(payload: Dict[str, Any]):
    try:
        parameters = _safe_preset_parameters(payload.get("parameters") or {})
        return getattr(_store(), "create_voice_preset")(
            name=payload.get("name"), provider=payload.get("provider"),
            voice_id=payload.get("voice_id", ""), language=payload.get("language", ""),
            parameters=parameters, is_default=bool(payload.get("is_default", False)),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"error_code": "VOICE_PRESET_INVALID", "message": str(exc)}) from exc


@app.put("/voice-presets/{preset_id}")
def update_voice_preset(preset_id: str, payload: Dict[str, Any]):
    updates = dict(payload)
    try:
        if "parameters" in updates:
            updates["parameters"] = _safe_preset_parameters(updates["parameters"])
        preset = getattr(_store(), "update_voice_preset")(preset_id, **updates)
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"error_code": "VOICE_PRESET_INVALID", "message": str(exc)}) from exc
    if preset is None:
        raise HTTPException(status_code=404, detail={"error_code": "VOICE_PRESET_NOT_FOUND"})
    return preset


@app.delete("/voice-presets/{preset_id}")
def delete_voice_preset(preset_id: str):
    if not getattr(_store(), "delete_voice_preset")(preset_id):
        raise HTTPException(status_code=404, detail={"error_code": "VOICE_PRESET_NOT_FOUND"})
    return {"deleted": True, "id": preset_id}


@app.post("/voice-presets/{preset_id}/default")
def set_default_voice_preset(preset_id: str):
    preset = getattr(_store(), "set_default_voice_preset")(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail={"error_code": "VOICE_PRESET_NOT_FOUND"})
    return preset


@app.post("/jobs", response_model=TaskResultResponse)
def create_job(
    req: CreateJobRequest,
    enforce_preflight: bool = False,
    confirm_warnings: bool = False,
):
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
        "fish_api_key": "FISH_TTS_API_KEY",
        "fish_audio_api_key": "FISH_TTS_API_KEY",
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

    if req.tts_preset_id:
        get_preset = getattr(_store(), "get_voice_preset", None)
        preset = get_preset(req.tts_preset_id) if get_preset else None
        if preset is None:
            raise HTTPException(status_code=400, detail={"error_code": "VOICE_PRESET_NOT_FOUND"})
        request_payload["tts_provider"] = preset["provider"]
        request_payload["tts_voice"] = preset["voice_id"]
        if preset.get("language") and not request_payload.get("target_language"):
            request_payload["target_language"] = preset["language"]
        merged_options = dict(preset.get("parameters") or {})
        merged_options.update(request_payload.get("tts_options") or {})
        request_payload["tts_options"] = merged_options
        request_payload["tts_preset_name"] = preset["name"]
        request_payload["tts_preset_snapshot"] = {
            "name": preset["name"], "provider": preset["provider"],
            "voice_id": preset["voice_id"], "language": preset["language"],
            "parameters": merged_options,
        }

    # New desktop clients opt into the strict gate. The opt-in preserves the
    # established direct API contract while the UI migration rolls out.
    if enforce_preflight:
        preflight_result = _preflight_checker().check(request_payload)
        if preflight_result.errors:
            raise HTTPException(status_code=422, detail={
                "error_code": "PREFLIGHT_FAILED",
                **preflight_result.to_dict(),
            })
        if preflight_result.warnings and not confirm_warnings:
            raise HTTPException(status_code=409, detail={
                "error_code": "PREFLIGHT_CONFIRMATION_REQUIRED",
                **preflight_result.to_dict(),
            })

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
        model_resources=_model_resources(),
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


@app.get("/jobs/{job_id}/subtitles")
def get_subtitle_document(job_id: str):
    document = _ensure_task_subtitle_document(job_id)
    draft = _subtitle_documents().recover_draft(document.document_id)
    return {
        "document": document.to_dict(),
        "draft": draft.to_dict() if draft else None,
        "issues": [issue.to_dict() for issue in _subtitle_documents().validate(document)],
    }


@app.put("/jobs/{job_id}/subtitles/draft")
def save_subtitle_draft(job_id: str, payload: Dict[str, Any]):
    document = _subtitle_request_document(job_id, payload)
    try:
        revision = _subtitle_documents().save_draft(
            document, base_version=int(payload.get("base_version", document.version))
        )
    except (SubtitleDocumentError, SubtitleVersionConflictError) as exc:
        _raise_subtitle_http(exc)
    return {"status": "saved", "draft": revision}


@app.post("/jobs/{job_id}/subtitles/validate")
def validate_subtitle_document(job_id: str, payload: Dict[str, Any]):
    document = _subtitle_request_document(job_id, payload)
    issues = _subtitle_documents().validate(document)
    return {"issues": [issue.to_dict() for issue in issues]}


@app.post("/jobs/{job_id}/subtitles/edit")
def edit_subtitle_document(job_id: str, payload: Dict[str, Any]):
    document = _subtitle_request_document(job_id, payload)
    command = _subtitle_command(payload.get("command") or {})
    try:
        edited = SubtitleEditor(document).execute(command)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "SUBTITLE_EDIT_INVALID", "message": str(exc)},
        ) from exc
    return {
        "document": edited.to_dict(),
        "issues": [issue.to_dict() for issue in _subtitle_documents().validate(edited)],
    }


@app.post("/jobs/{job_id}/subtitles/revisions")
def save_subtitle_revision(job_id: str, payload: Dict[str, Any]):
    document = _subtitle_request_document(job_id, payload)
    regenerate = bool(payload.get("regenerate", False))
    try:
        result = _subtitle_documents().save_revision(
            document,
            base_version=int(payload.get("base_version", document.version)),
            regenerate=regenerate,
        )
    except (SubtitleDocumentError, SubtitleVersionConflictError) as exc:
        _raise_subtitle_http(exc)
    if result.retry_plan:
        _launch_retry_plan(result.retry_plan)
    return {
        "document": result.document.to_dict(),
        "revision": result.revision,
        "issues": [issue.to_dict() for issue in result.issues],
        "invalidated_artifacts": result.invalidated_artifacts,
        "regenerating": bool(result.retry_plan),
        "retry_from": result.retry_plan.start_stage if result.retry_plan else None,
    }


@app.get("/jobs/{job_id}/subtitles/revisions")
def list_subtitle_revisions(job_id: str):
    document = _ensure_task_subtitle_document(job_id)
    return {"revisions": _subtitle_documents().list_revisions(document.document_id)}


@app.post("/jobs/{job_id}/subtitles/revisions/{revision_id}/restore")
def restore_subtitle_revision(job_id: str, revision_id: str, payload: Dict[str, Any]):
    document = _ensure_task_subtitle_document(job_id)
    try:
        result = _subtitle_documents().restore_revision(
            document.document_id,
            revision_id,
            base_version=int(payload.get("base_version", document.version)),
            regenerate=bool(payload.get("regenerate", False)),
        )
    except (SubtitleDocumentError, SubtitleVersionConflictError) as exc:
        _raise_subtitle_http(exc)
    if result.retry_plan:
        _launch_retry_plan(result.retry_plan)
    return {
        "document": result.document.to_dict(),
        "revision": result.revision,
        "invalidated_artifacts": result.invalidated_artifacts,
        "regenerating": bool(result.retry_plan),
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
    for run in stage_runs:
        run["elapsed_seconds"] = _stage_elapsed_seconds(run)
    task = rec.to_dict()
    task.pop("request_payload", None)
    task.pop("artifacts", None)
    monitor = _runtime_monitor()
    latest = monitor.latest()
    guidance = _state.get("guidance") or ErrorGuidanceRegistry()
    return {
        "task": task,
        "stage_runs": stage_runs,
        "artifacts": artifacts,
        "events": events,
        "runtime": latest.to_dict() if latest else None,
        "loaded_models": _model_resources().loaded_models(),
        "guidance": guidance.guidance(rec.error_code or ""),
        "log_endpoint": f"/jobs/{job_id}/logs",
    }


@app.get("/errors/{error_code}/guidance")
def error_guidance(error_code: str):
    registry = _state.get("guidance") or ErrorGuidanceRegistry()
    return {"error_code": error_code, "actions": registry.guidance(error_code)}


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
        model_resources=_model_resources(),
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
            model_resources=_model_resources(),
            run_lease_token=plan.lease_token,
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


@app.post("/jobs/{job_id}/resume")
def resume_job(job_id: str):
    """Continue an interrupted task by safely rerunning its current stage."""
    rec = _store().get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if rec.status not in {"interrupted", "paused"}:
        raise HTTPException(
            status_code=400,
            detail=f"Job is {rec.status}; only interrupted tasks can be resumed",
        )
    return retry_job(job_id, RetryRequest(from_stage=rec.stage))


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

def _ensure_task_subtitle_document(job_id: str) -> SubtitleDocument:
    record = _store().get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error_code": "TASK_NOT_FOUND"})
    existing = getattr(_store(), "get_task_subtitle_document", lambda _task_id: None)(job_id)
    if existing:
        return _subtitle_documents().load_current(existing["id"])
    request = dict(record.request_payload or {})
    source_text = str(request.get("source_subtitle") or "").strip()
    source = Path(source_text).expanduser() if source_text else None
    if not source or not source.is_file():
        workspace_text = str(request.get("workspace_dir") or "").strip()
        source = get_source_subtitle(Path(workspace_text)) if workspace_text else None
    segments = read_subtitle_file(source) if source else None
    if not segments:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "SOURCE_SUBTITLE_NOT_FOUND", "message": "No editable source subtitle was found"},
        )
    try:
        return _subtitle_documents().create_from_segments(
            job_id,
            segments,
            source_language=str(request.get("source_language") or ""),
            target_language=str(request.get("target_language") or ""),
        )
    except SubtitleDocumentError as exc:
        _raise_subtitle_http(exc)


def _subtitle_request_document(job_id: str, payload: Dict[str, Any]) -> SubtitleDocument:
    current = _ensure_task_subtitle_document(job_id)
    raw = payload.get("document")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail={"error_code": "SUBTITLE_DOCUMENT_REQUIRED"})
    try:
        document = SubtitleDocument.from_dict(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "SUBTITLE_DOCUMENT_INVALID", "message": str(exc)},
        ) from exc
    if document.task_id != job_id or document.document_id != current.document_id:
        raise HTTPException(status_code=400, detail={"error_code": "SUBTITLE_DOCUMENT_MISMATCH"})
    return document


def _subtitle_command(data: Dict[str, Any]):
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail={"error_code": "SUBTITLE_COMMAND_REQUIRED"})
    command_type = str(data.get("type") or "").lower()
    if command_type == "update":
        return UpdateCue(str(data.get("cue_id") or ""), dict(data.get("changes") or {}))
    if command_type == "insert":
        return InsertCue(
            int(data.get("start_ms", 0)), int(data.get("end_ms", 0)),
            str(data.get("source_text") or ""), str(data.get("translated_text") or ""),
            str(data.get("after_cue_id") or ""), str(data.get("cue_id") or ""),
        )
    if command_type == "delete":
        return DeleteCue(str(data.get("cue_id") or ""))
    if command_type == "split":
        split_ms = data.get("split_ms")
        return SplitCue(
            str(data.get("cue_id") or ""), int(data.get("character_index", 0)),
            int(split_ms) if split_ms is not None else None,
        )
    if command_type == "merge":
        return MergeCues(
            str(data.get("first_cue_id") or ""), str(data.get("second_cue_id") or ""),
            str(data.get("separator", " ")),
        )
    if command_type == "shift":
        return ShiftCues(
            int(data.get("offset_ms", 0)),
            tuple(str(value) for value in (data.get("cue_ids") or ())),
        )
    if command_type == "replace":
        return FindReplace(
            str(data.get("find") or ""), str(data.get("replace") or ""),
            str(data.get("field") or "both"), bool(data.get("case_sensitive", True)),
        )
    raise HTTPException(
        status_code=400,
        detail={"error_code": "SUBTITLE_COMMAND_UNSUPPORTED", "message": command_type},
    )


def _raise_subtitle_http(exc: Exception) -> None:
    code = getattr(exc, "error_code", "SUBTITLE_DOCUMENT_ERROR")
    status = 409 if code == "SUBTITLE_VERSION_CONFLICT" else 400
    if code in {"SUBTITLE_DOCUMENT_NOT_FOUND", "SUBTITLE_REVISION_NOT_FOUND"}:
        status = 404
    raise HTTPException(status_code=status, detail={"error_code": code, "message": str(exc)}) from exc


def _launch_retry_plan(plan) -> None:
    _progress().reset(plan.job_id)
    cancel_token = _cancellation().get(plan.job_id)
    if cancel_token:
        cancel_token.reset()
    else:
        cancel_token = _cancellation().register(plan.job_id)
    try:
        start_pipeline(
            job_id=plan.job_id,
            request=plan.request_payload,
            task_store=_store(),
            progress=_progress(),
            cancel_token=cancel_token,
            model_resources=_model_resources(),
            run_lease_token=plan.lease_token,
        )
    except Exception:
        getattr(_store(), "release_run_lease")(plan.job_id, plan.lease_token)
        raise


def _stage_elapsed_seconds(run: Dict[str, Any]) -> float:
    try:
        started = datetime.fromisoformat(str(run.get("started_at") or "").replace("Z", "+00:00"))
        finished_text = str(run.get("finished_at") or "")
        finished = (
            datetime.fromisoformat(finished_text.replace("Z", "+00:00"))
            if finished_text else datetime.now(timezone.utc)
        )
        return max(0.0, round((finished - started).total_seconds(), 3))
    except (TypeError, ValueError):
        return 0.0

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
