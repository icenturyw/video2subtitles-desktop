from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple


MAX_SPEED = 1.25
MAX_SLOW = 0.90


def adjust_timing(
    input_audio: Path,
    output_audio: Path,
    actual_duration: float,
    target_duration: float,
) -> Tuple[float, str]:
    """Apply atempo to match target duration.
    
    Returns:
        (adjusted_duration, warning) where warning is empty if OK.
    """
    if actual_duration <= 0 or target_duration <= 0:
        return actual_duration, "zero duration"

    ratio = actual_duration / target_duration

    if MAX_SLOW <= ratio <= MAX_SPEED:
        speed = 1.0 / ratio
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(input_audio),
                    "-filter:a", f"atempo={speed:.3f}",
                    str(output_audio),
                ],
                capture_output=True, text=True, timeout=60,
                check=True,
            )
            return target_duration, ""
        except Exception as e:
            return actual_duration, f"atempo failed: {e}"

    if ratio > MAX_SPEED:
        return actual_duration, f"timing_warning: too long ({ratio:.2f}x), max {MAX_SPEED:.2f}x"
    return actual_duration, f"timing_warning: too short ({ratio:.2f}x), min {MAX_SLOW:.2f}x"


def build_concat_file(segments: List[Tuple[Path, float]], output: Path) -> str:
    """Build a concat demuxer file from timed audio segments.
    
    Args:
        segments: List of (audio_path, start_time_seconds) for each segment.
        output: Output path for the concatenated audio.
    
    Returns:
        The filter_complex string to use.
    """
    inputs = []
    filter_parts = []

    for i, (audio_path, start_sec) in enumerate(segments):
        inputs.append(str(audio_path))
        delay_ms = int(start_sec * 1000)
        filter_parts.append(f"[{i}:a]adelay={delay_ms}|{delay_ms}[s{i}]")

    all_labels = "".join(f"[s{i}]" for i in range(len(segments)))
    filter_parts.append(f"{all_labels}amix=inputs={len(segments)}:normalize=0[tts]")

    return ";".join(filter_parts)
