"""Cross-platform runtime detection with no mandatory GPU dependency."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from .gpu import GPUMetrics, GPUMonitor, create_gpu_monitor

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - exercised through explicit monkeypatches
    psutil = None


@dataclass(frozen=True)
class CommandCapability:
    available: bool
    path: str = ""
    version: str = ""
    error_code: str = ""
    error_detail: str = ""


@dataclass(frozen=True)
class RuntimeSnapshot:
    timestamp: float
    cpu_percent: float
    memory_used_bytes: int
    memory_total_bytes: int
    memory_percent: float
    disk_free_bytes: int
    disk_total_bytes: int
    gpus: tuple[GPUMetrics, ...] = ()
    loaded_models: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["gpus"] = [gpu.to_dict() for gpu in self.gpus]
        data["loaded_models"] = list(self.loaded_models)
        return data


@dataclass(frozen=True)
class RuntimeCapabilities:
    os_name: str
    os_version: str
    architecture: str
    cpu_count: int
    memory_total_bytes: int
    workspace: str
    disk_total_bytes: int
    disk_free_bytes: int
    ffmpeg: CommandCapability
    ffprobe: CommandCapability
    cuda_available: bool
    gpu_monitor: str
    gpus: tuple[GPUMetrics, ...] = field(default_factory=tuple)
    gpu_error: str = ""

    @classmethod
    def detect(
        cls,
        workspace: str | Path,
        *,
        gpu_monitor: GPUMonitor | None = None,
        command_timeout: float = 3.0,
    ) -> "RuntimeCapabilities":
        root = Path(workspace).expanduser().resolve()
        disk = shutil.disk_usage(root if root.exists() else root.parent)
        memory_total = _memory_values()[1]
        monitor = gpu_monitor or create_gpu_monitor(command_timeout)
        gpus = tuple(monitor.sample())
        return cls(
            os_name=platform.system(),
            os_version=platform.version(),
            architecture=platform.machine(),
            cpu_count=os.cpu_count() or 1,
            memory_total_bytes=memory_total,
            workspace=str(root),
            disk_total_bytes=disk.total,
            disk_free_bytes=disk.free,
            ffmpeg=probe_command("ffmpeg", ("-version",), command_timeout),
            ffprobe=probe_command("ffprobe", ("-version",), command_timeout),
            cuda_available=bool(gpus),
            gpu_monitor=type(monitor).__name__,
            gpus=gpus,
            gpu_error=monitor.last_error,
        )

    def to_dict(self) -> dict:
        data = asdict(self)
        data["gpus"] = [gpu.to_dict() for gpu in self.gpus]
        return data


def probe_command(name: str, args: Iterable[str], timeout_seconds: float = 3.0) -> CommandCapability:
    path = shutil.which(name)
    if not path:
        return CommandCapability(False, error_code=f"{name.upper()}_NOT_FOUND")
    try:
        result = subprocess.run(
            [path, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(0.1, float(timeout_seconds)),
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return CommandCapability(
            False, path=path, error_code=f"{name.upper()}_QUERY_TIMEOUT",
            error_detail=f"Command exceeded {timeout_seconds:g}s",
        )
    except OSError as exc:
        return CommandCapability(False, path=path, error_code=f"{name.upper()}_QUERY_FAILED", error_detail=str(exc))
    output = (result.stdout or result.stderr or "").splitlines()
    return CommandCapability(
        result.returncode == 0,
        path=path,
        version=output[0].strip()[:300] if output else "",
        error_code="" if result.returncode == 0 else f"{name.upper()}_QUERY_FAILED",
        error_detail="" if result.returncode == 0 else (result.stderr or result.stdout or "")[:1000],
    )


def _memory_values() -> tuple[int, int, float]:
    if psutil is not None:
        memory = psutil.virtual_memory()
        return int(memory.used), int(memory.total), float(memory.percent)
    return 0, 0, 0.0


def collect_runtime_snapshot(
    workspace: str | Path,
    *,
    gpu_monitor: GPUMonitor | None = None,
    loaded_models: Iterable[dict] = (),
) -> RuntimeSnapshot:
    root = Path(workspace).expanduser().resolve()
    disk = shutil.disk_usage(root if root.exists() else root.parent)
    used, total, percent = _memory_values()
    cpu = float(psutil.cpu_percent(interval=None)) if psutil is not None else 0.0
    monitor = gpu_monitor or create_gpu_monitor()
    return RuntimeSnapshot(
        timestamp=time.time(),
        cpu_percent=cpu,
        memory_used_bytes=used,
        memory_total_bytes=total,
        memory_percent=percent,
        disk_free_bytes=disk.free,
        disk_total_bytes=disk.total,
        gpus=tuple(monitor.sample()),
        loaded_models=tuple(dict(model) for model in loaded_models),
    )
