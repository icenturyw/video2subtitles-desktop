"""Optional GPU monitoring with a safe no-GPU fallback."""
from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import asdict, dataclass
from typing import List, Protocol


@dataclass(frozen=True)
class GPUMetrics:
    index: int
    name: str
    utilization_percent: float
    memory_used_mb: int
    memory_total_mb: int
    temperature_c: float | None = None

    @property
    def memory_free_mb(self) -> int:
        return max(0, self.memory_total_mb - self.memory_used_mb)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["memory_free_mb"] = self.memory_free_mb
        return data


class GPUMonitor(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def last_error(self) -> str: ...

    def sample(self) -> List[GPUMetrics]: ...


class NullGPUMonitor:
    """Stable implementation for CPU-only and unsupported systems."""

    def __init__(self, reason: str = "No supported GPU monitor detected") -> None:
        self._reason = reason

    @property
    def available(self) -> bool:
        return False

    @property
    def last_error(self) -> str:
        return self._reason

    def sample(self) -> List[GPUMetrics]:
        return []


class NvidiaSmiMonitor:
    """Query NVIDIA metrics without importing CUDA or NVML packages."""

    QUERY_FIELDS = (
        "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu"
    )

    def __init__(self, executable: str = "nvidia-smi", timeout_seconds: float = 3.0) -> None:
        self.executable = executable
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self._last_error = ""
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def sample(self) -> List[GPUMetrics]:
        if not self.available:
            self._set_error("nvidia-smi was not found")
            return []
        command = [
            self.executable,
            f"--query-gpu={self.QUERY_FIELDS}",
            "--format=csv,noheader,nounits",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0:
                self._set_error((completed.stderr or completed.stdout or "query failed").strip())
                return []
            metrics = [self._parse_line(line) for line in completed.stdout.splitlines() if line.strip()]
            self._set_error("")
            return metrics
        except subprocess.TimeoutExpired:
            self._set_error(f"nvidia-smi timed out after {self.timeout_seconds:g}s")
        except (OSError, ValueError) as exc:
            self._set_error(str(exc))
        return []

    @staticmethod
    def _parse_line(line: str) -> GPUMetrics:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 6:
            raise ValueError(f"Unexpected nvidia-smi output: {line!r}")
        return GPUMetrics(
            index=int(parts[0]),
            name=parts[1],
            utilization_percent=float(parts[2]),
            memory_used_mb=int(float(parts[3])),
            memory_total_mb=int(float(parts[4])),
            temperature_c=None if parts[5] in {"", "N/A", "[N/A]"} else float(parts[5]),
        )

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._last_error = str(message)[:1000]


def create_gpu_monitor(timeout_seconds: float = 3.0) -> GPUMonitor:
    """Return NVIDIA monitoring when available, otherwise a harmless fallback."""
    if shutil.which("nvidia-smi"):
        return NvidiaSmiMonitor(timeout_seconds=timeout_seconds)
    return NullGPUMonitor()
