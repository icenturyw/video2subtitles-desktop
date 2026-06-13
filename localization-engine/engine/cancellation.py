"""Cancellation token for pipeline jobs.

Provides a thread-safe mechanism for cooperative cancellation.
Long-running operations (translation batches, TTS sentences, FFmpeg subprocesses)
should check the token periodically.
"""
from __future__ import annotations

import threading
from typing import Dict


class CancellationToken:
    """Thread-safe cancellation flag for a single job."""

    def __init__(self):
        self._cancelled = False
        self._lock = threading.Lock()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def reset(self) -> None:
        with self._lock:
            self._cancelled = False


class CancellationRegistry:
    """Registry of cancellation tokens keyed by job_id.

    Thread-safe for concurrent access from multiple request handlers
    and pipeline workers.
    """

    def __init__(self):
        self._tokens: Dict[str, CancellationToken] = {}
        self._lock = threading.Lock()

    def register(self, job_id: str) -> CancellationToken:
        """Create and register a new token for a job."""
        token = CancellationToken()
        with self._lock:
            self._tokens[job_id] = token
        return token

    def cancel(self, job_id: str) -> bool:
        """Request cancellation for a job. Returns True if the job was found."""
        with self._lock:
            token = self._tokens.get(job_id)
        if token is not None:
            token.cancel()
            return True
        return False

    def get(self, job_id: str) -> CancellationToken | None:
        """Get the cancellation token for a job."""
        with self._lock:
            return self._tokens.get(job_id)

    def remove(self, job_id: str) -> None:
        """Remove a completed/cancelled job's token."""
        with self._lock:
            self._tokens.pop(job_id, None)

    def is_cancelled(self, job_id: str) -> bool:
        """Check if a job has been cancelled."""
        with self._lock:
            token = self._tokens.get(job_id)
        if token is None:
            return False
        return token.is_cancelled()
