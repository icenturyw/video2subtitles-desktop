from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class TtsVoiceProfile:
    provider: str = "qwen"
    model: str = ""
    voice: Optional[str] = None
    prompt_audio_path: Optional[str] = None
    prompt_audio_hash: Optional[str] = None
    prompt_asset_id: Optional[str] = None
    language: Optional[str] = None
    style: Optional[str] = None
    emotion: Optional[str] = None
    speed: Optional[float] = None
    sample_rate: Optional[int] = None
    seed: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    consistency_mode: str = "stable"  # "fast" | "stable" | "strict"


def voice_profile_hash(profile: TtsVoiceProfile) -> str:
    relevant: Dict[str, object] = {
        "provider": profile.provider,
        "model": profile.model,
        "voice": profile.voice,
        "prompt_audio_hash": profile.prompt_audio_hash,
        "language": profile.language,
        "style": profile.style,
        "speed": profile.speed,
        "seed": profile.seed,
        "temperature": profile.temperature,
        "top_p": profile.top_p,
        "sample_rate": profile.sample_rate,
        "consistency_mode": profile.consistency_mode,
    }
    relevant = {k: v for k, v in relevant.items() if v is not None}
    raw = json.dumps(relevant, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def profile_to_log_dict(profile: TtsVoiceProfile, profile_hash: str) -> Dict[str, object]:
    return {
        "provider": profile.provider,
        "model": profile.model,
        "voice": profile.voice,
        "prompt_audio_hash": profile.prompt_audio_hash,
        "language": profile.language,
        "style": profile.style,
        "speed": profile.speed,
        "seed": profile.seed,
        "temperature": profile.temperature,
        "top_p": profile.top_p,
        "consistency_mode": profile.consistency_mode,
        "voice_profile_hash": profile_hash,
    }
