"""Translation provider interface and registry."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from job_models import SubtitleSegment, TranslationConfig


class TranslationError(Exception):
    """Base exception for translation failures."""

    def __init__(self, message: str, code: str = "TRANSLATION_ERROR",
                 recoverable: bool = False):
        super().__init__(message)
        self.code = code
        self.recoverable = recoverable


class AuthError(TranslationError):
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, code="TRANSLATION_AUTH_FAILED", recoverable=False)


class RateLimitError(TranslationError):
    def __init__(self, message: str = "Rate limited", retry_after: int = 60):
        super().__init__(message, code="TRANSLATION_RATE_LIMITED", recoverable=True)
        self.retry_after = retry_after


class TimeoutError_(TranslationError):
    def __init__(self, message: str = "Request timed out"):
        super().__init__(message, code="TRANSLATION_TIMEOUT", recoverable=True)


class InvalidResponseError(TranslationError):
    def __init__(self, message: str = "Invalid response"):
        super().__init__(message, code="TRANSLATION_INVALID_RESPONSE", recoverable=True)


class TranslationProvider(ABC):
    """Base class for translation providers."""

    @abstractmethod
    def translate_batch(self, segments: List[Dict], config: TranslationConfig,
                        source_lang: str, target_lang: str,
                        glossary: Optional[str] = None) -> List[Dict]:
        """Translate a batch of subtitle segments.

        Args:
            segments: List of dicts with "id" (int) and "text" (str).
            config: Translation provider configuration.
            source_lang: Source language code (e.g. "en").
            target_lang: Target language code (e.g. "zh-CN").
            glossary: Optional glossary text to inject into the prompt.

        Returns:
            List of dicts with "id" (int) and "text" (str) for each translation.

        Raises:
            TranslationError: On failures.
        """
        ...

    @classmethod
    def provider_name(cls) -> str:
        return "base"
