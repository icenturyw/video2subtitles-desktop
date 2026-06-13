"""SRT subtitle writer for source, translated, and bilingual output."""
from __future__ import annotations

from pathlib import Path
from typing import List

from job_models import SubtitleSegment
from subtitle_utils import save_srt_file, subtitles_to_srt, subtitles_to_txt, subtitles_to_vtt


def segments_to_srt(segments: List[SubtitleSegment], mode: str = "source") -> str:
    """Generate SRT content from subtitle segments.

    Args:
        segments: List of SubtitleSegment.
        mode: One of "source", "translated", "bilingual".

    Returns:
        SRT file content as string.
    """
    srt_list = []
    for seg in segments:
        source_text = seg.text
        trans_text = seg.translation if seg.translation else ""

        if mode == "source":
            text = source_text
        elif mode == "translated":
            text = trans_text or source_text
        elif mode == "bilingual":
            text = source_text
            if trans_text:
                text = f"{source_text}\n{trans_text}"
        else:
            text = source_text

        srt_list.append({
            "start": seg.start,
            "end": seg.end,
            "text": text,
        })

    return subtitles_to_srt(srt_list)


def segments_to_vtt(segments: List[SubtitleSegment], mode: str = "source") -> str:
    """Generate VTT content from subtitle segments."""
    srt_list = []
    for seg in segments:
        source_text = seg.text
        trans_text = seg.translation if seg.translation else ""
        text = source_text
        if mode == "bilingual" and trans_text:
            text = f"{source_text}\n{trans_text}"
        elif mode == "translated":
            text = trans_text or source_text
        srt_list.append({
            "start": seg.start,
            "end": seg.end,
            "text": text,
        })
    return subtitles_to_vtt(srt_list)


def segments_to_txt(segments: List[SubtitleSegment], mode: str = "source") -> str:
    """Generate plain text from subtitle segments."""
    srt_list = []
    for seg in segments:
        source_text = seg.text
        trans_text = seg.translation if seg.translation else ""
        text = source_text
        if mode == "bilingual" and trans_text:
            text = f"{source_text}\n{trans_text}"
        elif mode == "translated":
            text = trans_text or source_text
        srt_list.append({
            "start": seg.start,
            "end": seg.end,
            "text": text,
        })
    return subtitles_to_txt(srt_list)


def write_srt(segments: List[SubtitleSegment], output_path: Path,
              mode: str = "source") -> None:
    """Write SRT file from segments."""
    content = segments_to_srt(segments, mode)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")


def write_vtt(segments: List[SubtitleSegment], output_path: Path,
              mode: str = "source") -> None:
    """Write VTT file from segments."""
    content = segments_to_vtt(segments, mode)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")


def write_txt(segments: List[SubtitleSegment], output_path: Path,
              mode: str = "source") -> None:
    """Write TXT file from segments."""
    content = segments_to_txt(segments, mode)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
