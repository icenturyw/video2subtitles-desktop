"""Adaptive in-memory runtime sampling."""
from __future__ import annotations

import statistics
import threading
from collections import deque
from pathlib import Path
from typing import Callable, Deque, Iterable, Optional

from .capabilities import RuntimeSnapshot, collect_runtime_snapshot
from .gpu import GPUMonitor, create_gpu_monitor


class RuntimeMonitor:
    def __init__(
        self,
        workspace: str | Path,
        *,
        active_task_checker: Callable[[], bool] | None = None,
        loaded_models_provider: Callable[[], Iterable[dict]] | None = None,
        gpu_monitor: GPUMonitor | None = None,
        active_interval: float = 2.0,
        idle_interval: float = 10.0,
        max_samples: int = 300,
        sampler: Callable[..., RuntimeSnapshot] = collect_runtime_snapshot,
    ) -> None:
        self.workspace = Path(workspace)
        self.active_task_checker = active_task_checker or (lambda: False)
        self.loaded_models_provider = loaded_models_provider or (lambda: ())
        self.gpu_monitor = gpu_monitor or create_gpu_monitor()
        self.active_interval = max(0.05, float(active_interval))
        self.idle_interval = max(self.active_interval, float(idle_interval))
        self._samples: Deque[RuntimeSnapshot] = deque(maxlen=max(2, int(max_samples)))
        self._sampler = sampler
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="runtime-monitor", daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))
        return not bool(thread and thread.is_alive())

    def sample_once(self) -> RuntimeSnapshot:
        sample = self._sampler(
            self.workspace,
            gpu_monitor=self.gpu_monitor,
            loaded_models=self.loaded_models_provider(),
        )
        with self._lock:
            self._samples.append(sample)
        return sample

    def latest(self) -> RuntimeSnapshot | None:
        with self._lock:
            return self._samples[-1] if self._samples else None

    def samples(self) -> tuple[RuntimeSnapshot, ...]:
        with self._lock:
            return tuple(self._samples)

    def summary(self, since: float | None = None) -> dict:
        samples = [sample for sample in self.samples() if since is None or sample.timestamp >= since]
        if not samples:
            return {"sample_count": 0}
        cpu = [sample.cpu_percent for sample in samples]
        memory = [sample.memory_percent for sample in samples]
        gpu = [metric.utilization_percent for sample in samples for metric in sample.gpus]
        vram = [metric.memory_used_mb for sample in samples for metric in sample.gpus]
        return {
            "sample_count": len(samples),
            "started_at": samples[0].timestamp,
            "ended_at": samples[-1].timestamp,
            "cpu_percent": _stats(cpu),
            "memory_percent": _stats(memory),
            "gpu_percent": _stats(gpu),
            "vram_used_mb": _stats(vram),
            "minimum_disk_free_bytes": min(sample.disk_free_bytes for sample in samples),
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception:
                # Monitoring must never terminate the application.
                pass
            try:
                active = bool(self.active_task_checker())
            except Exception:
                active = False
            self._stop.wait(self.active_interval if active else self.idle_interval)


def _stats(values: list[float | int]) -> dict:
    if not values:
        return {"min": 0.0, "max": 0.0, "average": 0.0}
    return {
        "min": min(values),
        "max": max(values),
        "average": round(float(statistics.fmean(values)), 2),
    }
