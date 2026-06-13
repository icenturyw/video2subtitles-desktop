from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple


MAX_SPEED = 1.25
MAX_SLOW = 0.90


def adjust_timing(
    input_audio: Path,
    output_audio: Path,
    actual_duration: float,
    target_duration: float,
) -> Tuple[float, str, float]:
    """Apply atempo to match target duration.
    
    Returns:
        (adjusted_duration, warning, speed_ratio) where speed_ratio is
        the applied tempo (1.0 = no change).
    """
    if actual_duration <= 0 or target_duration <= 0:
        return actual_duration, "zero duration", 1.0

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
            return target_duration, "", speed
        except Exception as e:
            return actual_duration, f"atempo failed: {e}", 1.0

    if ratio > MAX_SPEED:
        return actual_duration, f"timing_warning: too long ({ratio:.2f}x)", 1.0
    return actual_duration, f"timing_warning: too short ({ratio:.2f}x)", 1.0


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
