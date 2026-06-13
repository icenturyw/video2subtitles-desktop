"""Rendering presets for different quality/speed targets."""
from __future__ import annotations

from typing import Any, Dict


def preset_fast() -> Dict[str, Any]:
    return {
        "video_encoder": "libx264",
        "preset": "veryfast",
        "crf": 25,
        "audio_encoder": "aac",
    }


def preset_balanced() -> Dict[str, Any]:
    return {
        "video_encoder": "libx264",
        "preset": "medium",
        "crf": 22,
        "audio_encoder": "aac",
    }


def preset_quality() -> Dict[str, Any]:
    return {
        "video_encoder": "libx264",
        "preset": "slow",
        "crf": 18,
        "audio_encoder": "aac",
    }


_RENDER_PRESETS = {
    "fast": preset_fast,
    "balanced": preset_balanced,
    "quality": preset_quality,
}


def get_preset(name: str = "balanced") -> Dict[str, Any]:
    func = _RENDER_PRESETS.get(name, preset_balanced)
    return func()
