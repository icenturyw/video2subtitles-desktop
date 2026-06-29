from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .planner import DEFAULT_PLANNER_OPTIONS, build_tts_plan


@dataclass
class TtsChunk:
    chunk_index: int
    segment_indexes: List[int]
    text: str
    start_time: float
    end_time: float


# Keep the historical public constant name for tests and callers.  The actual
# normalization and planning logic lives in planner.py.
DEFAULT_CHUNK_OPTIONS: Dict[str, Any] = dict(DEFAULT_PLANNER_OPTIONS)


def build_tts_chunks(
    segments: List[Any],
    options: Optional[Dict[str, Any]] = None,
) -> List[TtsChunk]:
    """Build TTS chunks from a timeline-aware plan.

    Stable/strict TTS uses chunking for timbre consistency, but a chunk must not
    cross a long subtitle gap or an unsafe speed-pressure boundary.  The planner
    first computes gap, tolerance, estimated speech duration, available window,
    and speed pressure for each subtitle; this wrapper returns the lightweight
    TtsChunk objects expected by the existing pipeline.
    """
    plan = build_tts_plan(segments, options)
    return [
        TtsChunk(
            chunk_index=chunk.chunk_index,
            segment_indexes=list(chunk.segment_indexes),
            text=chunk.text,
            start_time=chunk.start_time,
            end_time=chunk.end_time,
        )
        for chunk in plan.chunks
    ]
