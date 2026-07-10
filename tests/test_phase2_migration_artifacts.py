from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ENGINE = Path(__file__).resolve().parents[1] / "localization-engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from engine.artifacts import ArtifactManager, ArtifactPathError
from engine.json_migration import migrate_tasks_json
from engine.sqlite_repository import SQLiteTaskRepository


def _write_tasks(path: Path, tasks: list[dict]) -> None:
    path.write_text(json.dumps({"tasks": tasks}, ensure_ascii=False), encoding="utf-8")


def test_missing_json_is_a_clean_noop(tmp_path: Path):
    repo = SQLiteTaskRepository(tmp_path / "data")
    result = migrate_tasks_json(repo)
    assert result.ok and result.imported == result.skipped == result.failed == 0


def test_valid_json_is_backed_up_and_preserved(tmp_path: Path):
    repo = SQLiteTaskRepository(tmp_path)
    source = tmp_path / "tasks.json"
    _write_tasks(source, [{"job_id": "legacy", "status": "completed"}])
    original = source.read_bytes()
    result = migrate_tasks_json(repo, source)
    assert result.imported == 1 and Path(result.backup).read_bytes() == original
    assert source.read_bytes() == original and repo.get("legacy").status == "completed"


def test_repeated_migration_is_idempotent_without_extra_backup(tmp_path: Path):
    repo = SQLiteTaskRepository(tmp_path)
    source = tmp_path / "tasks.json"
    _write_tasks(source, [{"job_id": "once"}])
    assert migrate_tasks_json(repo, source).imported == 1
    repeated = migrate_tasks_json(repo, source)
    assert repeated.skipped == 1 and repeated.backup == ""
    assert len(list(tmp_path.glob("tasks.json.backup.*"))) == 1


def test_partial_existing_data_reports_imported_and_skipped(tmp_path: Path):
    repo = SQLiteTaskRepository(tmp_path)
    repo.create("existing")
    source = tmp_path / "tasks.json"
    _write_tasks(source, [{"job_id": "existing"}, {"job_id": "new"}])
    result = migrate_tasks_json(repo, source)
    assert (result.imported, result.skipped, result.failed) == (1, 1, 0)


@pytest.mark.parametrize("payload", ["{broken", "[]", '{"tasks": {}}'])
def test_malformed_json_rolls_back_without_backup(tmp_path: Path, payload: str):
    repo = SQLiteTaskRepository(tmp_path)
    source = tmp_path / "tasks.json"
    source.write_text(payload, encoding="utf-8")
    result = migrate_tasks_json(repo, source)
    assert result.failed == 1 and result.error
    assert repo.list_all() == [] and list(tmp_path.glob("tasks.json.backup.*")) == []


def test_duplicate_ids_are_not_silently_discarded(tmp_path: Path):
    repo = SQLiteTaskRepository(tmp_path)
    source = tmp_path / "tasks.json"
    _write_tasks(source, [{"job_id": "dup"}, {"job_id": "dup"}])
    result = migrate_tasks_json(repo, source)
    assert result.failed == 1 and "duplicates job_id" in result.error
    assert repo.get("dup") is None


def test_database_failure_rolls_back_entire_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = SQLiteTaskRepository(tmp_path)
    source = tmp_path / "tasks.json"
    _write_tasks(source, [
        {"job_id": "one"},
        {"job_id": "two", "artifacts": [{"kind": "srt", "path": "two.srt"}]},
    ])
    original = repo.register_artifact
    monkeypatch.setattr(repo, "register_artifact", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db failure")))
    result = migrate_tasks_json(repo, source)
    assert result.failed == 1 and "db failure" in result.error
    assert repo.get("one") is None and repo.get("two") is None
    monkeypatch.setattr(repo, "register_artifact", original)


@pytest.fixture
def manager(tmp_path: Path) -> ArtifactManager:
    repo = SQLiteTaskRepository(tmp_path / "db")
    repo.create("task-1")
    return ArtifactManager(tmp_path / "files", "task-1", repo)


def test_artifact_layout_is_task_scoped(manager: ArtifactManager):
    assert all(path.is_dir() for path in (
        manager.layout.work, manager.layout.artifacts, manager.layout.temp, manager.layout.logs
    ))
    assert manager.layout.root.name == "task-1"


def test_artifact_path_escape_and_unsafe_task_id_are_rejected(manager: ArtifactManager, tmp_path: Path):
    with pytest.raises(ArtifactPathError):
        manager.resolve("artifacts", "../outside.txt")
    with pytest.raises(ArtifactPathError):
        ArtifactManager(tmp_path, "../task", manager.repository)


def test_atomic_output_promotes_and_registers(manager: ArtifactManager):
    with manager.atomic_output("subs/result.srt", kind="translated_srt", stage="subtitle_export") as temp:
        assert temp.parent == manager.layout.temp
        temp.write_text("subtitle", encoding="utf-8")
    final = manager.layout.artifacts / "subs" / "result.srt"
    artifact = manager.repository.list_artifacts("task-1")[0]
    assert final.read_text(encoding="utf-8") == "subtitle"
    assert artifact["size_bytes"] == len(b"subtitle") and artifact["checksum"]


def test_failed_atomic_output_leaves_no_final_or_temp(manager: ArtifactManager):
    with pytest.raises(RuntimeError):
        with manager.atomic_output("failed.bin", kind="video", stage="render") as temp:
            temp.write_bytes(b"partial")
            raise RuntimeError("render failed")
    assert not (manager.layout.artifacts / "failed.bin").exists()
    assert list(manager.layout.temp.iterdir()) == []


def test_same_name_collision_preserves_files_and_supersedes_metadata(manager: ArtifactManager):
    for content in (b"old", b"new"):
        with manager.atomic_output("same.srt", kind="subtitle", stage="subtitle_export") as temp:
            temp.write_bytes(content)
    artifacts = manager.repository.list_artifacts("task-1")
    assert [a["path"] for a in artifacts] == ["artifacts/same.srt", "artifacts/same__2.srt"]
    assert [a["is_current"] for a in artifacts] == [False, True]
    assert (manager.layout.artifacts / "same.srt").read_bytes() == b"old"


def test_external_source_is_registered_without_copy(manager: ArtifactManager, tmp_path: Path):
    source = tmp_path / "large.mp4"
    source.write_bytes(b"video")
    artifact = manager.register_external_source(source)
    assert artifact["path"] == str(source.resolve())
    assert list(manager.layout.artifacts.iterdir()) == []


def test_manifest_is_database_snapshot_and_downstream_invalidation(manager: ArtifactManager):
    for name, stage in (("sub.srt", "subtitle_export"), ("video.mp4", "render")):
        with manager.atomic_output(name, kind=name, stage=stage) as temp:
            temp.write_bytes(name.encode())
    assert manager.invalidate_stages(["render"]) == 1
    manifest = json.loads(manager.layout.manifest.read_text(encoding="utf-8"))
    assert manifest["source_of_truth"] == "task_repository"
    assert [a["is_current"] for a in manifest["artifacts"]] == [True, False]
    assert (manager.layout.artifacts / "video.mp4").exists()
