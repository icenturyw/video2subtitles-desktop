"""Thread-safe in-memory task store with JSON persistence.

Tasks survive server restarts: completed/failed tasks are reloaded from disk,
while tasks that were 'running' or 'pending' at crash time are marked
'interrupted' on recovery.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("engine.task_store")

# Valid task statuses
VALID_STATUSES = frozenset({
    "pending", "running", "completed", "error", "cancelled", "interrupted",
})

# Statuses considered "in-flight" — on restart these become interrupted
_INFLIGHT = frozenset({"pending", "running"})


class TaskRecord:
    """Internal representation of a task in the store."""

    __slots__ = (
        "job_id", "status", "stage", "progress", "message",
        "detected_language", "artifacts", "error_code", "error_detail",
        "request_payload", "created_at", "updated_at",
    )

    def __init__(self, job_id: str, request_payload: Optional[Dict[str, Any]] = None):
        self.job_id = job_id
        self.status = "pending"
        self.stage = "prepare"
        self.progress = 0
        self.message = ""
        self.detected_language = ""
        self.artifacts: List[Dict[str, Any]] = []
        self.error_code: Optional[str] = None
        self.error_detail: Optional[str] = None
        self.request_payload = request_payload or {}
        self.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.updated_at = self.created_at

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "job_id": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "detected_language": self.detected_language,
            "artifacts": self.artifacts,
            "request_payload": self.request_payload,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.error_code:
            d["error_code"] = self.error_code
        if self.error_detail:
            d["error_detail"] = self.error_detail
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskRecord":
        rec = cls(
            job_id=data.get("job_id", ""),
            request_payload=data.get("request_payload", {}),
        )
        rec.status = data.get("status", "pending")
        rec.stage = data.get("stage", "prepare")
        rec.progress = int(data.get("progress", 0))
        rec.message = data.get("message", "")
        rec.detected_language = data.get("detected_language", "")
        rec.artifacts = data.get("artifacts", [])
        rec.error_code = data.get("error_code")
        rec.error_detail = data.get("error_detail")
        rec.created_at = data.get("created_at", "")
        rec.updated_at = data.get("updated_at", "")
        return rec

    def to_api_dict(self) -> Dict[str, Any]:
        """Return dict suitable for API responses (excludes request_payload)."""
        d: Dict[str, Any] = {
            "job_id": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "detected_language": self.detected_language,
            "artifacts": self.artifacts,
        }
        if self.error_code:
            d["error_code"] = self.error_code
        if self.error_detail:
            d["error_detail"] = self.error_detail
        return d


class TaskStore:
    """Thread-safe task storage with JSON file persistence.

    Data directory layout:
        data_dir/
            tasks.json       -- all task records
    """

    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._tasks: Dict[str, TaskRecord] = {}
        self._lock = threading.RLock()
        self._persist_path = self._data_dir / "tasks.json"
        self._load()
        self._recover_interrupted()

    # -- Persistence --------------------------------------------------------

    def _load(self) -> None:
        """Load tasks from disk."""
        if not self._persist_path.exists():
            return
        try:
            raw = self._persist_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            for item in data.get("tasks", []):
                rec = TaskRecord.from_dict(item)
                if rec.job_id:
                    self._tasks[rec.job_id] = rec
            logger.info("Loaded %d tasks from %s", len(self._tasks), self._persist_path)
        except Exception as exc:
            logger.warning("Failed to load tasks from %s: %s", self._persist_path, exc)

    def _save(self) -> None:
        """Persist current tasks to disk. Caller must hold _lock."""
        payload = {
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tasks": [rec.to_dict() for rec in self._tasks.values()],
        }
        try:
            # Atomic-ish write: write to tmp then rename
            tmp = self._persist_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if self._persist_path.exists():
                import os
                os.replace(str(tmp), str(self._persist_path))
            else:
                tmp.rename(self._persist_path)
        except Exception as exc:
            logger.error("Failed to persist tasks: %s", exc)

    def _recover_interrupted(self) -> None:
        """Mark in-flight tasks as interrupted after server restart."""
        recovered = 0
        with self._lock:
            for rec in self._tasks.values():
                if rec.status in _INFLIGHT:
                    rec.status = "interrupted"
                    rec.message = "服务重启，任务中断"
                    rec.error_code = "TASK_INTERRUPTED"
                    rec.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
                    recovered += 1
            if recovered:
                self._save()
                logger.info("Recovered %d interrupted tasks", recovered)

    # -- CRUD ---------------------------------------------------------------

    def create(self, job_id: str, request_payload: Optional[Dict[str, Any]] = None) -> TaskRecord:
        """Create a new task record."""
        rec = TaskRecord(job_id, request_payload)
        with self._lock:
            self._tasks[job_id] = rec
            self._save()
        return rec

    def get(self, job_id: str) -> Optional[TaskRecord]:
        """Get a task record by job_id."""
        with self._lock:
            return self._tasks.get(job_id)

    def exists(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._tasks

    def list_all(self) -> List[TaskRecord]:
        """List all tasks, newest first."""
        with self._lock:
            return sorted(
                self._tasks.values(),
                key=lambda r: r.created_at,
                reverse=True,
            )

    def update(self, job_id: str, **kwargs: Any) -> Optional[TaskRecord]:
        """Update fields on a task record and persist."""
        with self._lock:
            rec = self._tasks.get(job_id)
            if rec is None:
                return None
            for key, value in kwargs.items():
                if hasattr(rec, key) and key not in ("job_id", "created_at"):
                    setattr(rec, key, value)
            rec.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._save()
            return rec

    def add_artifact(self, job_id: str, artifact: Dict[str, Any]) -> Optional[TaskRecord]:
        """Append an artifact to a task's artifact list."""
        with self._lock:
            rec = self._tasks.get(job_id)
            if rec is None:
                return None
            rec.artifacts.append(artifact)
            rec.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._save()
            return rec

    def delete(self, job_id: str) -> bool:
        """Delete a task record."""
        with self._lock:
            removed = self._tasks.pop(job_id, None)
            if removed is not None:
                self._save()
                return True
            return False
