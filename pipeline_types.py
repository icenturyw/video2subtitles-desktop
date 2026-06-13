"""Pipeline stage and type definitions for the localization engine."""
from __future__ import annotations

from enum import Enum


class PipelineStage(str, Enum):
    """Stages in the localization pipeline."""

    PREPARE = "prepare"
    DOWNLOAD = "download"
    TRANSCRIBE = "transcribe"
    SEGMENT = "segment"
    TRANSLATE = "translate"
    SUBTITLE_EXPORT = "subtitle_export"
    TTS = "tts"
    AUDIO_MIX = "audio_mix"
    RENDER = "render"
    FINALIZE = "finalize"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"

    def ui_text(self) -> str:
        """Return Chinese display text for this stage."""
        return _STAGE_UI_TEXT.get(self, self.value)

    def is_terminal(self) -> bool:
        """Return True if this stage represents a terminal state."""
        return self in (PipelineStage.COMPLETED, PipelineStage.ERROR, PipelineStage.CANCELLED)


_STAGE_UI_TEXT = {
    PipelineStage.PREPARE: "准备",
    PipelineStage.DOWNLOAD: "下载",
    PipelineStage.TRANSCRIBE: "语音识别",
    PipelineStage.SEGMENT: "字幕切分",
    PipelineStage.TRANSLATE: "翻译",
    PipelineStage.SUBTITLE_EXPORT: "字幕生成",
    PipelineStage.TTS: "语音合成",
    PipelineStage.AUDIO_MIX: "音频混合",
    PipelineStage.RENDER: "视频渲染",
    PipelineStage.FINALIZE: "整理产物",
    PipelineStage.COMPLETED: "完成",
    PipelineStage.ERROR: "失败",
    PipelineStage.CANCELLED: "已取消",
}


class ProcessingMode(str, Enum):
    """Desktop processing modes."""

    SUBTITLE = "subtitle"       # Quick subtitle (existing fast mode)
    TRANSLATE = "translate"     # Translate + burn subtitles
    DUB = "dub"                 # Full dubbing with TTS


class SubtitleMode(str, Enum):
    """Which subtitle variants to export."""

    SOURCE = "source"
    TRANSLATED = "translated"
    BILINGUAL = "bilingual"


# Progress weight ranges for each stage (start_pct, end_pct).
# These map pipeline stages to overall progress percentage.

TRANSLATE_PROGRESS_WEIGHTS = {
    PipelineStage.PREPARE: (0, 5),
    PipelineStage.TRANSCRIBE: (5, 35),
    PipelineStage.SEGMENT: (35, 43),
    PipelineStage.TRANSLATE: (43, 72),
    PipelineStage.SUBTITLE_EXPORT: (72, 80),
    PipelineStage.RENDER: (80, 97),
    PipelineStage.FINALIZE: (97, 100),
}

DUB_PROGRESS_WEIGHTS = {
    PipelineStage.PREPARE: (0, 4),
    PipelineStage.TRANSCRIBE: (4, 25),
    PipelineStage.SEGMENT: (25, 31),
    PipelineStage.TRANSLATE: (31, 52),
    PipelineStage.SUBTITLE_EXPORT: (52, 58),
    PipelineStage.TTS: (58, 78),
    PipelineStage.AUDIO_MIX: (78, 87),
    PipelineStage.RENDER: (87, 97),
    PipelineStage.FINALIZE: (97, 100),
}


def stage_progress(stage: PipelineStage, stage_internal_pct: float,
                   mode: ProcessingMode = ProcessingMode.TRANSLATE) -> int:
    """Calculate overall progress percentage from stage and stage-internal progress.

    Args:
        stage: Current pipeline stage.
        stage_internal_pct: Progress within the current stage (0-100).
        mode: Processing mode to select weight table.

    Returns:
        Overall progress percentage (0-100).
    """
    if stage.is_terminal():
        if stage == PipelineStage.COMPLETED:
            return 100
        return 0  # error/cancelled: don't fake progress

    weights = TRANSLATE_PROGRESS_WEIGHTS if mode != ProcessingMode.DUB else DUB_PROGRESS_WEIGHTS
    start, end = weights.get(stage, (0, 0))
    clamped = max(0.0, min(100.0, stage_internal_pct))
    return int(start + (end - start) * clamped / 100)


# Ordered stage sequences for validation
TRANSLATE_STAGE_ORDER = [
    PipelineStage.PREPARE,
    PipelineStage.TRANSCRIBE,
    PipelineStage.SEGMENT,
    PipelineStage.TRANSLATE,
    PipelineStage.SUBTITLE_EXPORT,
    PipelineStage.RENDER,
    PipelineStage.FINALIZE,
]

DUB_STAGE_ORDER = [
    PipelineStage.PREPARE,
    PipelineStage.TRANSCRIBE,
    PipelineStage.SEGMENT,
    PipelineStage.TRANSLATE,
    PipelineStage.SUBTITLE_EXPORT,
    PipelineStage.TTS,
    PipelineStage.AUDIO_MIX,
    PipelineStage.RENDER,
    PipelineStage.FINALIZE,
]
