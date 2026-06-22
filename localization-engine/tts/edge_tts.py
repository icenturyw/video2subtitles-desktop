from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from process_utils import hidden_subprocess_kwargs
from tts.base import (
    TTSResult, TTSUnavailableError, TTSCache,
)

logger = logging.getLogger("tts.edge_tts")

_EDGE_TTS_AVAILABLE = False
try:
    import edge_tts
    _EDGE_TTS_AVAILABLE = True
except ImportError:
    pass


class EdgeTTSProvider:
    def __init__(self, cache: Optional[TTSCache] = None):
        self._cache = cache
        self._voice_cache: Optional[List[Dict[str, str]]] = None

    def list_voices(self, language: Optional[str] = None) -> List[Dict[str, str]]:
        if not _EDGE_TTS_AVAILABLE:
            return []
        if self._voice_cache is None:
            try:
                voices = asyncio.run(edge_tts.list_voices())
                self._voice_cache = [
                    {"name": v["ShortName"], "locale": v["Locale"],
                     "gender": v.get("Gender", "")}
                    for v in voices
                ]
            except Exception as e:
                logger.warning("Failed to list Edge-TTS voices: %s", e)
                return []
        if language:
            return [v for v in self._voice_cache
                    if v["locale"].lower().startswith(language.lower())]
        return list(self._voice_cache)

    def synthesize(
        self,
        text: str,
        language: str,
        voice: str,
        output_path: Path,
        options: dict,
    ) -> TTSResult:
        if not _EDGE_TTS_AVAILABLE:
            raise TTSUnavailableError("edge-tts 未安装，请执行 pip install edge-tts")

        # Check cache
        if self._cache:
            cached = self._cache.get(text, voice, language)
            if cached:
                dur = _get_audio_duration(cached)
                if dur > 0:
                    import shutil
                    shutil.copy2(str(cached), str(output_path))
                    return TTSResult(output_path=output_path, duration_seconds=dur)

        rate = options.get("rate", "+0%")
        pitch = options.get("pitch", "+0Hz")
        volume = options.get("volume", "+0%")
        timeout = options.get("timeout", 60)

        try:
            communicate = edge_tts.Communicate(
                text, voice, rate=rate, pitch=pitch, volume=volume,
            )
            asyncio.run(communicate.save(str(output_path)))
        except Exception as e:
            raise TTSUnavailableError(f"Edge-TTS 合成失败: {e}") from e

        duration = _get_audio_duration(output_path)

        # Store in cache
        if self._cache and duration > 0:
            self._cache.put(text, voice, language, output_path)

        return TTSResult(output_path=output_path, duration_seconds=duration)


def _get_audio_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
            **hidden_subprocess_kwargs(),
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0
