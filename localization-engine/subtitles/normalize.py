"""Subtitle reading and normalization.

Reads SRT/ASS/VTT files and converts them to unified SubtitleSegments.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from job_models import SubtitleSegment, segments_from_srt_dicts
from subtitle_utils import parse_srt_file, parse_srt_text


def read_subtitle_file(file_path: str | Path) -> Optional[List[SubtitleSegment]]:
    """Read a subtitle file and return normalized SubtitleSegments.

    Supports SRT, ASS, and VTT formats via file extension detection.

    Args:
        file_path: Path to the subtitle file.

    Returns:
        List of SubtitleSegments, or None if the file cannot be read.
    """
    path = Path(file_path)
    if not path.exists():
        return None

    ext = path.suffix.lower()

    if ext == ".srt":
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        srt_dicts = parse_srt_text(raw_text)
        return segments_from_srt_dicts(srt_dicts)
    elif ext == ".vtt":
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        srt_dicts = parse_srt_text(raw_text)
        return segments_from_srt_dicts(srt_dicts)
    elif ext == ".ass":
        return _read_ass(path)
    else:
        try:
            srt_dicts = parse_srt_file(path)
            return segments_from_srt_dicts(srt_dicts)
        except Exception:
            return None


def _read_ass(file_path: Path) -> Optional[List[SubtitleSegment]]:
    """Parse ASS file into SubtitleSegments (basic extraction)."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    segments: List[SubtitleSegment] = []
    in_events = False
    index = 0
    time_pattern = r"(\d+):(\d+):(\d+)\.(\d+)"

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper() == "[EVENTS]":
            in_events = True
            continue
        if stripped.startswith("["):
            in_events = False
            continue
        if not in_events or not stripped.startswith("Dialogue:"):
            continue

        parts = stripped.split(",", 9)
        if len(parts) < 10:
            continue

        start_str = parts[1].strip()
        end_str = parts[2].strip()
        dialog_text = parts[9].strip()

        import re
        start = _ass_time_to_seconds(start_str)
        end = _ass_time_to_seconds(end_str)

        if start is not None and end is not None and dialog_text:
            index += 1
            segment = SubtitleSegment(
                index=index,
                start=start,
                end=end,
                text=dialog_text.replace("\\N", "\n"),
            )
            segments.append(segment)

    return segments


def _ass_time_to_seconds(time_str: str) -> Optional[float]:
    """Convert ASS time format (0:00:00.00) to seconds."""
    import re
    m = re.match(r"(\d+):(\d+):(\d+)\.(\d+)", time_str)
    if not m:
        return None
    hours = int(m.group(1))
    minutes = int(m.group(2))
    seconds = int(m.group(3))
    centiseconds = int(m.group(4))
    return hours * 3600 + minutes * 60 + seconds + centiseconds / 100
