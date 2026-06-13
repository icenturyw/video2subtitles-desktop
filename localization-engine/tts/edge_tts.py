from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from tts.base import TTSResult, TTSUnavailableError


_EDGE_TTS_AVAILABLE = False
try:
    import edge_tts
    _EDGE_TTS_AVAILABLE = True
except ImportError:
    pass


_EDGE_TTS_CACHE: Dict[str, str] = {}


def _get_voices_sync(language: Optional[str] = None) -> List[Dict[str, str]]:
    """Synchronously fetch available Edge-TTS voices."""
    if not _EDGE_TTS_AVAILABLE:
        return []
    try:
        voices = asyncio.run(edge_tts.list_voices())
        result = []
        for v in voices:
            if language and not v["Locale"].lower().startswith(language.lower()):
                continue
            result.append({
                "name": v["ShortName"],
                "locale": v["Locale"],
                "gender": v.get("Gender", ""),
            })
        return result
    except Exception:
        return []


def _synthesize_sync(
    text: str,
    voice: str,
    output_path: Path,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
) -> TTSResult:
    """Synchronously synthesize speech using edge-tts."""
    if not _EDGE_TTS_AVAILABLE:
        raise TTSUnavailableError("edge-tts 未安装，请执行 pip install edge-tts")

    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume)
        asyncio.run(communicate.save(str(output_path)))
    except Exception as e:
        raise TTSUnavailableError(f"Edge-TTS 合成失败: {e}") from e

    duration = _get_audio_duration(output_path)
    return TTSResult(output_path=output_path, duration_seconds=duration)


def _get_audio_duration(path: Path) -> float:
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0


def get_provider() -> object:
    """Return a dict-based provider adapter for the pipeline."""
    return {
        "name": "edge-tts",
        "available": _EDGE_TTS_AVAILABLE,
        "list_voices": _get_voices_sync,
        "synthesize": _synthesize_sync,
    }
