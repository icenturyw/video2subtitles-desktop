"""Subtitle timeline and content validation."""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from job_models import SubtitleSegment
from subtitle_utils import (
    find_repeated_subtitle_runs,
    is_filler_only_text,
    is_punctuation_only_text,
)
try:
    from translation.quality import target_language_issues
except Exception:  # pragma: no cover - import fallback for partial installs
    target_language_issues = None


ValidationWarning = Tuple[str, str, Dict]  # (code, message, context)

_ACRONYM_RE = re.compile(r"^[A-Za-z0-9\s\.\,\&\-\%\$\(\)\/\:\;\'\"\!\?\+\=@\#\*\[\]\{\}]{1,30}$")

def _is_likely_acronym_or_symbol(text: str) -> bool:
    """Return True if text is probably a proper noun/acronym/symbol, not a natural-language fragment that should be translated."""
    if not _ACRONYM_RE.match(text):
        return False
    stripped = text.strip()
    letters = sum(1 for c in stripped if c.isalpha())
    digits = sum(1 for c in stripped if c.isdigit())
    total = max(len(stripped), 1)
    # All-uppercase with few lowercase (e.g. "NASDAQ", "GDP", "SPX")
    if letters > 0 and stripped == stripped.upper():
        return True
    # More digits than letters (e.g. "50 50", "7 700")
    if digits >= letters and digits > 0:
        return True
    # Very short fragments (≤ 3 meaningful chars) — likely truncated words
    meaningful = letters + digits
    if meaningful <= 3:
        return True
    return False


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
        elif is_punctuation_only_text(seg.text):
            warnings.append(("PUNCTUATION_ONLY",
                             f"Segment {seg.index}: punctuation-only text",
                             {"index": seg.index, "text": seg.text}))
        elif is_filler_only_text(seg.text):
            warnings.append(("FILLER_ONLY",
                             f"Segment {seg.index}: filler-only text",
                             {"index": seg.index, "text": seg.text}))

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
            if overlap >= 0.02:
                warnings.append(("OVERLAP",
                                 f"Overlap of {overlap:.2f}s between segments "
                                 f"{prev.index} and {curr.index}",
                                 {"prev_index": prev.index, "curr_index": curr.index,
                                  "overlap": overlap}))

    repeated_runs = find_repeated_subtitle_runs([seg.to_srt_dict() for seg in segments])
    for run in repeated_runs:
        warnings.append(("REPEATED_TEXT_RUN",
                         f"Repeated subtitle text {run['count']} times: {run['text'][:40]}",
                         run))

    return warnings


def validate_translation(segments: List[SubtitleSegment], target_language: str = "") -> List[ValidationWarning]:
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
            if seg.metadata.get("translation_skipped"):
                continue
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
            if not _is_likely_acronym_or_symbol(source):
                warnings.append(("UNTRANSLATED",
                                 f"Segment {seg.index}: translation identical to source",
                                 {"index": seg.index, "text": source[:80]}))

        length_ratio = len(trans) / max(len(source), 1)
        if length_ratio > 4.0:
            warnings.append(("TRANSLATION_TOO_LONG",
                             f"Segment {seg.index}: translation {length_ratio:.1f}x source length",
                             {"index": seg.index, "source_len": len(source),
                              "trans_len": len(trans), "ratio": length_ratio}))

        if target_language and target_language_issues is not None:
            for issue in target_language_issues(trans, target_language, source_text=source, index=seg.index):
                warnings.append((issue.code,
                                 f"Segment {seg.index}: {issue.message}",
                                 issue.context or {"index": seg.index}))

    return warnings
