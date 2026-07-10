from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "localization-engine"
for path in (ROOT, ENGINE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from engine.cancellation import CancellationToken
from engine.pipeline import PipelineRunner
from engine.progress import ProgressTracker
from engine.retry import RetryPlanner, RetryPlanningError
from engine.sqlite_repository import SQLiteTaskRepository


def _failed_at(tmp_path: Path, failed_stage: str, **payload_updates):
    payload = {
        "workspace_dir": str(tmp_path),
        "source_language": "en",
        "target_language": "zh-CN",
        "dubbing_enabled": False,
        "burn_subtitles": False,
    }
    payload.update(payload_updates)
    repo = SQLiteTaskRepository(tmp_path / "db")
    repo.create("job", payload)
    sequence = ["prepare", "normalize", "translate", "subtitle_export", "tts", "audio_mix", "render", "finalize"]
    repo.update("job", status="running", stage="prepare")
    for stage in sequence[1:sequence.index(failed_stage) + 1]:
        repo.update("job", stage=stage)
    repo.update("job", status="error", stage=failed_stage, error_code="STAGE_FAILED", error_detail="boom")
    return repo


def test_stage_attempts_preserve_failure_and_output_ids(tmp_path: Path):
    repo = _failed_at(tmp_path, "translate")
    repo.update("job", status="pending", stage="translate")
    repo.update("job", status="running", stage="translate")
    repo.register_artifact("job", {"kind": "report", "path": "report.json"})
    repo.update("job", stage="subtitle_export")
    runs = [run for run in repo.list_stage_runs("job") if run["stage"] == "translate"]
    assert [(run["attempt"], run["status"]) for run in runs] == [(1, "failed"), (2, "completed")]
    assert runs[0]["error_code"] == "STAGE_FAILED" and runs[1]["output_artifacts"]


def test_task_stage_and_new_run_are_committed_together(tmp_path: Path):
    repo = _failed_at(tmp_path, "translate")
    repo.update("job", status="pending", stage="translate")
    repo.update("job", status="running", stage="translate")
    record = repo.get("job")
    run = repo.list_stage_runs("job")[-1]
    assert record.status == "running" and record.stage == "translate"
    assert run["stage"] == record.stage and run["status"] == "running"


def test_stage_config_fingerprint_changes_only_for_relevant_config(tmp_path: Path):
    repo = SQLiteTaskRepository(tmp_path)
    base = {"source_language": "en", "target_language": "zh", "tts_voice": "A"}
    assert repo.stage_config_fingerprint("translate", base) == repo.stage_config_fingerprint(
        "translate", {**base, "tts_voice": "B"}
    )
    assert repo.stage_config_fingerprint("translate", base) != repo.stage_config_fingerprint(
        "translate", {**base, "target_language": "ja"}
    )


def test_retry_latest_failed_stage(tmp_path: Path):
    repo = _failed_at(tmp_path, "translate")
    plan = RetryPlanner(repo).retry_latest_failed("job")
    assert plan.requested_stage == plan.start_stage == "translate"
    assert repo.get("job").status == "pending" and repo.run_lease("job") == plan.lease_token


def test_retry_from_explicit_stage(tmp_path: Path):
    repo = _failed_at(tmp_path, "render")
    plan = RetryPlanner(repo).plan_from("job", "subtitle_export")
    assert plan.requested_stage == "subtitle_export" and plan.reason == "stage_rerun"
    assert plan.request_payload["resume_stage"] == "subtitle_export"


def test_full_rerun_needs_no_upstream_history(tmp_path: Path):
    repo = SQLiteTaskRepository(tmp_path)
    repo.create("legacy")
    repo.update("legacy", status="error", stage="render")
    plan = RetryPlanner(repo).rerun_all("legacy")
    assert plan.start_stage == "prepare" and plan.reason == "full_rerun"


def test_config_change_expands_retry_to_earliest_affected_stage(tmp_path: Path):
    repo = _failed_at(tmp_path, "translate")
    plan = RetryPlanner(repo).plan_from(
        "job", "translate", request_updates={"source_language": "fr"}
    )
    assert plan.start_stage == "normalize"
    assert plan.changed_config_stages == ("normalize",)


def test_missing_upstream_stage_rejects_retry(tmp_path: Path):
    repo = SQLiteTaskRepository(tmp_path)
    repo.create("job", {"workspace_dir": str(tmp_path), "target_language": "zh"})
    repo.update("job", status="error", stage="translate")
    with pytest.raises(RetryPlanningError, match="Upstream stages") as exc:
        RetryPlanner(repo).plan_from("job", "translate")
    assert exc.value.error_code == "UPSTREAM_STAGE_INCOMPLETE"


def test_missing_required_artifact_rejects_tts_retry(tmp_path: Path):
    repo = _failed_at(tmp_path, "tts", dubbing_enabled=True)
    with pytest.raises(RetryPlanningError) as exc:
        RetryPlanner(repo).plan_from("job", "tts")
    assert exc.value.error_code == "UPSTREAM_ARTIFACT_MISSING"


def test_concurrent_retry_only_grants_one_lease(tmp_path: Path):
    repo = _failed_at(tmp_path, "translate")
    barrier = threading.Barrier(2)
    results: list[str] = []

    def plan():
        barrier.wait()
        try:
            RetryPlanner(repo).retry_latest_failed("job")
            results.append("ok")
        except RetryPlanningError as exc:
            results.append(exc.error_code)

    threads = [threading.Thread(target=plan) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(results) == ["TASK_ALREADY_RUNNING", "ok"]


def test_retry_invalidates_downstream_metadata_without_deleting_files(tmp_path: Path):
    repo = _failed_at(tmp_path, "render")
    output = tmp_path / "old.mp4"
    output.write_bytes(b"old")
    repo.register_artifact("job", {"kind": "video", "path": str(output), "stage": "render"})
    plan = RetryPlanner(repo).plan_from("job", "render")
    assert plan.invalidated_artifacts == 1
    assert repo.list_artifacts("job")[0]["is_current"] is False and output.exists()


def test_startup_recovery_marks_runs_and_events_once(tmp_path: Path):
    repo = SQLiteTaskRepository(tmp_path)
    repo.create("job")
    repo.update("job", status="running", stage="render")
    restarted = SQLiteTaskRepository(tmp_path)
    assert restarted.recover_interrupted() == 1
    record = restarted.get("job")
    assert (record.status, record.stage, record.error_code) == ("interrupted", "render", "PROCESS_INTERRUPTED")
    assert restarted.list_stage_runs("job")[-1]["status"] == "interrupted"
    assert restarted.list_events("job")[-1]["event_type"] == "PROCESS_INTERRUPTED"
    assert restarted.recover_interrupted() == 0


def test_translation_failure_then_retry_completes_pipeline(tmp_path: Path):
    (tmp_path / "subtitles").mkdir()
    source = tmp_path / "subtitles" / "source.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    payload = {
        "workspace_dir": str(tmp_path), "source_subtitle": str(source),
        "source_language": "en", "target_language": "zh-CN",
        "subtitle_mode": "bilingual", "burn_subtitles": False,
        "dubbing_enabled": True,
        "translation": {"provider": "fake", "max_batch_items": 1, "retry_count": 0},
    }
    repo = SQLiteTaskRepository(tmp_path / "db")
    repo.create("translation-job", payload)
    runner = PipelineRunner(repo, ProgressTracker())

    class EmptyProvider:
        def translate_batch(self, *args, **kwargs):
            return []
        def close(self):
            pass

    class GoodProvider:
        def translate_batch(self, segments, *args, **kwargs):
            return [{"id": item["id"], "text": "你好"} for item in segments]
        def close(self):
            pass

    with patch("engine.pipeline.get_provider", return_value=EmptyProvider()):
        runner._execute("translation-job", payload, CancellationToken())
    assert repo.get("translation-job").status == "error"
    assert repo.list_stage_runs("translation-job")[-1]["stage"] == "translate"

    plan = RetryPlanner(repo).retry_latest_failed(
        "translation-job", request_updates={"dubbing_enabled": False}
    )
    with patch("engine.pipeline.get_provider", return_value=GoodProvider()):
        runner._execute("translation-job", plan.request_payload, CancellationToken())
    attempts = [run for run in repo.list_stage_runs("translation-job") if run["stage"] == "translate"]
    assert repo.get("translation-job").status == "completed"
    assert [(run["attempt"], run["status"]) for run in attempts] == [(1, "failed"), (2, "completed")]
    assert (tmp_path / "subtitles" / "zh-CN.srt").exists()


def test_render_crash_then_restart_can_resume_current_stage(tmp_path: Path):
    (tmp_path / "source").mkdir()
    (tmp_path / "subtitles").mkdir()
    video = tmp_path / "source" / "source.mp4"
    source = tmp_path / "subtitles" / "source.srt"
    video.write_bytes(b"video")
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    payload = {
        "workspace_dir": str(tmp_path), "source_video": str(video),
        "source_subtitle": str(source), "source_language": "en", "target_language": "en",
        "subtitle_mode": "source", "burn_subtitles": True, "dubbing_enabled": False,
    }
    db_dir = tmp_path / "db"
    repo = SQLiteTaskRepository(db_dir)
    repo.create("render-job", payload)
    runner = PipelineRunner(repo, ProgressTracker())
    with patch("engine.pipeline.find_ffmpeg", return_value="ffmpeg"), patch(
        "engine.pipeline.render_hardsub", side_effect=SystemExit("simulated crash")
    ):
        with pytest.raises(SystemExit):
            runner.run_job("render-job", payload, CancellationToken())
    assert repo.get("render-job").status == "running" and repo.get("render-job").stage == "render"

    restarted = SQLiteTaskRepository(db_dir)
    assert restarted.recover_interrupted() == 1
    plan = RetryPlanner(restarted).plan_from("render-job", "render")
    assert plan.start_stage == "render" and restarted.get("render-job").status == "pending"
