"""Progress tracking for pipeline jobs.

Provides a thread-safe progress store that the pipeline writes to
and the API reads from. Progress is monotonically non-decreasing
within a single run (no backward jumps).
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Optional


class ProgressEntry:
    """Current progress state for a single job."""

    __slots__ = ("job_id", "stage", "progress", "message", "updated_at")

    def __init__(self, job_id: str, stage: str = "prepare",
                 progress: int = 0, message: str = ""):
        self.job_id = job_id
        self.stage = stage
        self.progress = progress
        self.message = message
        self.updated_at = time.time()

    def to_dict(self) -> Dict[str, object]:
        return {
            "job_id": self.job_id,
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "updated_at": self.updated_at,
        }


class ProgressTracker:
    """Thread-safe progress tracking for concurrent pipeline jobs."""

    def __init__(self, event_sink: Callable[[str, str, int, str], object] | None = None):
        self._entries: Dict[str, ProgressEntry] = {}
        self._lock = threading.Lock()
        self._event_sink = event_sink

    def update(self, job_id: str, stage: str, progress: int,
               message: str = "") -> None:
        """Update progress for a job.

        Progress is clamped to [0, 100] and will not decrease
        within the same stage (monotonic guarantee).
        """
        progress = max(0, min(100, progress))
        with self._lock:
            entry = self._entries.get(job_id)
            if entry is None:
                self._entries[job_id] = ProgressEntry(
                    job_id, stage, progress, message
                )
            else:
                # Monotonic: don't go backward within same stage
                if stage == entry.stage and progress < entry.progress:
                    progress = entry.progress
                entry.stage = stage
                entry.progress = progress
                entry.message = message
                entry.updated_at = time.time()
        if self._event_sink:
            try:
                self._event_sink(job_id, stage, progress, message)
            except Exception:
                pass

    def get(self, job_id: str) -> Optional[ProgressEntry]:
        """Get current progress for a job."""
        with self._lock:
            entry = self._entries.get(job_id)
            if entry is not None:
                # Return a snapshot (ProgressEntry is mutable, so copy values)
                return ProgressEntry(
                    entry.job_id, entry.stage,
                    entry.progress, entry.message,
                )
            return None

    def remove(self, job_id: str) -> None:
        """Remove progress entry for a completed/removed job."""
        with self._lock:
            self._entries.pop(job_id, None)

    def reset(self, job_id: str) -> None:
        """Reset progress for a job (e.g. on retry)."""
        with self._lock:
            self._entries.pop(job_id, None)
