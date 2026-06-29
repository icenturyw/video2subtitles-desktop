from __future__ import annotations

"""TTS timeline planning helpers.

This module turns subtitle segments into a small, inspectable TTS plan before
synthesis starts.  It is inspired by VideoLingo's dubbing pipeline: compute the
available speech window, nearby gaps that may be borrowed, estimated speech
length, and speed pressure first; then build chunks from that plan instead of
merging only by character count.

The generated plan intentionally keeps the original subtitle timeline as the
source of truth.  It does not rewrite subtitle times; it only decides where TTS
chunks should be split and records why.
"""

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_PLANNER_OPTIONS: Dict[str, Any] = {
    "max_chars": 500,
    "max_duration_sec": 45.0,
    "prefer_sentence_end": True,
    "min_chars": 40,
    # Do not synthesize one continuous TTS chunk across a larger subtitle gap.
    "max_gap_sec": 0.8,
    # Cap the whole source timeline covered by one chunk.  This prevents one
    # chunk from spanning a long visual pause even when individual gaps are not
    # huge.
    "max_timeline_span_sec": 12.0,
    # Nearby silence that can be safely treated as extra speech budget for a
    # segment.  Large gaps still split chunks; this only affects diagnostics.
    "max_tolerance_sec": 0.8,
    # Conservative speech-rate estimates used before real TTS exists.
    "cjk_chars_per_sec": 4.2,
    "latin_words_per_sec": 2.6,
    "min_estimated_duration_sec": 0.25,
    # Pressure bands.  These are diagnostics plus a guardrail for chunking.
    "soft_speed_factor": 1.15,
    "max_speed_factor": 1.5,
    "max_chunk_speed_factor": 1.35,
    # When chunk TTS has no reliable silence boundary between subtitle lines,
    # proportional slicing can put words under the wrong subtitle. Keep it off
    # by default; callers may opt in for speed/timbre experiments.
    "allow_proportional_chunk_split": False,
}

_SENTENCE_END_CHARS = {".", "!", "?", "。", "！", "？", "\n", ";", "；"}
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?")


@dataclass
class TtsSegmentPlan:
    index: int
    text: str
    start: float
    end: float
    duration: float
    next_start: Optional[float]
    gap_to_next: Optional[float]
    tolerance: float
    available_duration: float
    estimated_duration: float
    speed_factor: float
    speed_pressure: str
    split_after: bool = False
    split_after_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TtsChunkPlan:
    chunk_index: int
    segment_indexes: List[int]
    text: str
    start_time: float
    end_time: float
    timeline_span: float
    speech_duration: float
    internal_gap_duration: float
    available_duration: float
    estimated_duration: float
    speed_factor: float
    speed_pressure: str
    keep_gaps: bool
    split_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TtsPlan:
    segments: List[TtsSegmentPlan] = field(default_factory=list)
    chunks: List[TtsChunkPlan] = field(default_factory=list)
    options: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "options": self.options,
            "segments": [item.to_dict() for item in self.segments],
            "chunks": [item.to_dict() for item in self.chunks],
        }


