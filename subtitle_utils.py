"""Reusable subtitle formatting and parsing helpers for Video2Subtitles."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, List, Mapping


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


_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_ASS_TAG_RE = re.compile(r"\{[^{}]*\}")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_PUNCT_RE = re.compile(
    r"[\s\u3000\.,!?;:'\"`~\-—–_()\[\]{}<>/\\|@#$%^&*+=，。！？；：、“”‘’（）【】《》…·￥]+"
)
_FILLER_RE = re.compile(
    r"^(?:嗯+|呃+|额+|唔+|哼+|啊+|呣+|em+|um+|uh+|hmm+|mm+)$",
    re.IGNORECASE,
)


def normalize_subtitle_text(text: Any) -> str:
    """Normalize subtitle text without changing its meaning.

    This is intentionally conservative: it removes invisible/control-like
    formatting, collapses inline whitespace, and trims line boundaries.  It
    does not rewrite words.  The function is shared by subtitle export and TTS
    so punctuation-only or invisible text cannot accidentally become a spoken
    audio segment.
    """
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = _ZERO_WIDTH_RE.sub("", value)
    value = _ASS_TAG_RE.sub("", value)
    value = _HTML_TAG_RE.sub("", value)
    lines = [_SPACE_RE.sub(" ", line).strip() for line in value.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()


def _semantic_chars(text: Any) -> str:
    cleaned = normalize_subtitle_text(text)
    return _PUNCT_RE.sub("", cleaned).strip()


def is_punctuation_only_text(text: Any) -> bool:
    """Return True for subtitles such as "。" or "...".

    These lines are common after automatic splitting.  They are useful as
    punctuation for the previous subtitle, but they must never be sent to TTS:
    autoregressive TTS models may free-run into hums/fillers when the prompt has
    no actual speech content.
    """
    cleaned = normalize_subtitle_text(text)
    return bool(cleaned) and not _semantic_chars(cleaned)


def is_filler_only_text(text: Any) -> bool:
    """Return True for very short filler-only captions such as "嗯嗯"."""
    semantic = _semantic_chars(text)
    return bool(semantic) and bool(_FILLER_RE.fullmatch(semantic))


def is_speech_subtitle_text(text: Any) -> bool:
    """Return True when a subtitle contains content worth speaking with TTS."""
    semantic = _semantic_chars(text)
    return bool(semantic) and not is_filler_only_text(text)


def _merge_punctuation_text(previous: Any, punctuation: Any) -> str:
    prev = normalize_subtitle_text(previous)
    punct = normalize_subtitle_text(punctuation)
    if not punct:
        return prev
    if not prev:
        return punct
    if prev.endswith(punct):
        return prev
    # Chinese punctuation should stick to the previous word; latin punctuation
    # is still safe to append directly because this helper is only used for
    # punctuation-only captions.
    return f"{prev}{punct}"


def normalize_subtitle_timeline(
    subtitles: Iterable[Subtitle],
    *,
    min_gap: float = 0.02,
    min_duration: float = 0.12,
    merge_punctuation: bool = True,
) -> list[dict[str, Any]]:
    """Clean subtitle dictionaries and remove small timeline overlaps.

    The bundled faster-whisper path used to add 100ms to every subtitle end.
    That created a near-constant overlap pattern like ``03.970 -> 03.870``.
    This normalizer merges punctuation-only captions into the previous line and
    clamps adjacent subtitles so generated SRT/TTS windows do not overlap.
    """
    items: list[dict[str, Any]] = []
    for subtitle in subtitles:
        start = _as_seconds(subtitle.get("start", 0))
        end = max(start, _as_seconds(subtitle.get("end", start)))
        text = normalize_subtitle_text(subtitle.get("text", ""))
        if not text:
            continue

        if merge_punctuation and is_punctuation_only_text(text):
            if items:
                items[-1]["text"] = _merge_punctuation_text(items[-1].get("text", ""), text)
                items[-1]["end"] = max(float(items[-1].get("end", 0) or 0), end)
            continue

        normalized = dict(subtitle)
        normalized["start"] = round(start, 3)
        normalized["end"] = round(max(end, start + 0.001), 3)
        normalized["text"] = text
        items.append(normalized)

    items.sort(key=lambda item: (float(item.get("start", 0) or 0), float(item.get("end", 0) or 0)))
    min_gap = max(0.0, float(min_gap))
    min_duration = max(0.001, float(min_duration))

    for i in range(len(items) - 1):
        prev = items[i]
        curr = items[i + 1]
        prev_start = _as_seconds(prev.get("start", 0))
        prev_end = max(prev_start + 0.001, _as_seconds(prev.get("end", prev_start)))
        curr_start = _as_seconds(curr.get("start", 0))
        curr_end = max(curr_start + 0.001, _as_seconds(curr.get("end", curr_start)))

        if prev_end + min_gap <= curr_start:
            continue

        desired_prev_end = curr_start - min_gap
        if desired_prev_end - prev_start >= min_duration:
            prev["end"] = round(desired_prev_end, 3)
            continue

        desired_curr_start = prev_end + min_gap
        if curr_end - desired_curr_start >= min_duration:
            curr["start"] = round(desired_curr_start, 3)
            continue

        # Last-resort midpoint split for pathological short overlaps.  This is
        # rare, but it prevents invalid timelines from leaking into rendering.
        boundary = max(prev_start + min_duration, min(curr_start, curr_end - min_duration))
        prev["end"] = round(max(prev_start + 0.001, boundary - min_gap / 2), 3)
        curr["start"] = round(min(curr_end - 0.001, max(curr_start, boundary + min_gap / 2)), 3)

    result: list[dict[str, Any]] = []
    for item in items:
        start = _as_seconds(item.get("start", 0))
        end = _as_seconds(item.get("end", start))
        if end <= start:
            continue
        item["start"] = round(start, 3)
        item["end"] = round(end, 3)
        result.append(item)
    return result


def reconstruct_split_words(
    subtitles: list[dict[str, Any]],
    *,
    max_gap_sec: float = 0.1,
) -> list[dict[str, Any]]:
    """Merge subtitle segments where a word has been cut by the auto-caption boundary.

    YouTube and similar auto-captions frequently split a word like "scaling"
    into ``"scali"`` / ``"ng"`` across two segments with a ~20 ms gap.  This
    function detects such splits and joins the text, reusing the first
    segment's start time and the second's end time.
    """
    if len(subtitles) < 2:
        return list(subtitles)
    items = list(subtitles)
    result: list[dict[str, Any]] = []

    def _last_fragment(text: str):
        sp = text.rfind(" ")
        return (text[:sp + 1], text[sp + 1:]) if sp >= 0 else ("", text)

    def _first_fragment(text: str):
        sp = text.find(" ")
        return (text[:sp], text[sp:]) if sp >= 0 else (text, "")

    i = 0
    while i < len(items):
        curr = dict(items[i])
        curr_text = str(curr.get("text", "") or "")

        # Backward merge: if this segment is a standalone lowercase fragment
        # (no spaces, short) and the previous result ends with a lowercase
        # letter at a tight gap, merge backward.
        if (
            result
            and " " not in curr_text
            and curr_text
            and curr_text[0].islower()
            and len(curr_text) <= 3
        ):
            prev = result[-1]
            prev_text = str(prev.get("text", "") or "")
            gap = float(curr.get("start", 0) or 0) - float(prev.get("end", 0) or 0)
            if 0 <= gap <= max_gap_sec and prev_text and prev_text[-1].islower():
                prefix, last_frag = _last_fragment(prev_text)
                prev["text"] = prefix + last_frag + curr_text
                prev["end"] = max(
                    float(prev.get("end", 0) or 0),
                    float(curr.get("end", 0) or 0),
                )
                i += 1
                continue

        # Forward merge: chain lowercase→lowercase across tight gaps.
        while i + 1 < len(items):
            nxt = items[i + 1]
            gap = float(nxt.get("start", 0) or 0) - float(curr.get("end", 0) or 0)
            if gap < 0 or gap > max_gap_sec:
                break
            curr_text = str(curr.get("text", "") or "")
            next_text = str(nxt.get("text", "") or "")
            if not (curr_text and next_text):
                break
            if not (curr_text[-1].islower() and next_text[0].islower()):
                break
            prefix, last_frag = _last_fragment(curr_text)
            first_frag, suffix = _first_fragment(next_text)
            if len(last_frag) > 2 and len(first_frag) > 2:
                break
            curr["text"] = prefix + last_frag + first_frag + suffix
            curr["end"] = max(
                float(curr.get("end", 0) or 0),
                float(nxt.get("end", 0) or 0),
            )
            i += 1
            _, joined = _last_fragment(curr["text"])
            if len(joined) >= 3:
                break
        result.append(curr)
        i += 1
    return result


def find_repeated_subtitle_runs(
    subtitles: Iterable[Subtitle],
    *,
    min_repeats: int = 3,
    max_gap_sec: float = 8.0,
) -> list[dict[str, Any]]:
    """Detect suspicious repeated subtitle text runs.

    Automatic speech recognition can hallucinate the same line during noisy or
    silent spans.  The function only reports; callers can decide whether to warn
    or stop before dubbing.
    """
    runs: list[dict[str, Any]] = []
    current_text = ""
    current_items: list[Subtitle] = []

    def flush() -> None:
        if len(current_items) >= min_repeats:
            runs.append({
                "text": current_text,
                "count": len(current_items),
                "start": current_items[0].get("start", 0),
                "end": current_items[-1].get("end", 0),
            })

    for subtitle in subtitles:
        text = _semantic_chars(subtitle.get("text", ""))
        if not text:
            continue
        if current_items:
            prev_end = _as_seconds(current_items[-1].get("end", 0))
            gap = _as_seconds(subtitle.get("start", 0)) - prev_end
        else:
            gap = 0.0
        if text == current_text and gap <= max_gap_sec:
            current_items.append(subtitle)
        else:
            flush()
            current_text = text
            current_items = [subtitle]
    flush()
    return runs


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


def _subtitle_keyframe_score(
    subtitle: Subtitle,
    *,
    previous_end: float | None = None,
    next_start: float | None = None,
) -> float:
    text = normalize_subtitle_text(subtitle.get("text", ""))
    semantic = _semantic_chars(text)
    start = _as_seconds(subtitle.get("start", 0))
    end = max(start, _as_seconds(subtitle.get("end", start)))
    duration = max(0.0, end - start)

    score = float(min(len(semantic), 120))
    score += min(duration, 8.0) * 2.0
    if re.search(r"[.!?。！？]$", text.strip()):
        score += 10.0
    if previous_end is not None and start - previous_end >= 1.2:
        score += 8.0
    if next_start is not None and next_start - end >= 1.2:
        score += 4.0
    return score


def choose_subtitle_keyframe_points(
    subtitles: Iterable[Subtitle],
    *,
    target_interval: float = 30.0,
    min_gap: float = 8.0,
    max_frames: int = 80,
) -> list[dict[str, Any]]:
    """Choose frame timestamps from subtitle timing and text importance.

    The result is intentionally compact: roughly one visual reference per
    ``target_interval`` seconds, aligned to the most representative subtitle in
    that time window.  This keeps ChatGPT upload packages small while avoiding
    blind fixed-interval grabs during silence or throwaway captions.
    """
    items: list[dict[str, Any]] = []
    for index, subtitle in enumerate(subtitles, 1):
        start = _as_seconds(subtitle.get("start", 0))
        end = max(start, _as_seconds(subtitle.get("end", start)))
        text = normalize_subtitle_text(subtitle.get("text", ""))
        if end <= start or not is_speech_subtitle_text(text):
            continue
        items.append({
            "subtitle_index": index,
            "start": start,
            "end": end,
            "text": text,
        })

    if not items:
        return []

    items.sort(key=lambda item: (item["start"], item["end"]))
    for idx, item in enumerate(items):
        previous_end = items[idx - 1]["end"] if idx > 0 else None
        next_start = items[idx + 1]["start"] if idx + 1 < len(items) else None
        item["score"] = _subtitle_keyframe_score(
            item,
            previous_end=previous_end,
            next_start=next_start,
        )
        duration = max(0.001, item["end"] - item["start"])
        item["timestamp"] = round(min(item["end"] - 0.05, item["start"] + duration * 0.5), 3)

    target_interval = max(5.0, float(target_interval or 30.0))
    min_gap = max(0.0, float(min_gap or 0.0))
    max_frames = max(1, int(max_frames or 1))
    timeline_end = max(item["end"] for item in items)
    window_count = max(1, int((timeline_end + target_interval - 0.001) // target_interval))

    selected: list[dict[str, Any]] = []
    used_indices: set[int] = set()
    for window_index in range(window_count):
        window_start = window_index * target_interval
        window_end = window_start + target_interval
        window_center = (window_start + window_end) / 2.0
        candidates = [
            item for item in items
            if item["subtitle_index"] not in used_indices
            and item["start"] < window_end
            and item["end"] >= window_start
        ]
        if not candidates:
            continue

        def rank(item: dict[str, Any]) -> float:
            distance_penalty = abs(item["timestamp"] - window_center) / target_interval
            return float(item["score"]) - distance_penalty

        best = max(candidates, key=rank)
        if selected and best["timestamp"] - selected[-1]["timestamp"] < min_gap:
            previous = selected[-1]
            if best["score"] <= previous["score"]:
                continue
            used_indices.discard(previous["subtitle_index"])
            selected[-1] = best
            used_indices.add(best["subtitle_index"])
            continue
        selected.append(best)
        used_indices.add(best["subtitle_index"])

    if len(selected) > max_frames:
        if max_frames == 1:
            selected = [max(selected, key=lambda item: item["score"])]
        else:
            step = (len(selected) - 1) / float(max_frames - 1)
            selected = [selected[round(i * step)] for i in range(max_frames)]

    selected.sort(key=lambda item: item["timestamp"])
    return [
        {
            "subtitle_index": int(item["subtitle_index"]),
            "timestamp": round(float(item["timestamp"]), 3),
            "subtitle_start": round(float(item["start"]), 3),
            "subtitle_end": round(float(item["end"]), 3),
            "subtitle_text": item["text"],
            "score": round(float(item["score"]), 3),
        }
        for item in selected
    ]


def align_keyframe_points_to_scene_changes(
    points: Iterable[Mapping[str, Any]],
    scene_timestamps: Iterable[Any],
    *,
    max_offset: float = 2.0,
    post_scene_offset: float = 0.4,
) -> list[dict[str, Any]]:
    """Move subtitle-selected frame points to nearby scene changes when useful."""
    scenes = sorted({
        round(_as_seconds(timestamp), 3)
        for timestamp in scene_timestamps
        if _as_seconds(timestamp) > 0
    })
    if not scenes:
        return [dict(point) for point in points]

    max_offset = max(0.0, float(max_offset or 0.0))
    post_scene_offset = max(0.0, float(post_scene_offset or 0.0))
    aligned: list[dict[str, Any]] = []
    for point in points:
        item = dict(point)
        base_ts = _as_seconds(item.get("timestamp", 0))
        subtitle_start = _as_seconds(item.get("subtitle_start", base_ts))
        subtitle_end = max(subtitle_start, _as_seconds(item.get("subtitle_end", base_ts)))
        window_start = max(0.0, subtitle_start - max_offset)
        window_end = subtitle_end + max_offset
        candidates = [
            scene for scene in scenes
            if window_start <= scene <= window_end
        ]
        if not candidates:
            item.setdefault("visual_anchor", "subtitle_midpoint")
            aligned.append(item)
            continue

        scene = min(candidates, key=lambda value: abs(value - base_ts))
        adjusted = round(scene + post_scene_offset, 3)
        if subtitle_end > subtitle_start:
            adjusted = max(subtitle_start + 0.05, min(adjusted, subtitle_end - 0.05))
        else:
            adjusted = max(0.0, adjusted)
        item["original_timestamp"] = round(base_ts, 3)
        item["timestamp"] = round(adjusted, 3)
        item["scene_timestamp"] = round(scene, 3)
        item["visual_anchor"] = "scene_change"
        aligned.append(item)
    return aligned


def sanitize_filename(name: Any, fallback: str = "video") -> str:
    sanitized = "".join(
        char if char.isalnum() or char in " ._-" else "_"
        for char in str(name or "")
    ).strip().strip("._")
    return sanitized if sanitized else fallback
