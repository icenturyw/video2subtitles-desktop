"""FFmpeg filter string building utilities."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional


def escape_ass_path(path: str | Path) -> str:
    """Escape a file path for use in FFmpeg subtitles filter.

    FFmpeg subtitles filter requires:
    - Backslashes converted to forward slashes or escaped
    - Colons escaped with \\:
    - Single quotes escaped
    """
    path_str = str(Path(path).resolve())
    path_str = path_str.replace("\\", "/").replace(":", "\\:")
    return path_str


def subtitles_filter(subtitle_path: str | Path, *,
                     force_style: Optional[str] = None) -> str:
    """Build a subtitles filter for FFmpeg.

    Args:
        subtitle_path: Path to subtitle file (ASS recommended for styles).
        force_style: Optional SRT force_style string.

    Returns:
        FFmpeg filter string, e.g. "subtitles='path'"
    """
    escaped = escape_ass_path(subtitle_path)
    parts = [f"subtitles='{escaped}'"]

    if force_style:
        parts.append(f"force_style='{force_style}'")

    return ":".join(parts)


_VIDEO_ENCODER_MAP = {
    "h264": "libx264",
    "hevc": "libx265",
    "h264_nvenc": "h264_nvenc",
    "hevc_nvenc": "hevc_nvenc",
}

_AUDIO_ENCODER_MAP = {
    "aac": "aac",
    "mp3": "libmp3lame",
    "copy": "copy",
}


def build_hardsub_command(
    ffmpeg_path: str,
    video_path: str | Path,
    subtitle_path: str | Path,
    output_path: str | Path,
    *,
    video_encoder: str = "libx264",
    audio_encoder: str = "aac",
    preset: str = "medium",
    crf: int = 22,
    extra_args: Optional[list] = None,
) -> list:
    """Build the full FFmpeg command for hard subtitling.

    Returns:
        List of command arguments suitable for subprocess.Popen.
    """
    filter_str = subtitles_filter(subtitle_path)

    cmd = [
        ffmpeg_path, "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(Path(video_path).resolve()),
        "-vf", filter_str,
        "-c:v", video_encoder,
        "-preset", preset,
        "-crf", str(crf),
        "-c:a", audio_encoder,
        "-movflags", "+faststart",
        str(Path(output_path).resolve()),
    ]

    if extra_args:
        cmd.extend(extra_args)

    return cmd


def build_softsub_command(
    ffmpeg_path: str,
    video_path: str | Path,
    subtitle_path: str | Path,
    output_path: str | Path,
    *,
    subtitle_codec: str = "mov_text",
    language: str = "und",
) -> list:
    """Build the full FFmpeg command for soft subtitling.

    Returns:
        List of command arguments suitable for subprocess.Popen.
    """
    cmd = [
        ffmpeg_path, "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(Path(video_path).resolve()),
        "-i", str(Path(subtitle_path).resolve()),
        "-c:v", "copy",
        "-c:a", "copy",
        f"-c:s:{subtitle_codec}",
        "-metadata:s:s:0", f"language={language}",
        "-disposition:s:0", "default",
        str(Path(output_path).resolve()),
    ]

    return cmd
