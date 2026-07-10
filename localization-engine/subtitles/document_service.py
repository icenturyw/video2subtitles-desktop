"""Draft, revision, and downstream invalidation services for subtitle documents."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from engine.artifacts import ArtifactManager
from engine.repository import TaskRepository
from engine.retry import RetryPlan, RetryPlanner
from engine.sqlite_repository import ConcurrentUpdateError

from .document import SubtitleDocument
from .document_validator import SubtitleValidationIssue, SubtitleValidator


class SubtitleVersionConflictError(RuntimeError):
    error_code = "SUBTITLE_VERSION_CONFLICT"


class SubtitleDocumentError(RuntimeError):
    def __init__(self, message: str, error_code: str = "SUBTITLE_DOCUMENT_ERROR") -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class SubtitleSaveResult:
    document: SubtitleDocument
    revision: dict
    issues: tuple[SubtitleValidationIssue, ...]
    invalidated_artifacts: int = 0
    retry_plan: RetryPlan | None = None


class SubtitleDocumentService:
    DOWNSTREAM_STAGES = ["tts", "audio_mix", "render", "finalize"]

    def __init__(
        self,
        storage_root: str | Path,
        repository: TaskRepository,
        *,
        validator: SubtitleValidator | None = None,
    ) -> None:
        self.storage_root = Path(storage_root).expanduser().resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.repository = repository
        self.validator = validator or SubtitleValidator()
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def create_from_segments(
        self,
        task_id: str,
        segments: Iterable[Any],
        *,
        source_language: str = "",
        target_language: str = "",
    ) -> SubtitleDocument:
        existing = self._task_metadata(task_id)
        if existing:
            return self.load_current(existing["id"])
        if self.repository.get(task_id) is None:
            raise SubtitleDocumentError("Task not found", "TASK_NOT_FOUND")
        document = SubtitleDocument.from_segments(
            task_id, segments,
            source_language=source_language,
            target_language=target_language,
        )
        getattr(self.repository, "create_subtitle_document")(document.document_id, task_id)
        return self.save_revision(document, base_version=0, invalidate_downstream=False).document

    def load_current(self, document_id: str) -> SubtitleDocument:
        metadata = self._metadata(document_id)
        revision_id = metadata.get("current_revision_id")
        if not revision_id:
            raise SubtitleDocumentError("Subtitle document has no formal revision", "SUBTITLE_REVISION_NOT_FOUND")
        revision = getattr(self.repository, "get_subtitle_revision")(revision_id)
        if not revision:
            raise SubtitleDocumentError("Current subtitle revision metadata is missing", "SUBTITLE_REVISION_NOT_FOUND")
        return self._load_revision_artifact(metadata["task_id"], revision)

    def save_draft(self, document: SubtitleDocument, *, base_version: int) -> dict:
        with self._document_lock(document.document_id):
            metadata = self._metadata(document.document_id)
            self._check_base_version(metadata, base_version)
            if document.task_id != metadata["task_id"]:
                raise SubtitleDocumentError("Document task id mismatch")
            draft = document.clone(version=base_version)
            manager = self._artifacts(document.task_id)
            target = manager.resolve("work", "subtitles/draft.json")
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = manager.allocate_temp(".json")
            try:
                temp.write_text(json.dumps(draft.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
                checksum = _sha256(temp)
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)
            try:
                revision = getattr(self.repository, "save_subtitle_revision_metadata")(
                    document.document_id,
                    base_version=base_version,
                    artifact_path=target.relative_to(manager.layout.root).as_posix(),
                    checksum=checksum,
                    is_draft=True,
                )
            except ConcurrentUpdateError as exc:
                raise SubtitleVersionConflictError(str(exc)) from exc
            self._event(document.task_id, "SUBTITLE_DRAFT_SAVED", "Subtitle draft saved", {
                "document_id": document.document_id, "base_version": base_version,
            })
            return revision

    def recover_draft(self, document_id: str) -> SubtitleDocument | None:
        metadata = self._metadata(document_id)
        revision = getattr(self.repository, "get_subtitle_draft")(document_id)
        if not revision:
            return None
        return self._load_revision_artifact(metadata["task_id"], revision)

    def save_revision(
        self,
        document: SubtitleDocument,
        *,
        base_version: int,
        invalidate_downstream: bool = True,
        regenerate: bool = False,
    ) -> SubtitleSaveResult:
        with self._document_lock(document.document_id):
            metadata = self._metadata(document.document_id)
            self._check_base_version(metadata, base_version)
            if document.task_id != metadata["task_id"]:
                raise SubtitleDocumentError("Document task id mismatch")
            issues = tuple(self.validator.validate(document))
            blocking = [issue for issue in issues if issue.severity == "error"]
            if blocking:
                raise SubtitleDocumentError(
                    f"Subtitle validation failed: {blocking[0].code}",
                    "SUBTITLE_VALIDATION_FAILED",
                )
            saved = document.clone(version=base_version + 1)
            manager = self._artifacts(document.task_id)
            revision_artifact = self._promote_document(
                manager,
                saved,
                f"subtitles/revision_v{saved.version}.json",
                kind="subtitle_revision",
                supersede=False,
            )
            try:
                revision = getattr(self.repository, "save_subtitle_revision_metadata")(
                    document.document_id,
                    base_version=base_version,
                    artifact_path=str(revision_artifact["path"]),
                    checksum=str(revision_artifact["checksum"]),
                    is_draft=False,
                )
            except ConcurrentUpdateError as exc:
                raise SubtitleVersionConflictError(str(exc)) from exc
            self._promote_document(
                manager,
                saved,
                f"subtitles/current_v{saved.version}.json",
                kind="current_subtitle",
                supersede=True,
            )
            draft_path = manager.resolve("work", "subtitles/draft.json")
            draft_path.unlink(missing_ok=True)
            invalidated = manager.invalidate_stages(self.DOWNSTREAM_STAGES) if invalidate_downstream else 0
            retry_plan = None
            if regenerate:
                retry_plan = RetryPlanner(self.repository).plan_from(
                    document.task_id, "tts", reason="subtitle_revision"
                )
            self._event(document.task_id, "SUBTITLE_REVISION_SAVED", "Formal subtitle revision saved", {
                "document_id": document.document_id,
                "revision_id": revision["id"],
                "version": saved.version,
                "invalidated_artifacts": invalidated,
            })
            if invalidated:
                self._event(document.task_id, "ARTIFACT_INVALIDATED", "Downstream artifacts invalidated", {
                    "stages": list(self.DOWNSTREAM_STAGES), "count": invalidated,
                })
            return SubtitleSaveResult(saved, revision, issues, invalidated, retry_plan)

    def list_revisions(self, document_id: str) -> list[dict]:
        self._metadata(document_id)
        return getattr(self.repository, "list_subtitle_revisions")(document_id)

    def restore_revision(
        self,
        document_id: str,
        revision_id: str,
        *,
        base_version: int,
        regenerate: bool = False,
    ) -> SubtitleSaveResult:
        metadata = self._metadata(document_id)
        revision = getattr(self.repository, "get_subtitle_revision")(revision_id)
        if not revision or revision["document_id"] != document_id or revision["is_draft"]:
            raise SubtitleDocumentError("Subtitle revision not found", "SUBTITLE_REVISION_NOT_FOUND")
        historical = self._load_revision_artifact(metadata["task_id"], revision)
        restored = historical.clone(version=base_version)
        return self.save_revision(
            restored,
            base_version=base_version,
            invalidate_downstream=True,
            regenerate=regenerate,
        )

    def validate(self, document: SubtitleDocument) -> list[SubtitleValidationIssue]:
        return self.validator.validate(document)

    def _promote_document(
        self,
        manager: ArtifactManager,
        document: SubtitleDocument,
        relative_path: str,
        *,
        kind: str,
        supersede: bool,
    ) -> dict:
        temp = manager.allocate_temp(".json")
        try:
            temp.write_text(json.dumps(document.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            return manager.promote(
                temp, relative_path,
                kind=kind, stage="subtitle_export",
                language=document.target_language,
                supersede=supersede,
            )
        finally:
            temp.unlink(missing_ok=True)

    def _load_revision_artifact(self, task_id: str, revision: dict) -> SubtitleDocument:
        manager = self._artifacts(task_id)
        path = (manager.layout.root / str(revision["artifact_path"])).resolve()
        try:
            path.relative_to(manager.layout.root.resolve())
        except ValueError as exc:
            raise SubtitleDocumentError("Revision path escapes task storage", "SUBTITLE_ARTIFACT_INVALID") from exc
        if not path.is_file():
            raise SubtitleDocumentError("Subtitle revision artifact is missing", "SUBTITLE_ARTIFACT_MISSING")
        checksum = _sha256(path)
        if checksum != revision["checksum"]:
            raise SubtitleDocumentError("Subtitle revision checksum mismatch", "SUBTITLE_ARTIFACT_CHECKSUM_MISMATCH")
        try:
            return SubtitleDocument.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise SubtitleDocumentError("Subtitle revision artifact is invalid", "SUBTITLE_ARTIFACT_INVALID") from exc

    def _metadata(self, document_id: str) -> dict:
        metadata = getattr(self.repository, "get_subtitle_document")(document_id)
        if not metadata:
            raise SubtitleDocumentError("Subtitle document not found", "SUBTITLE_DOCUMENT_NOT_FOUND")
        return metadata

    def _task_metadata(self, task_id: str) -> dict | None:
        getter = getattr(self.repository, "get_task_subtitle_document", None)
        return getter(task_id) if getter else None

    @staticmethod
    def _check_base_version(metadata: dict, base_version: int) -> None:
        if int(metadata["current_version"]) != int(base_version):
            raise SubtitleVersionConflictError(
                f"Expected version {base_version}; current version is {metadata['current_version']}"
            )

    def _artifacts(self, task_id: str) -> ArtifactManager:
        return ArtifactManager(self.storage_root, task_id, self.repository)

    def _document_lock(self, document_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(document_id, threading.RLock())

    def _event(self, task_id: str, event_type: str, message: str, payload: dict) -> None:
        add_event = getattr(self.repository, "add_event", None)
        if add_event:
            add_event(task_id, event_type, message, payload)


def document_to_segments(document: SubtitleDocument) -> list[Any]:
    from job_models import SubtitleSegment
    return [
        SubtitleSegment(
            index=index,
            start=cue.start_ms / 1000.0,
            end=cue.end_ms / 1000.0,
            text=cue.source_text,
            translation=cue.translated_text,
        )
        for index, cue in enumerate(document.cues, 1)
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
