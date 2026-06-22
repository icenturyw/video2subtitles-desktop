from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Protocol


@dataclass
class TTSResult:
    output_path: Path
    duration_seconds: float
    cached: bool = False
    mode: str = ""


class TTSProvider(Protocol):
    def synthesize(
        self,
        text: str,
        language: str,
        voice: str,
        output_path: Path,
        options: dict,
    ) -> TTSResult:
        ...

    def list_voices(self, language: Optional[str] = None) -> List[Dict[str, str]]:
        ...


class TTSError(Exception):
    pass


class TTSAuthError(TTSError):
    pass


class TTSUnavailableError(TTSError):
    pass


def _text_hash(text: str, voice: str, lang: str, variant: str = "") -> str:
    raw = f"{lang}|{voice}|{variant}|{text}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


class TTSCache:
    def __init__(self, cache_dir: Path):
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, text: str, voice: str, language: str, variant: str = "") -> Optional[Path]:
        path = self._cache_dir / f"{_text_hash(text, voice, language, variant)}.wav"
        return path if path.exists() else None

    def put(self, text: str, voice: str, language: str, audio_path: Path, variant: str = "") -> Path:
        dst = self._cache_dir / f"{_text_hash(text, voice, language, variant)}.wav"
        if not dst.exists():
            import shutil
            shutil.copy2(str(audio_path), str(dst))
        return dst

    def clear(self):
        for f in self._cache_dir.glob("*.wav"):
            f.unlink(missing_ok=True)
