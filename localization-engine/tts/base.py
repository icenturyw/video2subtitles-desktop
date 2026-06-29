from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Protocol

logger = logging.getLogger("tts.cache")


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


def _evict_old_files(cache_dir: Path, max_size_mb: int, max_age_days: int):
    """Remove oldest files when cache exceeds limits."""
    try:
        now = time.time()
        max_age_sec = max_age_days * 86400
        files = sorted(cache_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        if not files:
            return

        oldest_allowed = now - max_age_sec
        stale = [f for f in files if f.stat().st_mtime < oldest_allowed]
        for f in stale:
            f.unlink(missing_ok=True)
        if stale:
            logger.info("Evicted %d stale cache files (age > %dd)", len(stale), max_age_days)

        total_mb = sum(f.stat().st_size for f in cache_dir.glob("*.wav")) / (1024 * 1024)
        if total_mb > max_size_mb:
            remaining = sorted(cache_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
            to_remove = int(len(remaining) * 0.2)
            for f in remaining[:to_remove]:
                f.unlink(missing_ok=True)
            logger.info("Evicted %d cache files (size %.0fMB > %dMB)", to_remove, total_mb, max_size_mb)
    except Exception:
        pass


class TTSCache:
    def __init__(self, cache_dir: Path, max_size_mb: int = 500, max_age_days: int = 7):
        self._cache_dir = cache_dir
        self._max_size_mb = max_size_mb
        self._max_age_days = max_age_days
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        _evict_old_files(cache_dir, max_size_mb, max_age_days)

    def get(self, text: str, voice: str, language: str, variant: str = "") -> Optional[Path]:
        path = self._cache_dir / f"{_text_hash(text, voice, language, variant)}.wav"
        return path if path.exists() else None

    def put(self, text: str, voice: str, language: str, audio_path: Path, variant: str = "") -> Path:
        dst = self._cache_dir / f"{_text_hash(text, voice, language, variant)}.wav"
        if not dst.exists():
            import shutil
            shutil.copy2(str(audio_path), str(dst))
        _evict_old_files(self._cache_dir, self._max_size_mb, self._max_age_days)
        return dst

    def clear(self):
        for f in self._cache_dir.glob("*.wav"):
            f.unlink(missing_ok=True)
