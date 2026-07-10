"""Safe, idempotent migration from the legacy tasks.json store."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from engine.repository import TaskRecord
from engine.sqlite_repository import SQLiteTaskRepository


logger = logging.getLogger("engine.json_migration")


@dataclass(frozen=True)
class MigrationResult:
    source: str
    backup: str = ""
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _load_records(raw: bytes) -> List[TaskRecord]:
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("tasks.json root must be an object")
    items = decoded.get("tasks", [])
    if not isinstance(items, list):
        raise ValueError("tasks.json 'tasks' must be an array")
    records: List[TaskRecord] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"tasks[{index}] must be an object")
        job_id = str(item.get("job_id") or "").strip()
        if not job_id:
            raise ValueError(f"tasks[{index}] has no job_id")
        if job_id in seen:
            raise ValueError(f"tasks[{index}] duplicates job_id {job_id!r}")
        seen.add(job_id)
        rec = TaskRecord.from_dict(item)
        if not isinstance(rec.request_payload, dict):
            raise ValueError(f"tasks[{index}].request_payload must be an object")
        if not isinstance(rec.artifacts, list):
            raise ValueError(f"tasks[{index}].artifacts must be an array")
        for artifact_index, artifact in enumerate(rec.artifacts):
            if not isinstance(artifact, dict):
                raise ValueError(
                    f"tasks[{index}].artifacts[{artifact_index}] must be an object"
                )
        records.append(rec)
    return records


def _backup_path(source: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    candidate = source.with_name(f"{source.name}.backup.{stamp}")
    suffix = 1
    while candidate.exists():
        candidate = source.with_name(f"{source.name}.backup.{stamp}.{suffix}")
        suffix += 1
    return candidate


def migrate_tasks_json(
    repository: SQLiteTaskRepository,
    source: Optional[Path] = None,
) -> MigrationResult:
    """Import tasks.json once without deleting or modifying the source file.

    Invalid input never reaches the database. Any database failure rolls the
    whole import back through ``bulk_import``'s explicit transaction.
    """
    source_path = Path(source) if source is not None else repository.data_dir / "tasks.json"
    if not source_path.exists():
        return MigrationResult(source=str(source_path))
    backup = ""
    try:
        raw = source_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        records = _load_records(raw)
        new_records = [record for record in records if not repository.exists(record.job_id)]
        if not new_records:
            return MigrationResult(
                source=str(source_path), skipped=len(records),
            )
        backup_path = _backup_path(source_path)
        shutil.copy2(source_path, backup_path)
        backup = str(backup_path)
        imported, skipped = repository.bulk_import(
            records,
            source=str(source_path.resolve()),
            source_sha256=digest,
        )
        result = MigrationResult(
            source=str(source_path), backup=backup,
            imported=imported, skipped=skipped,
        )
        logger.info(
            "tasks.json migration: imported=%d skipped=%d failed=0 backup=%s",
            imported, skipped, backup,
        )
        return result
    except Exception as exc:
        logger.exception("tasks.json migration failed; database import rolled back")
        return MigrationResult(
            source=str(source_path), backup=backup,
            failed=1, error=f"{type(exc).__name__}: {exc}",
        )
