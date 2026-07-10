"""Canonical registry for the localization pipeline's real execution stages."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class StageDefinition:
    name: str
    required_artifact_kinds: Tuple[str, ...] = ()
    produced_artifact_kinds: Tuple[str, ...] = ()
    config_keys: Tuple[str, ...] = ()


STAGES: Tuple[StageDefinition, ...] = (
    StageDefinition("prepare", config_keys=("workspace_dir", "source_video", "source_subtitle")),
    StageDefinition("normalize", config_keys=("source_language",)),
    StageDefinition(
        "translate",
        produced_artifact_kinds=("translation_quality_report", "translation_error_report"),
        config_keys=("source_language", "target_language", "translation", "translation_preset_id"),
    ),
    StageDefinition(
        "subtitle_export",
        produced_artifact_kinds=(
            "source_srt", "translated_srt", "bilingual_srt",
            "source_ass", "translated_ass", "bilingual_ass",
        ),
        config_keys=("subtitle_mode", "style", "target_language"),
    ),
    StageDefinition(
        "tts",
        required_artifact_kinds=("translated_srt",),
        produced_artifact_kinds=("tts_report", "tts_timeline_report"),
        config_keys=("tts_provider", "tts_voice", "tts_options", "tts_concurrency"),
    ),
    StageDefinition(
        "audio_mix",
        produced_artifact_kinds=("dubbed_video", "audio_mix_report"),
        config_keys=("original_volume",),
    ),
    StageDefinition(
        "render",
        required_artifact_kinds=("source_ass",),
        produced_artifact_kinds=("burned_video", "softsub_video"),
        config_keys=("burn_subtitles", "embed_soft_subtitles", "style"),
    ),
    StageDefinition("finalize"),
)

STAGE_NAMES: Tuple[str, ...] = tuple(stage.name for stage in STAGES)
STAGE_BY_NAME: Dict[str, StageDefinition] = {stage.name: stage for stage in STAGES}


def stage_index(stage: str) -> int:
    try:
        return STAGE_NAMES.index(stage)
    except ValueError as exc:
        raise ValueError(f"Unknown pipeline stage: {stage}") from exc


def stages_from(stage: str, *, include_self: bool = True) -> Tuple[str, ...]:
    index = stage_index(stage) + (0 if include_self else 1)
    return STAGE_NAMES[index:]


def stages_before(stage: str) -> Tuple[str, ...]:
    return STAGE_NAMES[:stage_index(stage)]
