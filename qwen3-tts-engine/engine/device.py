from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger("engine.device")


def cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def get_gpu_info() -> Optional[Dict]:
    if not cuda_available():
        return None
    try:
        import torch
        props = torch.cuda.get_device_properties(0)
        return {
            "name": props.name,
            "total_memory_mb": round(props.total_memory / 1024 / 1024),
            "compute_capability": f"{props.major}.{props.minor}",
        }
    except Exception as e:
        logger.warning("Failed to get GPU info: %s", e)
        return None


def get_free_memory_mb() -> Optional[int]:
    if not cuda_available():
        return None
    try:
        import torch
        free, _ = torch.cuda.mem_get_info(0)
        return round(free / 1024 / 1024)
    except Exception:
        return None


def get_optimal_device() -> Tuple[str, str]:
    device = "cpu"
    dtype = "float32"
    if cuda_available():
        device = "cuda"
        free_mb = get_free_memory_mb()
        if free_mb and free_mb > 4096:
            dtype = "bfloat16"
        else:
            dtype = "float16"
    return device, dtype


def detect_device() -> Dict:
    info = {"device": "cpu", "dtype": "float32", "flash_attention": False}
    if cuda_available():
        from engine.device import get_gpu_info, get_free_memory_mb
        gpu = get_gpu_info()
        free_mb = get_free_memory_mb()
        info["device"] = "cuda"
        info["gpu"] = gpu
        info["free_memory_mb"] = free_mb
        if free_mb and free_mb > 4096:
            info["dtype"] = "bfloat16"
        else:
            info["dtype"] = "float16"
    try:
        import importlib.util
        flash_spec = importlib.util.find_spec("flash_attn")
        info["flash_attention"] = flash_spec is not None
    except Exception:
        pass
    return info
