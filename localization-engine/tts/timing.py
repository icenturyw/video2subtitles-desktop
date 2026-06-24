from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from process_utils import hidden_subprocess_kwargs


MAX_SPEED = 2.0
MAX_SLOW = 0.75


def adjust_timing(
    input_audio: Path,
    output_audio: Path,
    actual_duration: float,
    target_duration: float,
) -> Tuple[float, str, float]:
    """Apply atempo/trim to fit the target duration.
    
    Returns:
        (adjusted_duration, warning, speed_ratio) where speed_ratio is
        the applied tempo (1.0 = no change).
    """
    if actual_duration <= 0 or target_duration <= 0:
        return actual_duration, "zero duration", 1.0

    required_speed = actual_duration / target_duration

    if required_speed > MAX_SPEED:
        speed = MAX_SPEED
        warning = (
            f"timing_warning: too long ({required_speed:.2f}x), "
            f"sped up {MAX_SPEED:.2f}x and trimmed"
        )
    elif required_speed < MAX_SLOW:
        speed = MAX_SLOW
        warning = (
            f"timing_warning: too short ({required_speed:.2f}x), "
            f"slowed to {MAX_SLOW:.2f}x"
        )
    else:
        speed = required_speed
        warning = ""

    filters = _atempo_chain(speed)
    if required_speed > MAX_SPEED:
        filters.extend([f"atrim=0:{target_duration:.3f}", "asetpts=N/SR/TB"])

    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-i", str(input_audio),
                "-filter:a", ",".join(filters),
                str(output_audio),
            ],
            capture_output=True, text=True, timeout=60,
            check=True,
            **hidden_subprocess_kwargs(),
        )
        if required_speed > MAX_SPEED:
            return target_duration, warning, speed
        adjusted = actual_duration / speed if speed else actual_duration
        return adjusted, warning, speed
    except Exception as e:
        trimmed = _trim_audio(input_audio, output_audio, target_duration)
        if trimmed:
            return target_duration, f"atempo failed, trimmed instead: {e}", 1.0
        return actual_duration, f"atempo failed: {e}", 1.0


def _atempo_chain(speed: float) -> List[str]:
    """Build ffmpeg atempo filters using conservative 0.5..2.0 factors."""
    factors = []
    remaining = max(0.01, float(speed))
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return [f"atempo={factor:.3f}" for factor in factors]


def _trim_audio(input_audio: Path, output_audio: Path, target_duration: float) -> bool:
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-i", str(input_audio),
                "-t", f"{target_duration:.3f}",
                "-c:a", "pcm_s16le",
                str(output_audio),
            ],
            capture_output=True, text=True, timeout=60,
            check=True,
            **hidden_subprocess_kwargs(),
        )
        return output_audio.exists()
    except Exception:
        return False


def extract_audio_window(
    input_audio: Path,
    output_audio: Path,
    start_offset: float,
    duration: float,
) -> bool:
    """Extract a time window from an audio file."""
    if duration <= 0:
        return False

    output_audio.parent.mkdir(parents=True, exist_ok=True)
    start_offset = max(0.0, float(start_offset))
    duration = max(0.0, float(duration))
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-i", str(input_audio),
                "-filter:a",
                f"atrim=start={start_offset:.3f}:duration={duration:.3f},asetpts=N/SR/TB",
                "-c:a", "pcm_s16le",
                str(output_audio),
            ],
            capture_output=True, text=True, timeout=60,
            check=True,
            **hidden_subprocess_kwargs(),
        )
        return output_audio.exists() and output_audio.stat().st_size > 0
    except Exception:
        output_audio.unlink(missing_ok=True)
        return False


def build_concat_file(segments: List[Tuple[Path, float]], output: Path) -> str:
    """Build the filter_complex string for timed audio segments."""
    inputs = []
    filter_parts = []

    for i, (audio_path, start_sec) in enumerate(segments):
        inputs.append(str(audio_path))
        delay_ms = int(start_sec * 1000)
        filter_parts.append(
            f"[{i}:a]adelay={delay_ms}|{delay_ms}[s{i}]"
        )

    all_labels = "".join(f"[s{i}]" for i in range(len(segments)))
    filter_parts.append(
        f"{all_labels}amix=inputs={len(segments)}:normalize=0[tts]"
    )

    return ";".join(filter_parts)


def save_timing_report(speed_ratios: Dict[int, float],
                       output_path: Path) -> None:
    """Record per-sentence speed ratios for diagnostics."""
    data = {
        "max_speed": MAX_SPEED,
        "max_slow": MAX_SLOW,
        "segments": speed_ratios,
    }
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
