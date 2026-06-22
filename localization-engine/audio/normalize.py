from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from process_utils import hidden_subprocess_kwargs


def normalize_tts_audio(
    input_path: Path,
    output_path: Optional[Path] = None,
    sample_rate: int = 24000,
    channels: int = 1,
    normalize_loudness: bool = True,
    remove_silence: bool = True,
) -> Optional[Path]:
    """Normalize TTS audio to consistent format.

    Args:
        input_path: Source audio file.
        output_path: Destination path (default: overwrite input_path).
        sample_rate: Target sample rate in Hz (default 24000).
        channels: Target channel count (default 1 = mono).
        normalize_loudness: Apply loudnorm filter (default True).
        remove_silence: Remove leading/trailing silence (default True).

    Returns:
        Output path if successful, None on failure.
    """
    if not input_path.exists() or input_path.stat().st_size == 0:
        return None

    dst = output_path or input_path
    dst.parent.mkdir(parents=True, exist_ok=True)

    filters: list[str] = []
    if remove_silence:
        filters.append("silenceremove=start_periods=1:start_duration=0.05:start_threshold=-50dB")
        filters.append("silenceremove=stop_periods=1:stop_duration=0.05:stop_threshold=-50dB")
        filters.append("aformat=dblp,areverse")
        filters.append("silenceremove=start_periods=1:start_duration=0.05:start_threshold=-50dB")
        filters.append("silenceremove=stop_periods=1:stop_duration=0.05:stop_threshold=-50dB")
        filters.append("aformat=dblp,areverse")

    resample_filter = f"aresample={sample_rate}"
    if channels == 1:
        resample_filter += ":ocl=mono"
    elif channels == 2:
        resample_filter += ":ocl=stereo"
    filters.append(resample_filter)

    if normalize_loudness:
        filters.append("loudnorm=I=-16:LRA=11:TP=-1.5")

    try:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
            "-i", str(input_path),
            "-filter:a", ",".join(filters),
            "-c:a", "pcm_s16le",
            str(dst),
        ]
        subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=120,
            check=True,
            **hidden_subprocess_kwargs(),
        )
        return dst if dst.exists() else None
    except Exception:
        return None
