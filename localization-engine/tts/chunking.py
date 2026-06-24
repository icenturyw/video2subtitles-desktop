from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class TtsChunk:
    chunk_index: int
    segment_indexes: List[int]
    text: str
    start_time: float
    end_time: float


DEFAULT_CHUNK_OPTIONS: Dict[str, Any] = {
    "max_chars": 500,
    "max_duration_sec": 45,
    "prefer_sentence_end": True,
    "min_chars": 40,
    # Keep stable/strict TTS chunks local to a continuous subtitle region.
    # Crossing long silent gaps makes the generated chunk audio continuous, then
    # later silence/character based slicing can put speech for a later subtitle
    # into an earlier segment and make dubbing sound too early.
    "max_gap_sec": 0.8,
    "max_timeline_span_sec": 12.0,
}

_SENTENCE_END_CHARS = {".", "!", "?", "。", "！", "？", "\n", ";"}


def _coerce_int(value: Any, default: int, *, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _coerce_float(
    value: Any,
    default: float,
    *,
    minimum: float = 0.0,
    maximum: Optional[float] = None,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _option(opts: Dict[str, Any], *keys: str, default: Any) -> Any:
    for key in keys:
        if key in opts:
            return opts[key]
    return default


def _safe_to_merge_chunks(prev: TtsChunk, current: TtsChunk,
                          max_gap_sec: float,
                          max_timeline_span_sec: float) -> bool:
    gap = current.start_time - prev.end_time
    if gap > max_gap_sec:
        return False
    if current.end_time - prev.start_time > max_timeline_span_sec:
        return False
    return True


def build_tts_chunks(
    segments: List[Any],
    options: Optional[Dict[str, Any]] = None,
) -> List[TtsChunk]:
    """Merge adjacent short subtitle segments into longer TTS chunks.

    Stable/strict TTS uses chunking for timbre consistency, but a chunk must not
    cross a long subtitle gap. TTS providers synthesize chunk text as continuous
    speech, so combining subtitles separated by long silence can make later
    speech appear inside an earlier segment after the chunk is sliced back.

    Args:
        segments: List of objects with .text, .translation, .start, .end, .index.
        options: Override keys for max_chars, max_duration_sec, min_chars,
                 prefer_sentence_end, max_gap_sec, and max_timeline_span_sec.
                 Also accepts tts_chunk_* aliases from request options.

    Returns:
        List of TtsChunk with merged text and segment index mapping.
    """
    opts = dict(DEFAULT_CHUNK_OPTIONS)
    if options:
        opts.update(options)

    max_chars = _coerce_int(
        _option(opts, "tts_chunk_max_chars", "max_chars", default=opts["max_chars"]),
        DEFAULT_CHUNK_OPTIONS["max_chars"],
    )
    max_duration_sec = _coerce_float(
        _option(opts, "tts_chunk_max_duration_sec", "max_duration_sec", default=opts["max_duration_sec"]),
        DEFAULT_CHUNK_OPTIONS["max_duration_sec"],
        minimum=0.1,
    )
    min_chars = _coerce_int(
        _option(opts, "tts_chunk_min_chars", "min_chars", default=opts["min_chars"]),
        DEFAULT_CHUNK_OPTIONS["min_chars"],
        minimum=1,
    )
    prefer_sentence_end = _coerce_bool(
        _option(opts, "tts_chunk_prefer_sentence_end", "prefer_sentence_end", default=opts["prefer_sentence_end"]),
        DEFAULT_CHUNK_OPTIONS["prefer_sentence_end"],
    )
    max_gap_sec = _coerce_float(
        _option(opts, "tts_chunk_max_gap_sec", "max_gap_sec", default=opts["max_gap_sec"]),
        DEFAULT_CHUNK_OPTIONS["max_gap_sec"],
        minimum=0.1,
        maximum=5.0,
    )
    max_timeline_span_sec = _coerce_float(
        _option(
            opts,
            "tts_chunk_max_timeline_span_sec",
            "max_timeline_span_sec",
            default=opts["max_timeline_span_sec"],
        ),
        DEFAULT_CHUNK_OPTIONS["max_timeline_span_sec"],
        minimum=2.0,
        maximum=60.0,
    )

    candidates = [
        seg for seg in segments
        if (seg.translation or seg.text or "").strip()
    ]
    candidates.sort(key=lambda seg: (seg.start, seg.index))

    chunks: List[TtsChunk] = []
    current_segments: List[Any] = []
    current_text_parts: List[str] = []
    current_chars = 0
    current_start = 0.0
    current_end = 0.0

    def _flush() -> None:
        nonlocal current_segments, current_text_parts, current_chars
        nonlocal current_start, current_end
        if not current_segments:
            return
        text = " ".join(part for part in current_text_parts if part.strip())
        if text.strip():
            chunks.append(TtsChunk(
                chunk_index=len(chunks),
                segment_indexes=[s.index for s in current_segments],
                text=text.strip(),
                start_time=current_start,
                end_time=current_end,
            ))
        current_segments = []
        current_text_parts = []
        current_chars = 0
        current_start = 0.0
        current_end = 0.0

    for seg in candidates:
        text = (seg.translation or seg.text or "").strip()
        if not text:
            continue
        seg_chars = len(text)
        seg_duration = max(0.0, float(seg.end) - float(seg.start))

        if current_segments:
            gap = float(seg.start) - float(current_end)
            timeline_span = float(seg.end) - float(current_start)
            chunk_duration_sum = float(current_end) - float(current_start) + seg_duration
            if (
                current_chars + seg_chars > max_chars
                or chunk_duration_sum > max_duration_sec
                or gap > max_gap_sec
                or timeline_span > max_timeline_span_sec
            ):
                _flush()

        if not current_segments:
            current_start = float(seg.start)

        current_segments.append(seg)
        current_text_parts.append(text)
        current_chars += seg_chars
        current_end = float(seg.end)

        if prefer_sentence_end and text and text[-1] in _SENTENCE_END_CHARS:
            if current_chars >= min_chars:
                _flush()

    _flush()

    if not chunks:
        return chunks

    final: List[TtsChunk] = []
    for c in chunks:
        if len(c.text) < min_chars and final and _safe_to_merge_chunks(
            final[-1], c, max_gap_sec, max_timeline_span_sec,
        ):
            prev = final[-1]
            final[-1] = TtsChunk(
                chunk_index=prev.chunk_index,
                segment_indexes=prev.segment_indexes + c.segment_indexes,
                text=(prev.text + " " + c.text).strip(),
                start_time=prev.start_time,
                end_time=c.end_time,
            )
        else:
            final.append(c)

    for i, c in enumerate(final):
        c.chunk_index = i

    return final