def _coerce_int(value: Any, default: int, *, minimum: int = 1, maximum: Optional[int] = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


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
        parsed = default
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


def normalize_planner_options(options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    opts = dict(DEFAULT_PLANNER_OPTIONS)
    if options:
        opts.update(options)

    normalized = {
        "max_chars": _coerce_int(
            _option(opts, "tts_chunk_max_chars", "max_chars", default=opts["max_chars"]),
            int(DEFAULT_PLANNER_OPTIONS["max_chars"]), minimum=20, maximum=5000,
        ),
        "max_duration_sec": _coerce_float(
            _option(opts, "tts_chunk_max_duration_sec", "max_duration_sec", default=opts["max_duration_sec"]),
            float(DEFAULT_PLANNER_OPTIONS["max_duration_sec"]), minimum=1.0, maximum=120.0,
        ),
        "min_chars": _coerce_int(
            _option(opts, "tts_chunk_min_chars", "min_chars", default=opts["min_chars"]),
            int(DEFAULT_PLANNER_OPTIONS["min_chars"]), minimum=1, maximum=1000,
        ),
        "prefer_sentence_end": _coerce_bool(
            _option(opts, "tts_chunk_prefer_sentence_end", "prefer_sentence_end", default=opts["prefer_sentence_end"]),
            bool(DEFAULT_PLANNER_OPTIONS["prefer_sentence_end"]),
        ),
        "max_gap_sec": _coerce_float(
            _option(opts, "tts_chunk_max_gap_sec", "max_gap_sec", default=opts["max_gap_sec"]),
            float(DEFAULT_PLANNER_OPTIONS["max_gap_sec"]), minimum=0.1, maximum=5.0,
        ),
        "max_timeline_span_sec": _coerce_float(
            _option(
                opts,
                "tts_chunk_max_timeline_span_sec",
                "max_timeline_span_sec",
                default=opts["max_timeline_span_sec"],
            ),
            float(DEFAULT_PLANNER_OPTIONS["max_timeline_span_sec"]), minimum=2.0, maximum=60.0,
        ),
        "max_tolerance_sec": _coerce_float(
            _option(
                opts,
                "tts_chunk_max_tolerance_sec",
                "max_tolerance_sec",
                default=opts["max_tolerance_sec"],
            ),
            float(DEFAULT_PLANNER_OPTIONS["max_tolerance_sec"]), minimum=0.0, maximum=5.0,
        ),
        "cjk_chars_per_sec": _coerce_float(
            _option(opts, "tts_cjk_chars_per_sec", "cjk_chars_per_sec", default=opts["cjk_chars_per_sec"]),
            float(DEFAULT_PLANNER_OPTIONS["cjk_chars_per_sec"]), minimum=1.0, maximum=12.0,
        ),
        "latin_words_per_sec": _coerce_float(
            _option(opts, "tts_latin_words_per_sec", "latin_words_per_sec", default=opts["latin_words_per_sec"]),
            float(DEFAULT_PLANNER_OPTIONS["latin_words_per_sec"]), minimum=0.8, maximum=8.0,
        ),
        "min_estimated_duration_sec": _coerce_float(
            _option(
                opts,
                "tts_min_estimated_duration_sec",
                "min_estimated_duration_sec",
                default=opts["min_estimated_duration_sec"],
            ),
            float(DEFAULT_PLANNER_OPTIONS["min_estimated_duration_sec"]), minimum=0.05, maximum=3.0,
        ),
        "soft_speed_factor": _coerce_float(
            _option(opts, "tts_soft_speed_factor", "soft_speed_factor", default=opts["soft_speed_factor"]),
            float(DEFAULT_PLANNER_OPTIONS["soft_speed_factor"]), minimum=1.0, maximum=2.5,
        ),
        "max_speed_factor": _coerce_float(
            _option(opts, "tts_max_speed_factor", "max_speed_factor", default=opts["max_speed_factor"]),
            float(DEFAULT_PLANNER_OPTIONS["max_speed_factor"]), minimum=1.0, maximum=3.0,
        ),
        "max_chunk_speed_factor": _coerce_float(
            _option(
                opts,
                "tts_max_chunk_speed_factor",
                "max_chunk_speed_factor",
                default=opts["max_chunk_speed_factor"],
            ),
            float(DEFAULT_PLANNER_OPTIONS["max_chunk_speed_factor"]), minimum=1.0, maximum=3.0,
        ),
        "allow_proportional_chunk_split": _coerce_bool(
            _option(
                opts,
                "tts_chunk_allow_proportional_split",
                "allow_proportional_chunk_split",
                default=opts["allow_proportional_chunk_split"],
            ),
            bool(DEFAULT_PLANNER_OPTIONS["allow_proportional_chunk_split"]),
        ),
    }
    if normalized["max_chunk_speed_factor"] > normalized["max_speed_factor"]:
        normalized["max_chunk_speed_factor"] = normalized["max_speed_factor"]
    if normalized["soft_speed_factor"] > normalized["max_speed_factor"]:
        normalized["soft_speed_factor"] = normalized["max_speed_factor"]
    return normalized


def _segment_text(seg: Any) -> str:
    return str(getattr(seg, "translation", "") or getattr(seg, "text", "") or "").strip()


def estimate_speech_duration(text: str, options: Optional[Dict[str, Any]] = None) -> float:
    """Estimate spoken duration for text before running a real TTS provider."""
    opts = normalize_planner_options(options)
    stripped = (text or "").strip()
    if not stripped:
        return 0.0

    cjk_count = len(_CJK_RE.findall(stripped))
    latin_words = len(_LATIN_WORD_RE.findall(stripped))
    # Non-whitespace punctuation tends to create pauses, but should not dominate.
    punctuation_count = sum(1 for ch in stripped if not ch.isspace() and not ch.isalnum() and not _CJK_RE.match(ch))
    # Characters that are neither CJK nor Latin words still need a small budget.
    residual_chars = max(0, len(stripped.replace(" ", "")) - cjk_count - punctuation_count)

    cjk_duration = cjk_count / float(opts["cjk_chars_per_sec"])
    latin_duration = latin_words / float(opts["latin_words_per_sec"])
    residual_duration = residual_chars * 0.04
    punctuation_pause = punctuation_count * 0.06
    estimated = cjk_duration + latin_duration + residual_duration + punctuation_pause
    return max(float(opts["min_estimated_duration_sec"]), estimated)


def _pressure_for(speed_factor: float, opts: Dict[str, Any]) -> str:
    if speed_factor <= 0.65:
        return "too_slow"
    if speed_factor <= 1.0:
        return "normal"
    if speed_factor <= float(opts["soft_speed_factor"]):
        return "mild_fast"
    if speed_factor <= float(opts["max_speed_factor"]):
        return "fast"
    return "too_fast"


def _merge_pressure(pressures: Iterable[str], speed_factor: float, opts: Dict[str, Any]) -> str:
    rank = {"normal": 0, "too_slow": 1, "mild_fast": 2, "fast": 3, "too_fast": 4}
    speed_pressure = _pressure_for(speed_factor, opts)
    values = list(pressures) + [speed_pressure]
    return max(values, key=lambda item: rank.get(item, 0)) if values else speed_pressure


def _chunk_from_segments(
    chunk_index: int,
    current: List[TtsSegmentPlan],
    *,
    split_reason: str,
    opts: Dict[str, Any],
) -> TtsChunkPlan:
    start = float(current[0].start)
    end = float(current[-1].end)
    timeline_span = max(0.0, end - start)
    speech_duration = sum(max(0.0, item.duration) for item in current)
    internal_gap_duration = max(0.0, timeline_span - speech_duration)
    available_duration = sum(max(0.0, item.available_duration) for item in current)
    estimated_duration = sum(max(0.0, item.estimated_duration) for item in current)
    speed_factor = estimated_duration / max(0.001, available_duration)
    pressure = _merge_pressure((item.speed_pressure for item in current), speed_factor, opts)
    # This is diagnostic: if true, the estimated chunk speech can probably keep
    # original subtitle gaps without heavy compression.
    keep_gaps = estimated_duration <= max(0.001, speech_duration) * float(opts["soft_speed_factor"])
    return TtsChunkPlan(
        chunk_index=chunk_index,
        segment_indexes=[item.index for item in current],
        text=" ".join(item.text for item in current if item.text).strip(),
        start_time=start,
        end_time=end,
        timeline_span=timeline_span,
        speech_duration=speech_duration,
        internal_gap_duration=internal_gap_duration,
        available_duration=available_duration,
        estimated_duration=estimated_duration,
        speed_factor=speed_factor,
        speed_pressure=pressure,
        keep_gaps=keep_gaps,
        split_reason=split_reason,
    )


def _candidate_split_reason(
    current: List[TtsSegmentPlan],
    seg: TtsSegmentPlan,
    *,
    opts: Dict[str, Any],
) -> str:
    if not current:
        return ""

    prev = current[-1]
    gap = max(0.0, float(seg.start) - float(prev.end))
    if gap > float(opts["max_gap_sec"]):
        return f"gap>{opts['max_gap_sec']}s"

    candidate_span = float(seg.end) - float(current[0].start)
    if candidate_span > float(opts["max_timeline_span_sec"]):
        return f"timeline_span>{opts['max_timeline_span_sec']}s"

    candidate_chars = sum(len(item.text) for item in current) + len(seg.text)
    if candidate_chars > int(opts["max_chars"]):
        return f"chars>{opts['max_chars']}"

    speech_duration = sum(item.duration for item in current) + seg.duration
    if speech_duration > float(opts["max_duration_sec"]):
        return f"duration>{opts['max_duration_sec']}s"

    estimated = sum(item.estimated_duration for item in current) + seg.estimated_duration
    available = sum(item.available_duration for item in current) + seg.available_duration
    speed_factor = estimated / max(0.001, available)
    if speed_factor > float(opts["max_chunk_speed_factor"]):
        return f"chunk_speed>{opts['max_chunk_speed_factor']}"

    return ""


def build_tts_plan(segments: List[Any], options: Optional[Dict[str, Any]] = None) -> TtsPlan:
    opts = normalize_planner_options(options)
    candidates = [seg for seg in segments if _segment_text(seg)]
    candidates.sort(key=lambda seg: (float(getattr(seg, "start", 0.0)), int(getattr(seg, "index", 0))))

    segment_plans: List[TtsSegmentPlan] = []
    for i, seg in enumerate(candidates):
        text = _segment_text(seg)
        start = float(getattr(seg, "start", 0.0))
        end = float(getattr(seg, "end", start))
        duration = max(0.001, end - start)
        next_start = float(getattr(candidates[i + 1], "start", 0.0)) if i + 1 < len(candidates) else None
        gap_to_next = None if next_start is None else max(0.0, next_start - end)
        tolerance = min(gap_to_next or 0.0, float(opts["max_tolerance_sec"]), float(opts["max_gap_sec"]))
        available = max(0.001, duration + tolerance)
        estimated = estimate_speech_duration(text, opts)
        speed_factor = estimated / available
        pressure = _pressure_for(speed_factor, opts)
        split_after = bool(gap_to_next is not None and gap_to_next > float(opts["max_gap_sec"]))
        split_after_reason = f"gap>{opts['max_gap_sec']}s" if split_after else ""
        segment_plans.append(TtsSegmentPlan(
            index=int(getattr(seg, "index", 0)),
            text=text,
            start=start,
            end=end,
            duration=duration,
            next_start=next_start,
            gap_to_next=gap_to_next,
            tolerance=tolerance,
            available_duration=available,
            estimated_duration=estimated,
            speed_factor=speed_factor,
            speed_pressure=pressure,
            split_after=split_after,
            split_after_reason=split_after_reason,
        ))

    chunks: List[TtsChunkPlan] = []
    current: List[TtsSegmentPlan] = []
    current_chars = 0
    pending_reason = "start"

    def flush(reason: str) -> None:
        nonlocal current, current_chars, pending_reason
        if not current:
            pending_reason = reason
            return
        chunks.append(_chunk_from_segments(
            len(chunks), current, split_reason=pending_reason or reason, opts=opts,
        ))
        current = []
        current_chars = 0
        pending_reason = reason

    for seg_plan in segment_plans:
        reason = _candidate_split_reason(current, seg_plan, opts=opts)
        if reason:
            flush(reason)

        current.append(seg_plan)
        current_chars += len(seg_plan.text)

        if (
            bool(opts["prefer_sentence_end"])
            and seg_plan.text
            and seg_plan.text[-1] in _SENTENCE_END_CHARS
            and current_chars >= int(opts["min_chars"])
        ):
            flush("sentence_end")
        elif seg_plan.split_after:
            flush(seg_plan.split_after_reason)

    flush("end")

    # Re-number in case a future change filters chunks.
    for i, chunk in enumerate(chunks):
        chunk.chunk_index = i

    return TtsPlan(segments=segment_plans, chunks=chunks, options=opts)
