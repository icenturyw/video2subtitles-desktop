from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskProgress:
    task_id: str
    status: TaskStatus
    progress: float = 0.0
    message: str = ""
    result: Optional[dict] = None
    error: Optional[str] = None


@dataclass
class Capabilities:
    custom_voice: bool = False
    voice_design: bool = False
    voice_clone: bool = False
    batch: bool = True


@dataclass
class HealthResponse:
    status: str = "ok"
    service: str = "video2subtitles-qwen3-tts"
    version: str = "0.1.0"
    device: str = "cpu"
    dtype: str = "float32"
    loaded_model: Optional[str] = None
    flash_attention: bool = False
    capabilities: Capabilities = field(default_factory=Capabilities)


@dataclass
class VoiceInfo:
    name: str
    gender: str = ""
    language: str = ""


@dataclass
class InstallModelRequest:
    model_id: str
    source: str = "huggingface"
    cache_dir: Optional[str] = None


@dataclass
class LoadModelRequest:
    model_id: str
    device: Optional[str] = None
    dtype: Optional[str] = None
    attn_implementation: Optional[str] = None
    cache_dir: Optional[str] = None


@dataclass
class SynthesizeCustomVoiceRequest:
    text: str
    speaker: str
    language: Optional[str] = None
    instruct: Optional[str] = None
    max_new_tokens: Optional[int] = None
    top_p: Optional[float] = None
    temperature: Optional[float] = None
    seed: Optional[int] = None


@dataclass
class SynthesizeVoiceCloneRequest:
    text: str
    language: Optional[str] = None
    ref_audio: Optional[str] = None
    ref_text: Optional[str] = None
    x_vector_only_mode: bool = False
    voice_clone_prompt_id: Optional[str] = None
    max_new_tokens: Optional[int] = None
    top_p: Optional[float] = None
    temperature: Optional[float] = None
    seed: Optional[int] = None


@dataclass
class SynthesizeVoiceDesignRequest:
    text: str
    instruct: str
    language: Optional[str] = None
    max_new_tokens: Optional[int] = None
    top_p: Optional[float] = None
    temperature: Optional[float] = None
    seed: Optional[int] = None


@dataclass
class VoiceClonePromptRequest:
    ref_audio: str
    ref_text: Optional[str] = None
    x_vector_only_mode: bool = False


@dataclass
class VoiceClonePromptResponse:
    prompt_id: str
    message: str = ""
