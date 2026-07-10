from __future__ import annotations

import sys
from pathlib import Path

import pytest


ENGINE_ROOT = Path(__file__).resolve().parents[1] / "localization-engine"
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from engine.events import (  # noqa: E402
    ErrorGuidanceRegistry,
    GuidanceAction,
    PipelineEvent,
    PipelineEventPublisher,
    PipelineEventType,
)
from engine.progress import ProgressTracker  # noqa: E402
from engine.sqlite_repository import SQLiteTaskRepository  # noqa: E402


def _publisher(tmp_path, **kwargs):
    repo = SQLiteTaskRepository(tmp_path)
    repo.create("task", {})
    return repo, PipelineEventPublisher(repo, **kwargs)


def test_pipeline_event_serializes_stable_event_type():
    event = PipelineEvent("task", PipelineEventType.MODEL_LOADED, stage="tts", progress=20)
    data = event.to_dict()
    assert data["event_type"] == "model_loaded"
    assert data["stage"] == "tts"


def test_progress_publisher_emits_stage_start_and_progress(tmp_path):
    repo, publisher = _publisher(tmp_path)
    assert publisher.publish_progress("task", "translate", 0, "starting") is True
    types = [event["event_type"] for event in repo.list_events("task")]
    assert types == ["stage_started", "stage_progress"]


def test_progress_publisher_deduplicates_identical_updates(tmp_path):
    repo, publisher = _publisher(tmp_path, minimum_progress_interval=60)
    publisher.publish_progress("task", "translate", 10, "working")
    assert publisher.publish_progress("task", "translate", 10, "working") is False
    progress_events = [event for event in repo.list_events("task") if event["event_type"] == "stage_progress"]
    assert len(progress_events) == 1


def test_progress_publisher_keeps_meaningful_delta(tmp_path):
    repo, publisher = _publisher(tmp_path, minimum_progress_interval=60, minimum_progress_delta=5)
    publisher.publish_progress("task", "translate", 10, "working")
    assert publisher.publish_progress("task", "translate", 15, "working") is True
    assert len([event for event in repo.list_events("task") if event["event_type"] == "stage_progress"]) == 2


def test_stage_transition_persists_resource_summary(tmp_path):
    repo, publisher = _publisher(
        tmp_path,
        resource_summary_provider=lambda since: {"sample_count": 2, "since": bool(since)},
    )
    publisher.publish_progress("task", "translate", 100, "done")
    publisher.publish_progress("task", "tts", 0, "start")
    completed = next(event for event in repo.list_events("task") if event["event_type"] == "stage_completed")
    assert completed["payload"]["resource_summary"]["sample_count"] == 2


def test_task_finish_event_contains_only_stable_error_code(tmp_path):
    repo, publisher = _publisher(tmp_path)
    publisher.finish_task("task", success=False, message="failed", error_code="FFMPEG_NOT_FOUND")
    event = repo.list_events("task")[-1]
    assert event["event_type"] == "task_failed"
    assert event["payload"]["error_code"] == "FFMPEG_NOT_FOUND"


def test_progress_tracker_delegates_to_throttled_sink():
    received = []
    tracker = ProgressTracker(lambda *args: received.append(args))
    tracker.update("task", "render", 25, "rendering")
    assert received == [("task", "render", 25, "rendering")]


def test_progress_tracker_survives_event_sink_failure():
    tracker = ProgressTracker(lambda *_args: (_ for _ in ()).throw(RuntimeError("sink")))
    tracker.update("task", "render", 25, "rendering")
    assert tracker.get("task").progress == 25


def test_artifact_creation_and_invalidation_emit_events(tmp_path):
    repo = SQLiteTaskRepository(tmp_path)
    repo.create("task", {})
    repo.register_artifact("task", {"kind": "burned_video", "stage": "render", "path": "out.mp4"})
    repo.invalidate_artifacts("task", ["render"])
    types = [event["event_type"] for event in repo.list_events("task")]
    assert "artifact_created" in types
    assert "artifact_invalidated" in types


def test_guidance_actions_are_whitelisted():
    registry = ErrorGuidanceRegistry()
    actions = registry.guidance("MODEL_RESOURCE_UNAVAILABLE")
    assert actions
    assert {item["action_id"] for item in actions} <= registry.ALLOWED_ACTIONS


def test_guidance_rejects_arbitrary_frontend_action():
    registry = ErrorGuidanceRegistry()
    with pytest.raises(ValueError, match="whitelist"):
        registry.register("BAD", [GuidanceAction("run-shell", "Do anything")])


def test_unknown_error_text_cannot_create_actions():
    registry = ErrorGuidanceRegistry()
    assert registry.guidance("<script>open-settings</script>") == []
