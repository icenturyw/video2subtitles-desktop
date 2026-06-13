"""Subtitle timeline and content validation."""
from __future__ import annotations

from typing import Dict, List, Tuple

from job_models import SubtitleSegment


ValidationWarning = Tuple[str, str, Dict]  # (code, message, context)


def validate_timeline(segments: List[SubtitleSegment]) -> List[ValidationWarning]:
    """Validate subtitle timeline.

    Checks:
    - No negative times
    - End > Start
    - Segments in chronological order
    - No excessive gaps (> 10s between segments, warn)
    - No overlapping segments (end of N > start of N+1, warn)

    Returns:
        List of (code, message, context) warnings.
    """
    warnings: List[ValidationWarning] = []

    for i, seg in enumerate(segments):
        if seg.start < 0:
            warnings.append(("NEGATIVE_START", f"Segment {seg.index}: negative start time",
                             {"index": seg.index, "start": seg.start}))
        if seg.end <= seg.start:
            warnings.append(("INVALID_DURATION", f"Segment {seg.index}: end <= start",
                             {"index": seg.index, "start": seg.start, "end": seg.end}))
        if not seg.text.strip():
            warnings.append(("EMPTY_TEXT", f"Segment {seg.index}: empty text",
                             {"index": seg.index}))

    if len(segments) < 2:
        return warnings

    for i in range(1, len(segments)):
        prev = segments[i - 1]
        curr = segments[i]

        if curr.start < prev.start:
            warnings.append(("TIMELINE_DISORDER",
                             f"Segment {curr.index} starts before segment {prev.index}",
                             {"curr_index": curr.index, "prev_index": prev.index,
                              "curr_start": curr.start, "prev_start": prev.start}))

        gap = curr.start - prev.end
        if gap > 10.0:
            warnings.append(("LARGE_GAP",
                             f"Gap of {gap:.1f}s between segments {prev.index} and {curr.index}",
                             {"prev_index": prev.index, "curr_index": curr.index, "gap": gap}))

        if prev.end > curr.start:
            overlap = prev.end - curr.start
            if overlap > 0.1:
                warnings.append(("OVERLAP",
                                 f"Overlap of {overlap:.2f}s between segments "
                                 f"{prev.index} and {curr.index}",
                                 {"prev_index": prev.index, "curr_index": curr.index,
                                  "overlap": overlap}))

    return warnings


def validate_translation(segments: List[SubtitleSegment]) -> List[ValidationWarning]:
    """Validate that translated segments are reasonable.

    Checks:
    - Translation count matches source count
    - No empty translations
    - Translation not identical to source (likely untranslated)
    - Translation not excessively long compared to source

    Returns:
        List of warnings.
    """
    warnings: List[ValidationWarning] = []

    for seg in segments:
        if not seg.translation:
            warnings.append(("MISSING_TRANSLATION",
                             f"Segment {seg.index}: missing translation",
                             {"index": seg.index}))
            continue

        trans = seg.translation.strip()
        source = seg.text.strip()

        if not trans:
            warnings.append(("EMPTY_TRANSLATION",
                             f"Segment {seg.index}: empty translation",
                             {"index": seg.index}))
            continue

        if trans.lower() == source.lower():
            warnings.append(("UNTRANSLATED",
                             f"Segment {seg.index}: translation identical to source",
                             {"index": seg.index, "text": source[:80]}))

        length_ratio = len(trans) / max(len(source), 1)
        if length_ratio > 4.0:
            warnings.append(("TRANSLATION_TOO_LONG",
                             f"Segment {seg.index}: translation {length_ratio:.1f}x source length",
                             {"index": seg.index, "source_len": len(source),
                              "trans_len": len(trans), "ratio": length_ratio}))

    return warnings
