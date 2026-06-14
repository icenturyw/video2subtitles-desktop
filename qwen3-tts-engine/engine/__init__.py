from __future__ import annotations

from engine.schemas import (
    Capabilities,
    HealthResponse,
    InstallModelRequest,
    LoadModelRequest,
    SynthesizeCustomVoiceRequest,
    SynthesizeVoiceCloneRequest,
    SynthesizeVoiceDesignRequest,
    TaskStatus,
    TaskProgress,
    VoiceInfo,
    VoiceClonePromptRequest,
    VoiceClonePromptResponse,
)
from engine.device import detect_device, cuda_available, get_gpu_info
from engine.cache import TTSCache
from engine.model_manager import ModelManager
from engine.synthesis import Synthesizer
