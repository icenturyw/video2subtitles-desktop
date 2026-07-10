"""Throttled pipeline events and whitelisted user guidance."""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable

from engine.repository import TaskRepository


class PipelineEventType(str, Enum):
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    STAGE_STARTED = "stage_started"
    STAGE_PROGRESS = "stage_progress"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"
    MODEL_LOADING = "model_loading"
    MODEL_LOADED = "model_loaded"
    MODEL_UNLOADING = "model_unloading"
    ARTIFACT_CREATED = "artifact_created"
    ARTIFACT_INVALIDATED = "artifact_invalidated"
    WARNING = "warning"


@dataclass(frozen=True)
class PipelineEvent:
    task_id: str
    event_type: PipelineEventType
    message: str = ""
    stage: str = ""
    progress: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["event_type"] = self.event_type.value
        return data


class PipelineEventPublisher:
    """Persist useful milestones while suppressing noisy progress events."""

    def __init__(
        self,
        repository: TaskRepository,
        *,
        minimum_progress_interval: float = 0.5,
        minimum_progress_delta: int = 1,
        resource_summary_provider: Callable[[float | None], dict] | None = None,
    ) -> None:
        self.repository = repository
        self.minimum_progress_interval = max(0.0, float(minimum_progress_interval))
        self.minimum_progress_delta = max(1, int(minimum_progress_delta))
        self.resource_summary_provider = resource_summary_provider or (lambda _since: {})
        self._last: dict[str, tuple[str, int, str, float]] = {}
        self._stage_started: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def publish(self, event: PipelineEvent) -> bool:
        add_event = getattr(self.repository, "add_event", None)
        if not add_event:
            return False
        payload = dict(event.payload)
        if event.stage:
            payload["stage"] = event.stage
        if event.progress is not None:
            payload["progress"] = event.progress
        payload["timestamp"] = event.timestamp
        add_event(event.task_id, event.event_type.value, event.message, payload)
        return True

    def publish_progress(self, task_id: str, stage: str, progress: int, message: str) -> bool:
        now = time.monotonic()
        progress = max(0, min(100, int(progress)))
        with self._lock:
            previous = self._last.get(task_id)
            stage_changed = previous is None or previous[0] != stage
            if stage_changed:
                if previous is not None:
                    old_stage, old_progress, _old_message, _old_time = previous
                    started = self._stage_started.pop((task_id, old_stage), None)
                    self.publish(PipelineEvent(
                        task_id, PipelineEventType.STAGE_COMPLETED,
                        stage=old_stage, progress=old_progress,
                        payload={"resource_summary": self.resource_summary_provider(started)},
                    ))
                self._stage_started[(task_id, stage)] = time.time()
                self.publish(PipelineEvent(
                    task_id, PipelineEventType.STAGE_STARTED,
                    message=message, stage=stage, progress=progress,
                ))
            elif previous is not None:
                same_message = previous[2] == message
                delta = abs(progress - previous[1])
                if (
                    same_message
                    and delta < self.minimum_progress_delta
                    and now - previous[3] < self.minimum_progress_interval
                    and progress not in {0, 100}
                ):
                    return False
                if previous[1] == progress and same_message and now - previous[3] < self.minimum_progress_interval:
                    return False
            self._last[task_id] = (stage, progress, message, now)
        return self.publish(PipelineEvent(
            task_id, PipelineEventType.STAGE_PROGRESS,
            message=message, stage=stage, progress=progress,
        ))

    def finish_task(self, task_id: str, *, success: bool, message: str = "", error_code: str = "") -> None:
        event_type = PipelineEventType.TASK_COMPLETED if success else PipelineEventType.TASK_FAILED
        self.publish(PipelineEvent(
            task_id, event_type, message,
            payload={"error_code": error_code} if error_code else {},
        ))
        with self._lock:
            self._last.pop(task_id, None)
            for key in [key for key in self._stage_started if key[0] == task_id]:
                self._stage_started.pop(key, None)


@dataclass(frozen=True)
class GuidanceAction:
    action_id: str
    label: str
    parameters: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class ErrorGuidanceRegistry:
    """Maps stable error codes to a fixed set of harmless UI actions."""

    ALLOWED_ACTIONS = {
        "retry-stage", "open-settings", "open-output", "view-log",
        "install-ffmpeg", "free-disk", "choose-cpu", "edit-subtitles",
    }

    def __init__(self) -> None:
        self._guidance: dict[str, tuple[GuidanceAction, ...]] = {
            "FFMPEG_NOT_FOUND": (
                GuidanceAction("install-ffmpeg", "Install or configure FFmpeg"),
                GuidanceAction("open-settings", "Open settings", {"section": "runtime"}),
            ),
            "DISK_SPACE_INSUFFICIENT": (
                GuidanceAction("free-disk", "Free workspace disk space"),
                GuidanceAction("open-settings", "Choose another workspace", {"section": "workspace"}),
            ),
            "MODEL_RESOURCE_UNAVAILABLE": (
                GuidanceAction("choose-cpu", "Use CPU or a smaller model"),
                GuidanceAction("retry-stage", "Retry the failed stage"),
            ),
            "TRANSLATION_PROVIDER_NOT_CONFIGURED": (
                GuidanceAction("open-settings", "Configure translation", {"section": "translation"}),
            ),
            "TTS_PROVIDER_NOT_FOUND": (
                GuidanceAction("open-settings", "Configure text to speech", {"section": "tts"}),
            ),
            "SUBTITLE_VALIDATION_FAILED": (
                GuidanceAction("edit-subtitles", "Open the subtitle editor"),
            ),
            "SUBTITLE_VERSION_CONFLICT": (
                GuidanceAction("edit-subtitles", "Reload the latest subtitle revision"),
            ),
            "PROCESS_INTERRUPTED": (
                GuidanceAction("retry-stage", "Resume from the interrupted stage"),
                GuidanceAction("view-log", "View the full log"),
            ),
        }

    def register(self, error_code: str, actions: list[GuidanceAction]) -> None:
        if any(action.action_id not in self.ALLOWED_ACTIONS for action in actions):
            raise ValueError("Guidance contains an action outside the whitelist")
        self._guidance[str(error_code)] = tuple(actions)

    def guidance(self, error_code: str) -> list[dict]:
        actions = self._guidance.get(str(error_code or ""), ())
        return [action.to_dict() for action in actions]
