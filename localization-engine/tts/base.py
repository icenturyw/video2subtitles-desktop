from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Protocol


@dataclass
class TTSResult:
    output_path: Path
    duration_seconds: float


class TTSProvider(Protocol):
    def list_voices(self, language: Optional[str] = None) -> List[Dict[str, str]]:
        ...

    def synthesize(
        self,
        text: str,
        voice: str,
        output_path: Path,
        *,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%",
    ) -> TTSResult:
        ...


class TTSError(Exception):
    pass


class TTSAuthError(TTSError):
    pass


class TTSUnavailableError(TTSError):
    pass
