"""Validated planning for failed-stage, stage-specific, and full reruns."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from engine.repository import TaskRepository
from engine.stages import STAGE_BY_NAME, stage_index, stages_before, stages_from


RETRYABLE_STATUSES = {"completed", "error", "failed", "cancelled", "interrupted", "paused"}


class RetryPlanningError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "RETRY_PLAN_INVALID") -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class RetryPlan:
    job_id: str
    requested_stage: str
    start_stage: str
    reason: str
    request_payload: Dict[str, Any]
    invalidated_artifacts: int = 0
    changed_config_stages: Sequence[str] = field(default_factory=tuple)
    lease_token: str = ""


class RetryPlanner:
    """Compute and atomically reserve a safe pipeline rerun."""

    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository

    def retry_latest_failed(
        self, job_id: str, *, request_updates: Optional[Dict[str, Any]] = None
    ) -> RetryPlan:
        list_runs = getattr(self.repository, "list_stage_runs", None)
        runs = list_runs(job_id) if list_runs else []
        failed = [run for run in runs if run.get("status") in {"failed", "interrupted"}]
        record = self.repository.get(job_id)
        if record is None:
            raise RetryPlanningError("Task not found", error_code="TASK_NOT_FOUND")
        stage = failed[-1]["stage"] if failed else record.stage
        if stage not in STAGE_BY_NAME:
            raise RetryPlanningError(
                "No failed pipeline stage is available",
                error_code="FAILED_STAGE_NOT_FOUND",
            )
        return self.plan_from(job_id, stage, request_updates=request_updates, reason="latest_failed")

    def rerun_all(
        self, job_id: str, *, request_updates: Optional[Dict[str, Any]] = None
    ) -> RetryPlan:
        return self.plan_from(job_id, "prepare", request_updates=request_updates, reason="full_rerun")

    def plan_from(
        self,
        job_id: str,
        stage: str,
        *,
        request_updates: Optional[Dict[str, Any]] = None,
        reason: str = "stage_rerun",
    ) -> RetryPlan:
        if stage not in STAGE_BY_NAME:
            raise RetryPlanningError(
                f"Unsupported retry stage: {stage}", error_code="RETRY_STAGE_INVALID"
            )
        record = self.repository.get(job_id)
        if record is None:
            raise RetryPlanningError("Task not found", error_code="TASK_NOT_FOUND")
        if record.status not in RETRYABLE_STATUSES:
            raise RetryPlanningError(
                f"Task is {record.status}; it cannot be retried",
                error_code="TASK_ALREADY_RUNNING" if record.status in {"running", "pending"} else "TASK_NOT_RETRYABLE",
            )

        payload = dict(record.request_payload or {})
        payload.update(request_updates or {})
        changed = self._changed_upstream_configs(job_id, stage, payload)
        start_stage = min((stage, *changed), key=stage_index)
        self._validate_upstream(job_id, start_stage, payload)
        self._validate_required_artifacts(job_id, start_stage, payload)

        acquire = getattr(self.repository, "acquire_run_lease", None)
        if not acquire:
            raise RetryPlanningError(
                "Repository does not support atomic retry leases",
                error_code="RETRY_LEASE_UNSUPPORTED",
            )
        lease_token = uuid.uuid4().hex
        if not acquire(job_id, lease_token):
            raise RetryPlanningError(
                "Task is already reserved or running",
                error_code="TASK_ALREADY_RUNNING",
            )

        invalidated = 0
        try:
            invalidate = getattr(self.repository, "invalidate_artifacts", None)
            if invalidate:
                invalidated = int(invalidate(job_id, stages_from(start_stage)))
            payload["resume_stage"] = start_stage
            self.repository.update(
                job_id,
                status="pending",
                stage=start_stage,
                progress=0,
                message=f"Retry planned from {start_stage}",
                error_code=None,
                error_detail=None,
                request_payload=payload,
            )
            add_event = getattr(self.repository, "add_event", None)
            if add_event:
                add_event(job_id, "RETRY_PLANNED", f"Retry from {start_stage}", {
                    "requested_stage": stage,
                    "start_stage": start_stage,
                    "reason": reason,
                    "changed_config_stages": list(changed),
                    "invalidated_artifacts": invalidated,
                })
        except Exception:
            getattr(self.repository, "release_run_lease")(job_id, lease_token)
            raise
        return RetryPlan(
            job_id=job_id,
            requested_stage=stage,
            start_stage=start_stage,
            reason=reason,
            request_payload=payload,
            invalidated_artifacts=invalidated,
            changed_config_stages=tuple(changed),
            lease_token=lease_token,
        )

    def _changed_upstream_configs(
        self, job_id: str, requested_stage: str, payload: Dict[str, Any]
    ) -> List[str]:
        last_run = getattr(self.repository, "last_completed_stage_run", None)
        fingerprint = getattr(self.repository, "stage_config_fingerprint", None)
        if not last_run or not fingerprint:
            return []
        changed: List[str] = []
        for stage in (*stages_before(requested_stage), requested_stage):
            previous = last_run(job_id, stage)
            if previous and previous.get("config_fingerprint") != fingerprint(stage, payload):
                changed.append(stage)
        return changed

    @staticmethod
    def _applicable(stage: str, payload: Dict[str, Any]) -> bool:
        if stage == "translate":
            source = str(payload.get("source_language") or "auto").lower()
            target = str(payload.get("target_language") or "").lower()
            return bool(target and target != source)
        if stage in {"tts", "audio_mix"}:
            return bool(payload.get("dubbing_enabled"))
        if stage == "render":
            return bool(payload.get("burn_subtitles"))
        return True

    def _validate_upstream(self, job_id: str, stage: str, payload: Dict[str, Any]) -> None:
        if stage == "prepare":
            return
        last_run = getattr(self.repository, "last_completed_stage_run", None)
        if not last_run:
            raise RetryPlanningError(
                "Repository cannot verify upstream stages",
                error_code="UPSTREAM_STAGE_UNVERIFIED",
            )
        missing = [
            upstream for upstream in stages_before(stage)
            if self._applicable(upstream, payload) and last_run(job_id, upstream) is None
        ]
        if missing:
            raise RetryPlanningError(
                f"Upstream stages are incomplete: {', '.join(missing)}",
                error_code="UPSTREAM_STAGE_INCOMPLETE",
            )

    def _validate_required_artifacts(
        self, job_id: str, stage: str, payload: Dict[str, Any]
    ) -> None:
        required_any = {
            "tts": {"translated_srt", "bilingual_srt"},
            "audio_mix": {"tts_report", "tts_timeline_report"},
            "render": {"source_ass", "translated_ass", "bilingual_ass"},
        }.get(stage)
        if not required_any or not self._applicable(stage, payload):
            return
        list_artifacts = getattr(self.repository, "list_artifacts", None)
        artifacts = list_artifacts(job_id, current_only=True) if list_artifacts else []
        workspace = Path(str(payload.get("workspace_dir") or "")).expanduser()
        available: set[str] = set()
        for artifact in artifacts:
            if artifact.get("kind") not in required_any:
                continue
            path = Path(str(artifact.get("path") or ""))
            resolved = path if path.is_absolute() else workspace / path
            if resolved.is_file():
                available.add(str(artifact["kind"]))
        if not available:
            raise RetryPlanningError(
                f"Required upstream artifact is missing for {stage}: one of {sorted(required_any)}",
                error_code="UPSTREAM_ARTIFACT_MISSING",
            )
