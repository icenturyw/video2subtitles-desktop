from __future__ import annotations

import hashlib
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Protocol

logger = logging.getLogger("tts.cache")


@dataclass
class TTSResult:
    output_path: Path
    duration_seconds: float
    cached: bool = False
    mode: str = ""


@dataclass(frozen=True)
class TTSCapabilities:
    voice_list: bool = True
    speed: bool = False
    pitch: bool = False
    emotion: bool = False
    language: bool = True
    streaming: bool = False
    preview_character_limit: int = 300
    supported_output_formats: tuple[str, ...] = ("wav",)
    supported_parameters: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["supported_output_formats"] = list(self.supported_output_formats)
        data["supported_parameters"] = list(self.supported_parameters)
        return data


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

    def capabilities(self) -> TTSCapabilities:
        ...


class BaseTTSProvider(ABC):
    """Stable base contract for built-in and third-party TTS providers."""

    supports_concurrency = True
    tts_capabilities = TTSCapabilities()

    @abstractmethod
    def synthesize(
        self,
        text: str,
        language: str,
        voice: str,
        output_path: Path,
        options: dict,
    ) -> TTSResult:
        """Synthesize one text item into ``output_path``."""
        raise NotImplementedError

    @abstractmethod
    def list_voices(self, language: Optional[str] = None) -> List[Dict[str, str]]:
        """Return provider voice metadata, optionally filtered by language."""
        raise NotImplementedError

    def close(self) -> None:
        """Release optional provider resources; default providers are stateless."""

    def capabilities(self) -> TTSCapabilities:
        return self.tts_capabilities



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
