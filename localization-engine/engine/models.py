"""Pydantic models for the Localization Engine API.

These models define the request/response schemas for the FastAPI endpoints.
They are separate from the dataclass-based job_models.py used by the desktop client.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TranslationRequest(BaseModel):
    """Translation provider configuration in API requests."""
    provider: str = "openai_compatible"
    base_url: str = ""
    model: str = ""
    api_key_env: str = "V2S_TRANSLATION_API_KEY"
    api_type: str = "auto"
    api_key: str = ""
    temperature: float = 0.3
    timeout: int = 60
    max_batch_chars: int = 4000
    max_batch_items: int = 10
    retry_count: int = 3
    concurrency: int = 2
    quality_mode: str = "fast"


class TranslationApiKeyRequest(BaseModel):
    """Runtime translation API key update."""
    api_key: str = ""


class StyleRequest(BaseModel):
    """Subtitle style configuration in API requests."""
    preset: str = "default"
    font_family: str = "Microsoft YaHei"
    font_size: int = 48
    outline: float = 2.0
    shadow: float = 1.0
    margin_v: int = 40


class CreateJobRequest(BaseModel):
    """POST /jobs request body."""
    job_id: str = ""
    workspace_dir: str = ""
    source_video: str = ""
    source_subtitle: str = ""
    source_language: str = "en"
    target_language: str = "zh-CN"
    subtitle_mode: str = "bilingual"
    burn_subtitles: bool = True
    embed_soft_subtitles: bool = False
    dubbing_enabled: bool = False
    tts_provider: str = "edge-tts"
    tts_voice: str = ""
    tts_concurrency: int = 1
    tts_options: Dict[str, Any] = Field(default_factory=dict)
    original_volume: float = 0.0
    low_vram_mode: bool = True
    translation: Optional[TranslationRequest] = None
    translation_preset_id: str = ""
    translation_preset_name: str = ""
    tts_preset_id: str = ""
    tts_preset_name: str = ""
    style: Optional[StyleRequest] = None


class RetryRequest(BaseModel):
    """POST /jobs/{job_id}/retry request body."""
    from_stage: str = "translate"


class ArtifactResponse(BaseModel):
    """A single output artifact."""
    kind: str
    path: str
    language: Optional[str] = None
    size_bytes: int = 0


class TaskResultResponse(BaseModel):
    """GET /jobs/{job_id} response and unified task result."""
    job_id: str
    status: str = "pending"
    stage: str = "prepare"
    progress: int = 0
    message: str = ""
    detected_language: str = ""
    artifacts: List[ArtifactResponse] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_detail: Optional[str] = None


class HealthResponse(BaseModel):
    """GET /health response."""
    status: str = "ok"
    service: str = "video2subtitles-localization-engine"
    version: str = "0.1.0"
    capabilities: Dict[str, Any] = Field(default_factory=lambda: {
        "translation": False,
        "rendering": False,
        "whisperx": False,
        "tts": [],
    })
    ffmpeg: bool = False


class LogResponse(BaseModel):
    """GET /jobs/{job_id}/logs response."""
    job_id: str
    log_path: str = ""
    lines: List[str] = Field(default_factory=list)
    truncated: bool = False
