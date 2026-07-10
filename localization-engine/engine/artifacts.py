"""Safe task-scoped output asset management."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, Optional

from engine.repository import TaskRepository, utc_now


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ArtifactPathError(ValueError):
    pass


@dataclass(frozen=True)
class TaskArtifactLayout:
    root: Path
    work: Path
    artifacts: Path
    temp: Path
    logs: Path
    manifest: Path


class ArtifactManager:
    """Own paths and database registration for one task's generated files."""

    def __init__(self, storage_root: Path, job_id: str, repository: TaskRepository) -> None:
        if not _SAFE_COMPONENT.fullmatch(str(job_id)):
            raise ArtifactPathError(f"Unsafe task id: {job_id!r}")
        self.job_id = str(job_id)
        self.repository = repository
        root = Path(storage_root).expanduser().resolve() / self.job_id
        self.layout = TaskArtifactLayout(
            root=root,
            work=root / "work",
            artifacts=root / "artifacts",
            temp=root / "temp",
            logs=root / "logs",
            manifest=root / "manifest.json",
        )
        for path in (
            self.layout.root, self.layout.work, self.layout.artifacts,
            self.layout.temp, self.layout.logs,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _within(base: Path, target: Path) -> bool:
        try:
            target.relative_to(base)
            return True
        except (ValueError, OSError):
            return False

    def resolve(self, area: str, relative_path: str | Path = "") -> Path:
        bases = {
            "work": self.layout.work,
            "artifacts": self.layout.artifacts,
            "temp": self.layout.temp,
            "logs": self.layout.logs,
        }
        if area not in bases:
            raise ArtifactPathError(f"Unknown artifact area: {area}")
        relative = Path(relative_path)
        if relative.is_absolute():
            raise ArtifactPathError("Absolute artifact paths are not allowed")
        base = bases[area].resolve()
        target = (base / relative).resolve()
        if not self._within(base, target):
            raise ArtifactPathError(f"Artifact path escapes {area}: {relative}")
        return target

    def allocate_temp(self, suffix: str = ".tmp") -> Path:
        clean_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        if any(char in clean_suffix for char in ("/", "\\")):
            raise ArtifactPathError("Temporary suffix must not contain directories")
        return self.resolve("temp", f"{uuid.uuid4().hex}{clean_suffix}")

    def _unique_destination(self, relative_path: str | Path, *, area: str = "artifacts") -> Path:
        desired = self.resolve(area, relative_path)
        desired.parent.mkdir(parents=True, exist_ok=True)
        if not desired.exists():
            return desired
        for revision in range(2, 10000):
            candidate = desired.with_name(f"{desired.stem}__{revision}{desired.suffix}")
            if not candidate.exists():
                return candidate
        raise FileExistsError(f"Too many artifact name collisions for {desired.name}")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def promote(
        self,
        temp_path: Path,
        relative_path: str | Path,
        *,
        kind: str,
        stage: str,
        language: str = "",
        stage_run_id: Optional[int] = None,
        supersede: bool = True,
        area: str = "artifacts",
        checksum: bool = True,
    ) -> Dict[str, object]:
        temp = Path(temp_path).resolve()
        if not self._within(self.layout.temp.resolve(), temp):
            raise ArtifactPathError("Only files from the task temp directory can be promoted")
        if not temp.is_file():
            raise FileNotFoundError(temp)
        destination = self._unique_destination(relative_path, area=area)
        os.replace(temp, destination)
        relative = destination.relative_to(self.layout.root).as_posix()
        artifact: Dict[str, object] = {
            "kind": kind,
            "path": relative,
            "stage": stage,
            "language": language,
            "size_bytes": destination.stat().st_size,
            "checksum": self._sha256(destination) if checksum else "",
        }
        if supersede:
            supersede_method = getattr(self.repository, "supersede_artifacts", None)
            if supersede_method:
                supersede_method(self.job_id, kind)
        register = getattr(self.repository, "register_artifact", None)
        if register:
            artifact_id = register(
                self.job_id, artifact, stage_run_id=stage_run_id, is_current=True
            )
            artifact["id"] = artifact_id
        else:
            self.repository.add_artifact(self.job_id, artifact)
        self.export_manifest()
        return artifact

    @contextlib.contextmanager
    def atomic_output(
        self,
        relative_path: str | Path,
        *,
        kind: str,
        stage: str,
        language: str = "",
        stage_run_id: Optional[int] = None,
        supersede: bool = True,
        area: str = "artifacts",
    ) -> Iterator[Path]:
        """Yield a temp path and atomically promote it only after success."""
        suffix = Path(relative_path).suffix or ".tmp"
        temp = self.allocate_temp(suffix)
        try:
            yield temp
            self.promote(
                temp, relative_path, kind=kind, stage=stage,
                language=language, stage_run_id=stage_run_id,
                supersede=supersede, area=area,
            )
        finally:
            if temp.exists():
                temp.unlink()

    def register_external_source(self, path: Path, *, kind: str = "source_video") -> Dict[str, object]:
        """Register a source by reference; never copies a potentially large video."""
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        artifact: Dict[str, object] = {
            "kind": kind,
            "path": str(source),
            "stage": "prepare",
            "size_bytes": source.stat().st_size,
            "checksum": "",
            "metadata": {"external": True},
        }
        register = getattr(self.repository, "register_artifact", None)
        if register:
            artifact["id"] = register(self.job_id, artifact)
        else:
            self.repository.add_artifact(self.job_id, artifact)
        self.export_manifest()
        return artifact

    def invalidate_stages(self, stages: list[str]) -> int:
        invalidate = getattr(self.repository, "invalidate_artifacts", None)
        changed = int(invalidate(self.job_id, stages)) if invalidate else 0
        self.export_manifest()
        return changed

    def export_manifest(self) -> Dict[str, object]:
        """Export a replaceable snapshot; the repository remains authoritative."""
        list_artifacts = getattr(self.repository, "list_artifacts", None)
        artifacts = list_artifacts(self.job_id, current_only=False) if list_artifacts else []
        payload: Dict[str, object] = {
            "schema_version": 1,
            "job_id": self.job_id,
            "exported_at": utc_now(),
            "source_of_truth": "task_repository",
            "artifacts": artifacts,
        }
        temp = self.allocate_temp(".json")
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self.layout.manifest)
        finally:
            if temp.exists():
                temp.unlink()
        return payload
