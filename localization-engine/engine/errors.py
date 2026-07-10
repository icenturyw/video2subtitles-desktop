"""Unified, serializable exceptions for localization pipeline failures."""
from __future__ import annotations

from typing import Any, Dict, Optional


class PipelineError(Exception):
    """Base error carrying the stable fields persisted by the task store."""

    default_code = "PIPELINE_ERROR"
    default_stage = "prepare"

    def __init__(
        self,
        message: str,
        *,
        error_code: Optional[str] = None,
        stage: Optional[str] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        self.message = str(message)
        self.error_code = error_code or self.default_code
        self.stage = stage or self.default_stage
        self.cause = cause
        super().__init__(self.message)

    @property
    def code(self) -> str:
        """Compatibility alias used by older provider exceptions."""
        return self.error_code

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_code": self.error_code,
            "stage": self.stage,
            "message": self.message,
        }


class WorkspaceError(PipelineError, ValueError):
    default_code = "WORKSPACE_INVALID"
    default_stage = "prepare"


class InputPipelineError(PipelineError):
    default_code = "SOURCE_INPUT_ERROR"
    default_stage = "normalize"


class TranslationPipelineError(PipelineError):
    default_code = "TRANSLATION_ERROR"
    default_stage = "translate"


class TTSPipelineError(PipelineError):
    default_code = "TTS_GENERATION_FAILED"
    default_stage = "tts"


class RenderPipelineError(PipelineError):
    default_code = "SUBTITLE_RENDER_FAILED"
    default_stage = "render"

