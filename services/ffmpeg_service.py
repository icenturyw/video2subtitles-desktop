"""FFmpeg service for subtitle burning and video rendering.

Provides safe, cancellable FFmpeg operations with path escaping,
.staging file approach, and structured error reporting.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from process_utils import hidden_subprocess_kwargs

logger = logging.getLogger("services.ffmpeg")


_SUBTITLE_ENCODERS = {
    "ass": "ass",
    "srt": "srt",
    "vtt": "vtt",
    "mov_text": "mov_text",
}


def find_ffmpeg() -> Optional[str]:
    """Locate ffmpeg executable."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    # Common fallback paths
    candidates = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def get_ffmpeg_version() -> str:
    """Get installed ffmpeg version string."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return ""
    try:
        result = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True, text=True, timeout=10,
            **hidden_subprocess_kwargs(),
        )
        first_line = result.stdout.splitlines()[0] if result.stdout else ""
        return first_line.strip()
    except Exception:
        return ""


def escape_path(path: str | Path) -> str:
    """Escape a file path for FFmpeg argument usage.

    Handles Windows paths with spaces and special characters.
    FFmpeg expects paths to be passed directly as arguments (not shell).
    """
    return str(path)


def _build_hardsub_filter(subtitle_path: str | Path,
                           style: Optional[Dict[str, Any]] = None) -> str:
    """Build an FFmpeg subtitles filter for hardcoding.

    Args:
        subtitle_path: Path to the ASS/SRT subtitle file.
        style: Optional style overrides.

    Returns:
        FFmpeg filter string.
    """
    path_str = str(Path(subtitle_path).resolve()).replace("\\", "/").replace(":", "\\:")
    if Path(subtitle_path).suffix.lower() == ".ass":
        return f"subtitles='{path_str}'"
    else:
        return f"subtitles='{path_str}':force_style='FontName=Microsoft YaHei,FontSize=24'"


def probe_video(video_path: str | Path) -> Dict[str, Any]:
    """Probe video file for stream info using ffprobe.

    Returns:
        Dict with keys: width, height, duration, has_audio, codec, etc.
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return {"error": "ffmpeg not found"}

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        # Derive ffprobe path from ffmpeg
        ffprobe_path = str(Path(ffmpeg).parent / "ffprobe")
        if os.name == "nt":
            ffprobe_path += ".exe"
        if not Path(ffprobe_path).exists():
            return {"error": "ffprobe not found"}
        ffprobe = ffprobe_path

    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams",
                escape_path(video_path),
            ],
            capture_output=True, text=True, timeout=30,
            **hidden_subprocess_kwargs(),
        )
        import json
        data = json.loads(result.stdout)

        info: Dict[str, Any] = {}
        fmt = data.get("format", {})
        info["duration"] = float(fmt.get("duration", 0))
        info["size"] = int(fmt.get("size", 0))
        info["bit_rate"] = fmt.get("bit_rate", "")

        for stream in data.get("streams", []):
            codec_type = stream.get("codec_type")
            if codec_type == "video":
                info["width"] = int(stream.get("width", 0))
                info["height"] = int(stream.get("height", 0))
                info["video_codec"] = stream.get("codec_name", "")
                info["has_video"] = True
            elif codec_type == "audio":
                info["has_audio"] = True
                info["audio_codec"] = stream.get("codec_name", "")

        return info
    except Exception as e:
        return {"error": str(e)}


