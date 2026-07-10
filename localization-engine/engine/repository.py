"""Persistence contracts shared by the API, pipeline, and storage backends."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    """Return a stable, lexicographically sortable UTC timestamp."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class TaskRecord:
    """Backend-neutral task representation kept compatible with TaskStore."""

    __slots__ = (
        "job_id", "status", "stage", "progress", "message",
        "detected_language", "artifacts", "error_code", "error_detail",
        "request_payload", "created_at", "updated_at", "version",
    )

    def __init__(
        self,
        job_id: str,
        request_payload: Optional[Dict[str, Any]] = None,
        *,
        version: int = 1,
    ) -> None:
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
        self.created_at = utc_now()
        self.updated_at = self.created_at
        self.version = max(1, int(version))

    @property
    def current_stage(self) -> str:
        return self.stage

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "job_id": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "current_stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "detected_language": self.detected_language,
            "artifacts": list(self.artifacts),
            "request_payload": dict(self.request_payload),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }
        if self.error_code:
            data["error_code"] = self.error_code
        if self.error_detail:
            data["error_detail"] = self.error_detail
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskRecord":
        rec = cls(
            job_id=str(data.get("job_id", "")),
            request_payload=data.get("request_payload", {}),
            version=int(data.get("version", 1) or 1),
        )
        rec.status = str(data.get("status", "pending"))
        rec.stage = str(data.get("current_stage") or data.get("stage", "prepare"))
        rec.progress = max(0, min(100, int(data.get("progress", 0) or 0)))
        rec.message = str(data.get("message", ""))
        rec.detected_language = str(data.get("detected_language", ""))
        artifacts = data.get("artifacts", [])
        rec.artifacts = list(artifacts) if isinstance(artifacts, list) else []
        rec.error_code = data.get("error_code")
        rec.error_detail = data.get("error_detail")
        rec.created_at = str(data.get("created_at") or utc_now())
        rec.updated_at = str(data.get("updated_at") or rec.created_at)
        return rec

    def to_api_dict(self) -> Dict[str, Any]:
        data = self.to_dict()
        data.pop("request_payload", None)
        data.pop("current_stage", None)
        data.pop("created_at", None)
        data.pop("updated_at", None)
        data.pop("version", None)
        return data


@dataclass(frozen=True)
class TaskQuery:
    keyword: str = ""
    status: str = ""
    stage: str = ""
    created_from: str = ""
    created_to: str = ""
    page: int = 1
    page_size: int = 20
    sort_by: str = "created_at"
    sort_order: str = "desc"


@dataclass
class TaskPage:
    items: List[TaskRecord] = field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20

    @property
    def pages(self) -> int:
        if not self.total:
            return 0
        return (self.total + self.page_size - 1) // self.page_size


class TaskRepository(ABC):
    """Persistence boundary; business modules must not depend on sqlite3."""

    @abstractmethod
    def create(self, job_id: str, request_payload: Optional[Dict[str, Any]] = None) -> TaskRecord:
        raise NotImplementedError

    @abstractmethod
    def get(self, job_id: str) -> Optional[TaskRecord]:
        raise NotImplementedError

    @abstractmethod
    def exists(self, job_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def list_all(self) -> List[TaskRecord]:
        raise NotImplementedError

    @abstractmethod
    def update(self, job_id: str, **kwargs: Any) -> Optional[TaskRecord]:
        raise NotImplementedError

    @abstractmethod
    def add_artifact(self, job_id: str, artifact: Dict[str, Any]) -> Optional[TaskRecord]:
        raise NotImplementedError

    @abstractmethod
    def delete(self, job_id: str) -> bool:
        raise NotImplementedError

    def search(self, query: TaskQuery) -> TaskPage:
        """Portable fallback used by the JSON compatibility repository."""
        records = self.list_all()
        keyword = query.keyword.casefold().strip()
        if keyword:
            records = [
                rec for rec in records
                if keyword in rec.job_id.casefold()
                or keyword in rec.message.casefold()
                or keyword in str(rec.request_payload).casefold()
            ]
        if query.status:
            records = [rec for rec in records if rec.status == query.status]
        if query.stage:
            records = [rec for rec in records if rec.stage == query.stage]
        if query.created_from:
            records = [rec for rec in records if rec.created_at >= query.created_from]
        if query.created_to:
            records = [rec for rec in records if rec.created_at <= query.created_to]
        allowed = {"created_at", "updated_at", "status", "stage", "progress", "job_id"}
        if query.sort_by not in allowed:
            raise ValueError(f"Unsupported sort field: {query.sort_by}")
        if query.sort_order.lower() not in {"asc", "desc"}:
            raise ValueError(f"Unsupported sort order: {query.sort_order}")
        records.sort(
            key=lambda rec: getattr(rec, query.sort_by),
            reverse=query.sort_order.lower() == "desc",
        )
        page = max(1, int(query.page))
        page_size = max(1, min(100, int(query.page_size)))
        start = (page - 1) * page_size
        return TaskPage(records[start:start + page_size], len(records), page, page_size)
