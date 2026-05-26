"""GPU/device selection helpers for faster-whisper.

The app defaults to auto mode: prefer CUDA when an NVIDIA GPU is visible, then
fall back to CPU.  This keeps RTX machines fast without breaking CPU-only users.
"""
from __future__ import annotations

import os
import subprocess
from typing import Dict, Tuple


SUPPORTED_DEVICES = ["auto", "cuda", "cpu"]
SUPPORTED_COMPUTE_TYPES = ["auto", "float16", "int8_float16", "int8", "float32"]


def has_nvidia_gpu() -> bool:
    """Return True when nvidia-smi can see at least one GPU."""
    try:
        kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "text": True,
            "timeout": 2,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(["nvidia-smi", "-L"], **kwargs)
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def gpu_name() -> str:
    try:
        kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "text": True,
            "timeout": 2,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            **kwargs,
        )
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0].strip()
    except Exception:
        pass
    return ""


def clean_device(value: str) -> str:
    value = (value or "auto").strip().lower()
    return value if value in SUPPORTED_DEVICES else "auto"


def clean_compute_type(value: str) -> str:
    value = (value or "auto").strip().lower()
    return value if value in SUPPORTED_COMPUTE_TYPES else "auto"


def resolve_device_and_compute(device: str = "auto", compute_type: str = "auto") -> Tuple[str, str]:
    """Resolve saved UI settings into faster-whisper arguments."""
    requested_device = clean_device(device)
    requested_compute = clean_compute_type(compute_type)

    resolved_device = requested_device
    if requested_device == "auto":
        resolved_device = "cuda" if has_nvidia_gpu() else "cpu"

    resolved_compute = requested_compute
    if requested_compute == "auto":
        resolved_compute = "float16" if resolved_device == "cuda" else "int8"

    return resolved_device, resolved_compute


def device_status() -> Dict[str, str]:
    gpu = gpu_name()
    auto_device, auto_compute = resolve_device_and_compute("auto", "auto")
    return {
        "has_nvidia_gpu": "true" if bool(gpu) or has_nvidia_gpu() else "false",
        "gpu_name": gpu,
        "auto_device": auto_device,
        "auto_compute_type": auto_compute,
    }
