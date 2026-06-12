from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional


class TaskStatus(enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    SAVING = "saving"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"

    def __str__(self):
        return self.value


_UI_TEXT_MAP = {
    TaskStatus.PENDING: "等待处理",
    TaskStatus.QUEUED: "排队中",
    TaskStatus.DOWNLOADING: "下载中",
    TaskStatus.PROCESSING: "处理中",
    TaskStatus.SAVING: "保存中",
    TaskStatus.COMPLETED: "已完成",
    TaskStatus.ERROR: "失败",
    TaskStatus.CANCELLED: "已取消",
}

_LEGACY_TEXT_MAP = {
    "pending": TaskStatus.PENDING,
    "queued": TaskStatus.QUEUED,
    "downloading": TaskStatus.DOWNLOADING,
    "processing": TaskStatus.PROCESSING,
    "saving": TaskStatus.SAVING,
    "completed": TaskStatus.COMPLETED,
    "error": TaskStatus.ERROR,
    "cancelled": TaskStatus.CANCELLED,
}


def status_to_ui_text(status: TaskStatus) -> str:
    return _UI_TEXT_MAP.get(status, str(status))


def normalize_status(value: str | TaskStatus) -> TaskStatus:
    if isinstance(value, TaskStatus):
        return value
    result = _LEGACY_TEXT_MAP.get(value)
    if result is not None:
        return result
    return TaskStatus.PENDING


@dataclass
class TaskInfo:
    key: str
    is_url: bool = False
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    message: str = ""
    error: str = ""
    language: str = "auto"
    output_dir: str = ""
    srt_path: str = ""