class FFmpegProcess:
    """Manage a single FFmpeg subprocess with cancellation support."""

    def __init__(self, cmd: List[str], log_path: Optional[Path] = None):
        self._cmd = cmd
        self._log_path = log_path
        self._process: Optional[subprocess.Popen] = None
        self._cancelled = False
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the FFmpeg process."""
        self._process = subprocess.Popen(
            self._cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **hidden_subprocess_kwargs(),
        )

    def wait(self, timeout: Optional[float] = None, cancel_checker=None) -> Tuple[bool, str, str]:
        """Wait for process to complete.

        Returns:
            Tuple of (success, stdout, stderr).
        """
        if not self._process:
            return False, "", "Process not started"

        deadline = time.monotonic() + timeout if timeout is not None else None

        try:
            while True:
                if cancel_checker and cancel_checker():
                    self.cancel()
                    return False, "", "Cancelled"

                try:
                    stdout, stderr = self._process.communicate(timeout=0.25)
                    out = stdout.decode("utf-8", errors="replace") if stdout else ""
                    err = stderr.decode("utf-8", errors="replace") if stderr else ""

                    if self._log_path:
                        self._append_log(err[:2000])

                    success = self._process.returncode == 0
                    return success, out, err
                except subprocess.TimeoutExpired:
                    if deadline is not None and time.monotonic() >= deadline:
                        self._terminate()
                        return False, "", "Process timed out"
        except subprocess.TimeoutExpired:
            self._terminate()
            return False, "", "Process timed out"

    def cancel(self) -> None:
        """Cancel the running FFmpeg process."""
        with self._lock:
            self._cancelled = True
        self._terminate()

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def _terminate(self) -> None:
        if self._process and self._process.poll() is None:
            if os.name == "nt":
                self._process.terminate()
            else:
                self._process.send_signal(signal.SIGTERM)
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)

    def _append_log(self, text: str) -> None:
        if not self._log_path:
            return
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"[ffmpeg] {text}\n")
        except Exception:
            pass


def render_hardsub(
    video_path: str | Path,
    subtitle_path: str | Path,
    output_path: str | Path,
    *,
    language: str = "",
    subtitle_mode: str = "bilingual",
    cancel_checker=None,
    log_path: Optional[Path] = None,
    video_codec: str = "libx264",
    audio_codec: str = "aac",
    preset: str = "medium",
    crf: int = 22,
) -> Dict[str, Any]:
    """Render video with hardcoded subtitles.

    Args:
        video_path: Source video file.
        subtitle_path: ASS/SRT subtitle file.
        output_path: Output video file path.
        language: Target language code for filename (optional).
        subtitle_mode: Subtitle mode description (for naming).
        cancel_checker: Optional callable returning True if cancelled.
        log_path: Optional path for FFmpeg log output.
        video_codec: Video encoder.
        audio_codec: Audio encoder.
        preset: x264 preset.
        crf: x264 CRF value.

    Returns:
        Dict with "success" (bool), "output_path" (str), "error" (str, optional).
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return {"success": False, "error": "FFmpeg not found"}

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to .partial first, then rename atomically
    partial_path = out_path.with_name(f"{out_path.stem}.partial{out_path.suffix}")

    filter_str = _build_hardsub_filter(subtitle_path)

    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
        "-i", escape_path(video_path),
        "-vf", filter_str,
        "-c:v", video_codec,
        "-preset", preset,
        "-crf", str(crf),
        "-c:a", audio_codec,
        "-movflags", "+faststart",
        escape_path(partial_path),
    ]

    process = FFmpegProcess(cmd, log_path)
    process.start()

    try:
        success, stdout, stderr = process.wait(timeout=None, cancel_checker=cancel_checker)

        if process.is_cancelled() or stderr == "Cancelled":
            if partial_path.exists():
                partial_path.unlink(missing_ok=True)
            return {"success": False, "error": "Cancelled", "cancelled": True}

        if not success:
            if partial_path.exists():
                partial_path.unlink(missing_ok=True)
            return {"success": False, "error": f"FFmpeg failed: {stderr[:500]}"}

        # Atomic rename
        if partial_path.exists():
            if out_path.exists():
                os.remove(str(out_path))
            os.rename(str(partial_path), str(out_path))

        return {
            "success": True,
            "output_path": str(out_path),
        }
    except Exception as e:
        if partial_path.exists():
            partial_path.unlink(missing_ok=True)
        return {"success": False, "error": str(e)}


def render_softsub(
    video_path: str | Path,
    subtitle_path: str | Path,
    output_path: str | Path,
    *,
    cancel_checker=None,
    log_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Embed subtitles as soft subtitles (mov_text for MP4).

    Args:
        video_path: Source video file.
        subtitle_path: SRT subtitle file (SRT recommended for softsub).
        output_path: Output video file path.
        cancel_checker: Optional callable returning True if cancelled.
        log_path: Optional path for FFmpeg log output.

    Returns:
        Dict with "success" (bool), "output_path" (str), "error" (str, optional).
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return {"success": False, "error": "FFmpeg not found"}

    ext = Path(subtitle_path).suffix.lower()
    if ext not in (".srt", ".ass", ".vtt"):
        return {"success": False, "error": f"Unsupported subtitle format: {ext}"}

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = out_path.with_name(f"{out_path.stem}.partial{out_path.suffix}")

    codec_map = {".srt": "mov_text", ".ass": "mov_text", ".vtt": "mov_text"}
    sub_codec = codec_map.get(ext, "mov_text")

    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
        "-i", escape_path(video_path),
        "-i", escape_path(subtitle_path),
        "-c:v", "copy",
        "-c:a", "copy",
        "-c:s", sub_codec,
        "-metadata:s:s:0", "language=und",
        "-disposition:s:0", "default",
        escape_path(partial_path),
    ]

    process = FFmpegProcess(cmd, log_path)
    process.start()

    try:
        success, stdout, stderr = process.wait(timeout=None, cancel_checker=cancel_checker)

        if process.is_cancelled() or stderr == "Cancelled":
            if partial_path.exists():
                partial_path.unlink(missing_ok=True)
            return {"success": False, "error": "Cancelled", "cancelled": True}

        if not success:
            if partial_path.exists():
                partial_path.unlink(missing_ok=True)
            return {"success": False, "error": f"FFmpeg failed: {stderr[:500]}"}

        if partial_path.exists():
            if out_path.exists():
                os.remove(str(out_path))
            os.rename(str(partial_path), str(out_path))

        return {
            "success": True,
            "output_path": str(out_path),
        }
    except Exception as e:
        if partial_path.exists():
            partial_path.unlink(missing_ok=True)
        return {"success": False, "error": str(e)}
