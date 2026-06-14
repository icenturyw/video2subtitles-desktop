from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional, Tuple


def extract_audio(video_path: Path, output_path: Path) -> bool:
    """Extract original audio from video as WAV."""
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(video_path),
                "-vn", "-acodec", "pcm_s16le",
                "-ar", "24000", "-ac", "1",
                str(output_path),
            ],
            capture_output=True, text=True, timeout=300,
            check=True,
        )
        return output_path.exists()
    except Exception:
        return False


def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0


def mix_audio(
    video_path: Path,
    tts_segments: List[Tuple[Path, float]],
    output_path: Path,
    *,
    original_volume: float = 0.3,
    cancel_checker=None,
    log_path: Optional[Path] = None,
) -> dict:
    """Mix TTS audio segments onto the original video audio.
    
    Args:
        video_path: Source video file.
        tts_segments: List of (wav_path, start_time_seconds).
        output_path: Output video path with mixed audio.
        original_volume: Volume multiplier for original audio (0.0-1.0).
        cancel_checker: Optional callable returning True if cancelled.
        log_path: Optional path for FFmpeg log output.
    
    Returns:
        Dict with success/error keys.
    """
    if cancel_checker and cancel_checker():
        return {"success": False, "cancelled": True}

    if not tts_segments:
        return {"success": False, "error": "no TTS segments"}

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build filter graph
    input_files = [str(video_path)]
    filter_parts = []
    audio_input_idx = 1

    for i, (wav_path, _) in enumerate(tts_segments):
        input_files.append(str(wav_path))
        delay_ms = int(tts_segments[i][1] * 1000)
        filter_parts.append(
            f"[{audio_input_idx}:a]adelay={delay_ms}|{delay_ms}[s{i}]"
        )
        audio_input_idx += 1

    all_labels = "".join(f"[s{i}]" for i in range(len(tts_segments)))
    filter_parts.append(f"{all_labels}amix=inputs={len(tts_segments)}:normalize=0[tts_mix]")
    filter_parts.append(f"[0:a]volume={original_volume:.2f}[orig]")
    filter_parts.append("[orig][tts_mix]amix=inputs=2:duration=first[out]")

    filter_complex = ";".join(filter_parts)
    cmd = ["ffmpeg", "-y"] + sum((["-i", f] for f in input_files), []) + [
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[out]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=600,
        )
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[mix] cmd: {' '.join(cmd[:6])}...\n")
                f.write(f"[mix] stdout: {result.stdout[-500:]}\n")
                f.write(f"[mix] stderr: {result.stderr[-500:]}\n")

        if result.returncode != 0:
            return {"success": False, "error": result.stderr[-1200:].strip()}

        if not output_path.exists():
            return {"success": False, "error": "output file not created"}

        return {"success": True}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "FFmpeg timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def mix_simple_audio(
    video_path: Path,
    dubbed_audio: Path,
    output_path: Path,
    *,
    original_volume: float = 0.3,
    cancel_checker=None,
) -> dict:
    """Simpler mix: overlay a single dubbed audio track onto video.
    
    This is used when TTS segments have already been concatenated into one file.
    """
    if cancel_checker and cancel_checker():
        return {"success": False, "cancelled": True}

    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(dubbed_audio),
            "-filter_complex",
            f"[0:a]volume={original_volume:.2f}[orig];"
            f"[orig][1:a]amix=inputs=2:duration=first[out]",
            "-map", "0:v",
            "-map", "[out]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=True)
        return {"success": True} if output_path.exists() else {"success": False, "error": "output not created"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "FFmpeg timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}
