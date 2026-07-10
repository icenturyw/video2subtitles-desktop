from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


ENGINE = Path(__file__).resolve().parents[1] / "localization-engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from engine.repository import TaskQuery
from engine.sqlite_repository import (
    ConcurrentUpdateError,
    DuplicateTaskError,
    SQLiteTaskRepository,
)


@pytest.fixture
def repo(tmp_path: Path) -> SQLiteTaskRepository:
    return SQLiteTaskRepository(tmp_path / "data")


def test_schema_contains_all_phase2_tables_and_indexes(repo: SQLiteTaskRepository):
    with sqlite3.connect(repo.db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"schema_migrations", "tasks", "task_stage_runs", "task_artifacts", "task_events"} <= tables
    assert {"idx_tasks_status", "idx_tasks_updated", "idx_tasks_stage", "idx_artifacts_task"} <= indexes


def test_sqlite_safety_pragmas_are_enabled(repo: SQLiteTaskRepository):
    settings = repo.pragma_settings()
    assert settings["foreign_keys"] == 1
    assert settings["journal_mode"].lower() == "wal"
    assert settings["busy_timeout"] == 5000


def test_crud_persists_across_repository_instances(repo: SQLiteTaskRepository):
    created = repo.create("job-a", {"title": "alpha"})
    updated = repo.update("job-a", status="completed", progress=100)
    reopened = SQLiteTaskRepository(repo.data_dir)
    fetched = reopened.get("job-a")
    assert created.version == 1
    assert updated and updated.version == 2
    assert fetched and fetched.status == "completed" and fetched.request_payload["title"] == "alpha"
    assert reopened.delete("job-a") is True
    assert reopened.delete("job-a") is False


def test_duplicate_job_id_is_rejected(repo: SQLiteTaskRepository):
    repo.create("same")
    with pytest.raises(DuplicateTaskError):
        repo.create("same")


def test_explicit_transaction_rolls_back_all_writes(repo: SQLiteTaskRepository):
    with pytest.raises(RuntimeError):
        with repo.transaction() as conn:
            conn.execute(
                "INSERT INTO tasks(job_id,status,current_stage,created_at,updated_at) VALUES ('rolled','pending','prepare','x','x')"
            )
            raise RuntimeError("abort")
    assert repo.get("rolled") is None


def test_optimistic_lock_rejects_stale_version(repo: SQLiteTaskRepository):
    original = repo.create("locked")
    repo.update("locked", progress=10, expected_version=original.version)
    with pytest.raises(ConcurrentUpdateError):
        repo.update("locked", progress=20, expected_version=original.version)
    assert repo.get("locked").progress == 10


def test_artifacts_are_normalized_and_persisted(repo: SQLiteTaskRepository):
    repo.create("artifact-job")
    artifact_id = repo.register_artifact(
        "artifact-job",
        {"kind": "subtitle", "path": "subtitles/a.srt", "metadata": {"lines": 2}},
    )
    artifact = repo.list_artifacts("artifact-job")[0]
    assert artifact["id"] == artifact_id
    assert artifact["metadata"] == {"lines": 2}
    assert repo.get("artifact-job").artifacts[0]["path"] == "subtitles/a.srt"


def test_keyword_search_escapes_sql_wildcards(repo: SQLiteTaskRepository):
    repo.create("literal_100%", {"title": "needle"})
    repo.create("literalX1000", {"title": "other"})
    assert [r.job_id for r in repo.search(TaskQuery(keyword="_100%")).items] == ["literal_100%"]


def test_combined_status_stage_and_date_filters(repo: SQLiteTaskRepository):
    repo.create("match")
    repo.update("match", status="error", stage="translate")
    repo.create("wrong-status")
    repo.update("wrong-status", status="completed", stage="translate")
    result = repo.search(TaskQuery(
        status="error", stage="translate",
        created_from="2000-01-01T00:00:00Z", created_to="2999-01-01T00:00:00Z",
    ))
    assert [item.job_id for item in result.items] == ["match"]


def test_pagination_and_page_size_cap(repo: SQLiteTaskRepository):
    for index in range(105):
        repo.create(f"job-{index:03d}")
    first = repo.search(TaskQuery(page=1, page_size=1000, sort_by="job_id", sort_order="asc"))
    second = repo.search(TaskQuery(page=2, page_size=100, sort_by="job_id", sort_order="asc"))
    assert first.page_size == 100 and len(first.items) == 100 and first.pages == 2
    assert [item.job_id for item in second.items] == [f"job-{i:03d}" for i in range(100, 105)]
    assert len(repo.list_all()) == 105


def test_sorting_is_deterministic_in_both_directions(repo: SQLiteTaskRepository):
    for job_id in ("c", "a", "b"):
        repo.create(job_id)
    asc = repo.search(TaskQuery(sort_by="job_id", sort_order="asc"))
    desc = repo.search(TaskQuery(sort_by="job_id", sort_order="desc"))
    assert [r.job_id for r in asc.items] == ["a", "b", "c"]
    assert [r.job_id for r in desc.items] == ["c", "b", "a"]


@pytest.mark.parametrize("field,order", [("job_id; DROP TABLE tasks", "asc"), ("job_id", "sideways")])
def test_illegal_sort_inputs_are_rejected(repo: SQLiteTaskRepository, field: str, order: str):
    with pytest.raises(ValueError):
        repo.search(TaskQuery(sort_by=field, sort_order=order))
    repo.create("still-safe")
    assert repo.exists("still-safe")
