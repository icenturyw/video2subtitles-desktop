from __future__ import annotations

import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed
from pathlib import Path
from typing import List, Optional, Tuple

from process_utils import hidden_subprocess_kwargs

_MAX_DIRECT_SEGMENTS = 40


def _run_process(cmd: List[str], timeout: float, cancel_checker=None):
    if not cancel_checker:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            **hidden_subprocess_kwargs(),
        )

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **hidden_subprocess_kwargs(),
    )
    started = time.monotonic()
    while True:
        if cancel_checker():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            return subprocess.CompletedProcess(cmd, 130, "", "Cancelled")

        try:
            stdout, stderr = process.communicate(timeout=0.25)
            return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            if time.monotonic() - started > timeout:
                process.kill()
                process.wait(timeout=5)
                raise


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
            **hidden_subprocess_kwargs(),
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
            **hidden_subprocess_kwargs(),
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
    original_volume: float = 0.0,
    cancel_checker=None,
    log_path: Optional[Path] = None,
) -> dict:
    """Mix TTS audio segments onto the original video audio.
    
    Args:
        video_path: Source video file.
        tts_segments: List of (wav_path, start_time_seconds).
        output_path: Output video path with mixed audio.
        original_volume: Volume multiplier for original audio (0.0 disables it).
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
    tts_segments = [(Path(p), float(start)) for p, start in tts_segments if Path(p).exists()]
    if not tts_segments:
        return {"success": False, "error": "no existing TTS segment files"}

    if len(tts_segments) > _MAX_DIRECT_SEGMENTS:
        return _mix_audio_chunked(
            video_path=video_path,
            tts_segments=tts_segments,
            output_path=output_path,
            original_volume=original_volume,
            cancel_checker=cancel_checker,
            log_path=log_path,
        )

    # Build filter graph
    input_files = [str(video_path)]
    filter_parts = []
    audio_input_idx = 1
    has_original_audio = original_volume > 0 and _video_has_audio(video_path)

    for i, (wav_path, _) in enumerate(tts_segments):
        input_files.append(str(wav_path))
        delay_ms = int(tts_segments[i][1] * 1000)
        filter_parts.append(
            f"[{audio_input_idx}:a]adelay={delay_ms}|{delay_ms}[s{i}]"
        )
        audio_input_idx += 1

    all_labels = "".join(f"[s{i}]" for i in range(len(tts_segments)))
    filter_parts.append(f"{all_labels}amix=inputs={len(tts_segments)}:normalize=0[tts_mix]")
    if has_original_audio:
        filter_parts.append(f"[0:a]volume={original_volume:.2f}[orig]")
        filter_parts.append("[orig][tts_mix]amix=inputs=2:duration=longest[out]")
        audio_map = "[out]"
    else:
        audio_map = "[tts_mix]"

    filter_complex = ";".join(filter_parts)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"] + sum((["-i", f] for f in input_files), []) + [
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", audio_map,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ]

    try:
        result = _run_process(cmd, 600, cancel_checker=cancel_checker)
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[mix] cmd: {' '.join(cmd[:6])}...\n")
                f.write(f"[mix] stdout: {result.stdout[-500:]}\n")
                f.write(f"[mix] stderr: {result.stderr[-500:]}\n")

        if result.returncode != 0:
            if result.stderr == "Cancelled":
                return {"success": False, "cancelled": True}
            return {"success": False, "error": result.stderr[-1200:].strip()}

        if not output_path.exists():
            return {"success": False, "error": "output file not created"}

        return {"success": True}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "FFmpeg timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _mix_audio_chunked(
    video_path: Path,
    tts_segments: List[Tuple[Path, float]],
    output_path: Path,
    *,
    original_volume: float,
    cancel_checker=None,
    log_path: Optional[Path] = None,
) -> dict:
    """Mix many TTS clips without exceeding Windows command-line limits."""
    work_dir = output_path.parent / f"{output_path.stem}_audio_chunks"
    work_dir.mkdir(parents=True, exist_ok=True)

    chunk_outputs: List[Path] = []
    chunk_results: dict = {}
    try:
        tasks: List[tuple] = []
        for chunk_index, start in enumerate(range(0, len(tts_segments), _MAX_DIRECT_SEGMENTS), 1):
            chunk = tts_segments[start:start + _MAX_DIRECT_SEGMENTS]
            chunk_output = work_dir / f"tts_chunk_{chunk_index:04d}.wav"
            tasks.append((chunk_index, chunk, chunk_output))

        if len(tasks) > 1:
            with ThreadPoolExecutor(max_workers=min(2, len(tasks))) as executor:
                futures = {
                    executor.submit(
                        _mix_tts_chunk, chunk, chunk_output,
                        log_path=log_path, cancel_checker=cancel_checker,
                    ): chunk_index
                    for chunk_index, chunk, chunk_output in tasks
                }
                for future in _as_completed(futures):
                    if cancel_checker and cancel_checker():
                        for f in futures:
                            f.cancel()
                        return {"success": False, "cancelled": True}
                    chunk_index = futures[future]
                    result = future.result()
                    if not result.get("success"):
                        return result
                    chunk_results[chunk_index] = work_dir / f"tts_chunk_{chunk_index:04d}.wav"
            chunk_outputs = [chunk_results[idx] for idx in sorted(chunk_results)]
        else:
            for chunk_index, chunk, chunk_output in tasks:
                if cancel_checker and cancel_checker():
                    return {"success": False, "cancelled": True}
                result = _mix_tts_chunk(chunk, chunk_output, log_path=log_path, cancel_checker=cancel_checker)
                if not result.get("success"):
                    return result
                chunk_outputs.append(chunk_output)

        if cancel_checker and cancel_checker():
            return {"success": False, "cancelled": True}

        tts_mix = work_dir / "tts_mix.wav"
        result = _mix_chunk_outputs(chunk_outputs, tts_mix, log_path=log_path, cancel_checker=cancel_checker)
        if not result.get("success"):
            return result

        return _mux_tts_with_video(
            video_path=video_path,
            tts_audio=tts_mix,
            output_path=output_path,
            original_volume=original_volume,
            log_path=log_path,
            cancel_checker=cancel_checker,
        )
    finally:
        pass


def _mix_tts_chunk(
    tts_segments: List[Tuple[Path, float]],
    output_path: Path,
    *,
    log_path: Optional[Path] = None,
    cancel_checker=None,
) -> dict:
    input_files = [str(path) for path, _ in tts_segments]
    filter_parts = []
    for i, (_, start_sec) in enumerate(tts_segments):
        delay_ms = max(0, int(start_sec * 1000))
        filter_parts.append(f"[{i}:a]adelay={delay_ms}|{delay_ms}[s{i}]")
    all_labels = "".join(f"[s{i}]" for i in range(len(tts_segments)))
    filter_parts.append(f"{all_labels}amix=inputs={len(tts_segments)}:normalize=0[out]")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]
    for file in input_files:
        cmd.extend(["-i", file])
    cmd.extend([
        "-filter_complex", ";".join(filter_parts),
        "-map", "[out]",
        "-ac", "2",
        "-ar", "24000",
        str(output_path),
    ])
    return _run_ffmpeg(cmd, output_path, log_path=log_path, label="mix-chunk", cancel_checker=cancel_checker)


def _mix_chunk_outputs(
    chunk_outputs: List[Path],
    output_path: Path,
    *,
    log_path: Optional[Path] = None,
    cancel_checker=None,
) -> dict:
    if len(chunk_outputs) == 1:
        import shutil
        shutil.copy2(str(chunk_outputs[0]), str(output_path))
        return {"success": True}

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]
    for path in chunk_outputs:
        cmd.extend(["-i", str(path)])
    labels = "".join(f"[{i}:a]" for i in range(len(chunk_outputs)))
    cmd.extend([
        "-filter_complex", f"{labels}amix=inputs={len(chunk_outputs)}:normalize=0[out]",
        "-map", "[out]",
        "-ac", "2",
        "-ar", "24000",
        str(output_path),
    ])
    return _run_ffmpeg(cmd, output_path, log_path=log_path, label="mix-tts", cancel_checker=cancel_checker)


def _mux_tts_with_video(
    video_path: Path,
    tts_audio: Path,
    output_path: Path,
    *,
    original_volume: float,
    log_path: Optional[Path] = None,
    cancel_checker=None,
) -> dict:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(video_path),
        "-i", str(tts_audio),
    ]
    if original_volume > 0 and _video_has_audio(video_path):
        cmd.extend([
            "-filter_complex",
            f"[0:a]volume={original_volume:.2f}[orig];[orig][1:a]amix=inputs=2:duration=longest[out]",
            "-map", "0:v",
            "-map", "[out]",
        ])
    else:
        cmd.extend(["-map", "0:v", "-map", "1:a"])
    cmd.extend([
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ])
    return _run_ffmpeg(cmd, output_path, log_path=log_path, label="mux-video", cancel_checker=cancel_checker)


def _run_ffmpeg(cmd: List[str], output_path: Path, *, log_path: Optional[Path], label: str,
                cancel_checker=None) -> dict:
    result = _run_process(cmd, 1800, cancel_checker=cancel_checker)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{label}] cmd inputs={cmd.count('-i')} output={output_path}\n")
            f.write(f"[{label}] stderr: {result.stderr[-1200:]}\n")
    if result.returncode != 0:
        if result.stderr == "Cancelled":
            return {"success": False, "cancelled": True}
        return {"success": False, "error": result.stderr[-2000:].strip()}
    if not output_path.exists():
        return {"success": False, "error": "output file not created"}
    return {"success": True}


def _video_has_audio(video_path: Path) -> bool:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=30,
            **hidden_subprocess_kwargs(),
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def mix_simple_audio(
    video_path: Path,
    dubbed_audio: Path,
    output_path: Path,
    *,
    original_volume: float = 0.0,
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
        ]
        if original_volume > 0 and _video_has_audio(video_path):
            cmd.extend([
                "-filter_complex",
                f"[0:a]volume={original_volume:.2f}[orig];"
                f"[orig][1:a]amix=inputs=2:duration=longest[out]",
                "-map", "0:v",
                "-map", "[out]",
            ])
        else:
            cmd.extend(["-map", "0:v", "-map", "1:a"])
        cmd.extend([
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            str(output_path),
        ])
        result = _run_process(cmd, 600, cancel_checker=cancel_checker)
        if result.returncode != 0:
            if result.stderr == "Cancelled":
                return {"success": False, "cancelled": True}
            return {"success": False, "error": result.stderr[-1200:].strip()}
        return {"success": True} if output_path.exists() else {"success": False, "error": "output not created"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "FFmpeg timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}
