"""Reusable subtitle formatting and parsing helpers for Video2Subtitles."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Mapping


Subtitle = Mapping[str, Any]

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".3gp", ".ogv", ".ts",
})

_TIME_LINE_RE = re.compile(
    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)"
)


def _as_seconds(value: Any) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, seconds)


def format_subtitle_time(seconds: Any, decimal_separator: str = ",") -> str:
    """Format seconds as an SRT/VTT timestamp.

    SRT uses a comma as the millisecond separator, while VTT uses a dot.
    """
    separator = "." if decimal_separator == "." else ","
    total_ms = int(_as_seconds(seconds) * 1000)
    hours, remainder = divmod(total_ms, 3600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def _subtitle_text(subtitle: Subtitle, *, include_translation: bool = False) -> str:
    text = str(subtitle.get("text", "") or "")
    translation = str(subtitle.get("translation", "") or "")
    if include_translation and translation:
        return f"{text}\n{translation}" if text else translation
    return text


def subtitles_to_srt(subtitles: Iterable[Subtitle]) -> str:
    lines: list[str] = []
    for index, subtitle in enumerate(subtitles, 1):
        start = format_subtitle_time(subtitle.get("start", 0), ",")
        end = format_subtitle_time(subtitle.get("end", 0), ",")
        lines.append(str(index))
        lines.append(f"{start} --> {end}")
        lines.append(_subtitle_text(subtitle, include_translation=True))
        lines.append("")
    return "\n".join(lines)


def subtitles_to_vtt(subtitles: Iterable[Subtitle]) -> str:
    lines = ["WEBVTT", ""]
    for subtitle in subtitles:
        start = format_subtitle_time(subtitle.get("start", 0), ".")
        end = format_subtitle_time(subtitle.get("end", 0), ".")
        lines.append(f"{start} --> {end}")
        lines.append(_subtitle_text(subtitle))
        lines.append("")
    return "\n".join(lines)


def subtitles_to_txt(subtitles: Iterable[Subtitle]) -> str:
    return "\n".join(_subtitle_text(subtitle) for subtitle in subtitles)


def _write_text(path: str | Path, content: str) -> None:
    output_path = Path(path)
    if output_path.parent and str(output_path.parent) != ".":
        output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")


def save_srt_file(subtitles: Iterable[Subtitle], output_path: str | Path) -> None:
    _write_text(output_path, subtitles_to_srt(subtitles))


def save_vtt_file(subtitles: Iterable[Subtitle], output_path: str | Path) -> None:
    _write_text(output_path, subtitles_to_vtt(subtitles))


def save_txt_file(subtitles: Iterable[Subtitle], output_path: str | Path) -> None:
    _write_text(output_path, subtitles_to_txt(subtitles))


def _seconds_from_match(match: re.Match[str], offset: int) -> float:
    hours = int(match.group(offset))
    minutes = int(match.group(offset + 1))
    seconds = int(match.group(offset + 2))
    millis = int(match.group(offset + 3).ljust(3, "0")[:3])
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def parse_srt_text(text: str) -> list[dict[str, Any]]:
    subtitles: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", str(text or "").strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        match = _TIME_LINE_RE.match(lines[1].strip())
        if not match:
            continue
        subtitles.append({
            "start": round(_seconds_from_match(match, 1), 2),
            "end": round(_seconds_from_match(match, 5), 2),
            "text": "\n".join(lines[2:]),
        })
    return subtitles


def parse_srt_file(srt_path: str | Path) -> list[dict[str, Any]]:
    try:
        return parse_srt_text(Path(srt_path).read_text(encoding="utf-8"))
    except Exception:
        return []


def sanitize_filename(name: Any, fallback: str = "video") -> str:
    sanitized = "".join(
        char if char.isalnum() or char in " ._-" else "_"
        for char in str(name or "")
    ).strip().strip("._")
    return sanitized if sanitized else fallback
