"""SQLite implementation of the task persistence boundary."""
from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from engine.repository import TaskPage, TaskQuery, TaskRecord, TaskRepository, utc_now
from engine.stages import STAGE_BY_NAME


SCHEMA_VERSION = 1
SORT_COLUMNS = {
    "job_id": "job_id",
    "status": "status",
    "stage": "current_stage",
    "progress": "progress",
    "created_at": "created_at",
    "updated_at": "updated_at",
}


class RepositoryError(RuntimeError):
    pass


class DuplicateTaskError(RepositoryError):
    pass


class ConcurrentUpdateError(RepositoryError):
    pass


class SQLiteTaskRepository(TaskRepository):
    """Thread-safe, transactional SQLite task repository.

    Connections are short lived so worker threads never share a sqlite handle.
    WAL and the busy timeout make API reads coexist with pipeline writes.
    """

    def __init__(self, data_dir: Path, *, busy_timeout_ms: int = 5000) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "tasks.sqlite3"
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        self._schema_lock = threading.Lock()
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextlib.contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize_schema(self) -> None:
        with self._schema_lock, self.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    current_stage TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
                    message TEXT NOT NULL DEFAULT '',
                    detected_language TEXT NOT NULL DEFAULT '',
                    error_code TEXT,
                    error_detail TEXT,
                    request_payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
                    run_lock_token TEXT,
                    run_lock_at TEXT
                );

                CREATE TABLE IF NOT EXISTS task_stage_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(job_id) ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error_code TEXT,
                    error_detail TEXT,
                    input_fingerprint TEXT NOT NULL DEFAULT '',
                    config_fingerprint TEXT NOT NULL DEFAULT '',
                    output_artifacts TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(task_id, stage, attempt)
                );

                CREATE TABLE IF NOT EXISTS task_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(job_id) ON DELETE CASCADE,
                    stage_run_id INTEGER REFERENCES task_stage_runs(id) ON DELETE SET NULL,
                    stage TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    language TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    checksum TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0, 1)),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(job_id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_tasks_stage ON tasks(current_stage);
                CREATE INDEX IF NOT EXISTS idx_stage_runs_task ON task_stage_runs(task_id, stage, attempt DESC);
                CREATE INDEX IF NOT EXISTS idx_stage_runs_status ON task_stage_runs(status);
                CREATE INDEX IF NOT EXISTS idx_artifacts_task ON task_artifacts(task_id, is_current, kind);
                CREATE INDEX IF NOT EXISTS idx_artifacts_stage_run ON task_artifacts(stage_run_id);
                CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id, created_at DESC);
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (SCHEMA_VERSION, "initial_task_repository", utc_now()),
            )

    @staticmethod
    def _json_dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _json_load(value: str, fallback: Any) -> Any:
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback

    def _row_to_record(self, row: sqlite3.Row, artifacts: Optional[List[Dict[str, Any]]] = None) -> TaskRecord:
        rec = TaskRecord(
            row["job_id"],
            self._json_load(row["request_payload"], {}),
            version=row["version"],
        )
        rec.status = row["status"]
        rec.stage = row["current_stage"]
        rec.progress = row["progress"]
        rec.message = row["message"]
        rec.detected_language = row["detected_language"]
        rec.error_code = row["error_code"]
        rec.error_detail = row["error_detail"]
        rec.created_at = row["created_at"]
        rec.updated_at = row["updated_at"]
        rec.artifacts = artifacts if artifacts is not None else []
        return rec

    def _artifact_rows(self, conn: sqlite3.Connection, job_id: str, *, current_only: bool = True) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM task_artifacts WHERE task_id = ?"
        params: List[Any] = [job_id]
        if current_only:
            sql += " AND is_current = 1"
        sql += " ORDER BY id"
        return [self._artifact_to_dict(row) for row in conn.execute(sql, params)]

    def create(self, job_id: str, request_payload: Optional[Dict[str, Any]] = None) -> TaskRecord:
        rec = TaskRecord(job_id, request_payload)
        try:
            with self.transaction() as conn:
                conn.execute(
                    """INSERT INTO tasks(
                        job_id, status, current_stage, progress, message,
                        detected_language, error_code, error_detail, request_payload,
                        created_at, updated_at, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        rec.job_id, rec.status, rec.stage, rec.progress, rec.message,
                        rec.detected_language, rec.error_code, rec.error_detail,
                        self._json_dump(rec.request_payload), rec.created_at,
                        rec.updated_at, rec.version,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateTaskError(f"Task already exists: {job_id}") from exc
        return rec

    def bulk_import(
        self,
        records: Sequence[TaskRecord],
        *,
        source: str,
        source_sha256: str,
    ) -> tuple[int, int]:
        """Import legacy records in one transaction, skipping existing ids."""
        imported = 0
        skipped = 0
        with self.transaction() as conn:
            for rec in records:
                exists = conn.execute(
                    "SELECT 1 FROM tasks WHERE job_id = ?", (rec.job_id,)
                ).fetchone()
                if exists:
                    skipped += 1
                    continue
                conn.execute(
                    """INSERT INTO tasks(
                        job_id, status, current_stage, progress, message,
                        detected_language, error_code, error_detail, request_payload,
                        created_at, updated_at, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        rec.job_id, rec.status, rec.stage, rec.progress, rec.message,
                        rec.detected_language, rec.error_code, rec.error_detail,
                        self._json_dump(rec.request_payload), rec.created_at,
                        rec.updated_at, rec.version,
                    ),
                )
                for artifact in rec.artifacts:
                    self.register_artifact(rec.job_id, artifact, conn=conn)
                self.add_event(
                    rec.job_id,
                    "JSON_MIGRATED",
                    "Imported from legacy tasks.json",
                    {"source": source, "sha256": source_sha256},
                    conn=conn,
                )
                imported += 1
        return imported, skipped

    def get(self, job_id: str) -> Optional[TaskRecord]:
        with self.transaction(immediate=False) as conn:
            row = conn.execute("SELECT * FROM tasks WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_record(row, self._artifact_rows(conn, job_id))

    def exists(self, job_id: str) -> bool:
        with self.transaction(immediate=False) as conn:
            return conn.execute("SELECT 1 FROM tasks WHERE job_id = ?", (job_id,)).fetchone() is not None

    def list_all(self) -> List[TaskRecord]:
        return self.search(TaskQuery(page=1, page_size=100, sort_by="created_at", sort_order="desc")).items

    def update(self, job_id: str, **kwargs: Any) -> Optional[TaskRecord]:
        expected_version = kwargs.pop("expected_version", None)
        aliases = {"stage": "current_stage"}
        allowed = {
            "status", "current_stage", "progress", "message", "detected_language",
            "error_code", "error_detail", "request_payload",
        }
        updates: Dict[str, Any] = {}
        for key, value in kwargs.items():
            column = aliases.get(key, key)
            if column in allowed:
                updates[column] = self._json_dump(value) if column == "request_payload" else value
        if not updates:
            return self.get(job_id)
        updates["updated_at"] = utc_now()
        with self.transaction() as conn:
            current = conn.execute("SELECT * FROM tasks WHERE job_id = ?", (job_id,)).fetchone()
            if current is None:
                return None
            version = int(current["version"] if expected_version is None else expected_version)
            assignments = ", ".join(f"{column} = ?" for column in updates)
            cursor = conn.execute(
                f"UPDATE tasks SET {assignments}, version = version + 1 WHERE job_id = ? AND version = ?",
                [*updates.values(), job_id, version],
            )
            if cursor.rowcount != 1:
                raise ConcurrentUpdateError(f"Task {job_id} version changed from {version}")
            self._track_stage_transition(conn, current, updates)
            effective_status = str(updates.get("status", current["status"]))
            if effective_status in {"completed", "error", "failed", "cancelled", "interrupted", "paused"}:
                conn.execute(
                    "UPDATE tasks SET run_lock_token = NULL, run_lock_at = NULL WHERE job_id = ?",
                    (job_id,),
                )
            row = conn.execute("SELECT * FROM tasks WHERE job_id = ?", (job_id,)).fetchone()
            return self._row_to_record(row, self._artifact_rows(conn, job_id))

    def add_artifact(self, job_id: str, artifact: Dict[str, Any]) -> Optional[TaskRecord]:
        self.register_artifact(job_id, artifact)
        return self.get(job_id)

    def delete(self, job_id: str) -> bool:
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE job_id = ?", (job_id,))
            return cursor.rowcount == 1

    def search(self, query: TaskQuery) -> TaskPage:
        if query.sort_by not in SORT_COLUMNS:
            raise ValueError(f"Unsupported sort field: {query.sort_by}")
        order = query.sort_order.lower()
        if order not in {"asc", "desc"}:
            raise ValueError(f"Unsupported sort order: {query.sort_order}")
        page = max(1, int(query.page))
        page_size = max(1, min(100, int(query.page_size)))
        where: List[str] = []
        params: List[Any] = []
        if query.keyword.strip():
            where.append("(job_id LIKE ? ESCAPE '\\' OR message LIKE ? ESCAPE '\\' OR request_payload LIKE ? ESCAPE '\\')")
            escaped = query.keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.extend([f"%{escaped}%"] * 3)
        if query.status:
            where.append("status = ?")
            params.append(query.status)
        if query.stage:
            where.append("current_stage = ?")
            params.append(query.stage)
        if query.created_from:
            where.append("created_at >= ?")
            params.append(query.created_from)
        if query.created_to:
            where.append("created_at <= ?")
            params.append(query.created_to)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self.transaction(immediate=False) as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM tasks{clause}", params).fetchone()[0])
            rows = conn.execute(
                f"SELECT * FROM tasks{clause} ORDER BY {SORT_COLUMNS[query.sort_by]} {order.upper()}, job_id ASC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            records = [self._row_to_record(row, self._artifact_rows(conn, row["job_id"])) for row in rows]
        return TaskPage(records, total, page, page_size)

    def register_artifact(
        self,
        job_id: str,
        artifact: Dict[str, Any],
        *,
        stage_run_id: Optional[int] = None,
        is_current: bool = True,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        def insert(db: sqlite3.Connection) -> int:
            task = db.execute("SELECT current_stage FROM tasks WHERE job_id = ?", (job_id,)).fetchone()
            if task is None:
                raise RepositoryError(f"Task not found: {job_id}")
            resolved_stage_run_id = stage_run_id
            if resolved_stage_run_id is None:
                active = db.execute(
                    "SELECT id FROM task_stage_runs WHERE task_id = ? AND status = 'running' ORDER BY id DESC LIMIT 1",
                    (job_id,),
                ).fetchone()
                resolved_stage_run_id = int(active["id"]) if active else None
            cursor = db.execute(
                """INSERT INTO task_artifacts(
                    task_id, stage_run_id, stage, kind, path, language, size_bytes,
                    checksum, metadata, is_current, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id, resolved_stage_run_id, str(artifact.get("stage") or task["current_stage"]),
                    str(artifact.get("kind") or "unknown"), str(artifact.get("path") or ""),
                    str(artifact.get("language") or ""), int(artifact.get("size_bytes") or 0),
                    str(artifact.get("checksum") or artifact.get("sha256") or ""),
                    self._json_dump(artifact.get("metadata") or {}), int(bool(is_current)), utc_now(),
                ),
            )
            if resolved_stage_run_id is not None:
                run = db.execute(
                    "SELECT output_artifacts FROM task_stage_runs WHERE id = ?",
                    (resolved_stage_run_id,),
                ).fetchone()
                output_ids = self._json_load(run["output_artifacts"], []) if run else []
                output_ids.append(int(cursor.lastrowid))
                db.execute(
                    "UPDATE task_stage_runs SET output_artifacts = ? WHERE id = ?",
                    (self._json_dump(output_ids), resolved_stage_run_id),
                )
            db.execute("UPDATE tasks SET updated_at = ?, version = version + 1 WHERE job_id = ?", (utc_now(), job_id))
            return int(cursor.lastrowid)
        if conn is not None:
            return insert(conn)
        with self.transaction() as db:
            return insert(db)

    @staticmethod
    def _artifact_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        metadata = json.loads(row["metadata"] or "{}")
        return {
            "id": row["id"], "kind": row["kind"], "path": row["path"],
            "language": row["language"], "size_bytes": row["size_bytes"],
            "checksum": row["checksum"], "stage": row["stage"],
            "stage_run_id": row["stage_run_id"], "is_current": bool(row["is_current"]),
            "created_at": row["created_at"], "metadata": metadata,
        }

    def list_artifacts(self, job_id: str, *, current_only: bool = False) -> List[Dict[str, Any]]:
        with self.transaction(immediate=False) as conn:
            return self._artifact_rows(conn, job_id, current_only=current_only)

    def invalidate_artifacts(self, job_id: str, stages: Sequence[str]) -> int:
        if not stages:
            return 0
        placeholders = ",".join("?" for _ in stages)
        with self.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE task_artifacts SET is_current = 0 WHERE task_id = ? AND is_current = 1 AND stage IN ({placeholders})",
                [job_id, *stages],
            )
            if cursor.rowcount:
                conn.execute("UPDATE tasks SET updated_at = ?, version = version + 1 WHERE job_id = ?", (utc_now(), job_id))
            return cursor.rowcount

    def supersede_artifacts(self, job_id: str, kind: str) -> int:
        """Mark older artifacts of one logical kind as non-current."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE task_artifacts SET is_current = 0 WHERE task_id = ? AND kind = ? AND is_current = 1",
                (job_id, kind),
            )
            if cursor.rowcount:
                conn.execute(
                    "UPDATE tasks SET updated_at = ?, version = version + 1 WHERE job_id = ?",
                    (utc_now(), job_id),
                )
            return cursor.rowcount

    def add_event(
        self,
        job_id: str,
        event_type: str,
        message: str = "",
        payload: Optional[Dict[str, Any]] = None,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        def insert(db: sqlite3.Connection) -> int:
            cursor = db.execute(
                "INSERT INTO task_events(task_id, event_type, message, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (job_id, event_type, message, self._json_dump(payload or {}), utc_now()),
            )
            return int(cursor.lastrowid)
        if conn is not None:
            return insert(conn)
        with self.transaction() as db:
            return insert(db)

    def list_events(self, job_id: str) -> List[Dict[str, Any]]:
        with self.transaction(immediate=False) as conn:
            rows = conn.execute("SELECT * FROM task_events WHERE task_id = ? ORDER BY id", (job_id,))
            return [
                {
                    "id": row["id"], "event_type": row["event_type"],
                    "message": row["message"], "payload": self._json_load(row["payload"], {}),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def _stage_fingerprints(
        self, conn: sqlite3.Connection, job_id: str, stage: str
    ) -> tuple[str, str]:
        task = conn.execute(
            "SELECT request_payload FROM tasks WHERE job_id = ?", (job_id,)
        ).fetchone()
        request = self._json_load(task["request_payload"], {}) if task else {}
        config_keys = STAGE_BY_NAME.get(stage).config_keys if stage in STAGE_BY_NAME else ()
        config = {key: request.get(key) for key in config_keys}
        config_fingerprint = hashlib.sha256(
            self._json_dump(config).encode("utf-8")
        ).hexdigest()
        inputs = [
            {"kind": row["kind"], "path": row["path"], "checksum": row["checksum"]}
            for row in conn.execute(
                "SELECT kind, path, checksum FROM task_artifacts WHERE task_id = ? AND is_current = 1 AND stage != ? ORDER BY id",
                (job_id, stage),
            )
        ]
        input_fingerprint = hashlib.sha256(
            self._json_dump({"config": config, "artifacts": inputs}).encode("utf-8")
        ).hexdigest()
        return input_fingerprint, config_fingerprint

    def _begin_stage_run_tx(self, conn: sqlite3.Connection, job_id: str, stage: str) -> int:
        active = conn.execute(
            "SELECT id FROM task_stage_runs WHERE task_id = ? AND status = 'running' ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if active:
            return int(active["id"])
        attempt = int(conn.execute(
            "SELECT COALESCE(MAX(attempt), 0) + 1 FROM task_stage_runs WHERE task_id = ? AND stage = ?",
            (job_id, stage),
        ).fetchone()[0])
        input_fingerprint, config_fingerprint = self._stage_fingerprints(conn, job_id, stage)
        cursor = conn.execute(
            """INSERT INTO task_stage_runs(
                task_id, stage, attempt, status, started_at,
                input_fingerprint, config_fingerprint, output_artifacts
            ) VALUES (?, ?, ?, 'running', ?, ?, ?, '[]')""",
            (job_id, stage, attempt, utc_now(), input_fingerprint, config_fingerprint),
        )
        return int(cursor.lastrowid)

    def _finish_active_run_tx(
        self,
        conn: sqlite3.Connection,
        job_id: str,
        status: str,
        *,
        error_code: Optional[str] = None,
        error_detail: Optional[str] = None,
    ) -> Optional[int]:
        active = conn.execute(
            "SELECT id FROM task_stage_runs WHERE task_id = ? AND status = 'running' ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if not active:
            return None
        conn.execute(
            "UPDATE task_stage_runs SET status = ?, finished_at = ?, error_code = ?, error_detail = ? WHERE id = ?",
            (status, utc_now(), error_code, error_detail, active["id"]),
        )
        return int(active["id"])

    def _track_stage_transition(
        self, conn: sqlite3.Connection, current: sqlite3.Row, updates: Dict[str, Any]
    ) -> None:
        job_id = current["job_id"]
        old_status = current["status"]
        old_stage = current["current_stage"]
        new_status = str(updates.get("status", old_status))
        new_stage = str(updates.get("current_stage", old_stage))
        if old_status == "running" and new_stage != old_stage:
            terminal = "cancelled" if new_status == "cancelled" else "completed"
            self._finish_active_run_tx(conn, job_id, terminal)
        if new_status == "running" and new_stage in STAGE_BY_NAME:
            self._begin_stage_run_tx(conn, job_id, new_stage)
        elif new_status in {"error", "failed"}:
            self._finish_active_run_tx(
                conn, job_id, "failed",
                error_code=updates.get("error_code"),
                error_detail=updates.get("error_detail") or updates.get("message"),
            )
        elif new_status in {"cancelled", "interrupted", "paused"}:
            self._finish_active_run_tx(
                conn, job_id, "interrupted" if new_status in {"interrupted", "paused"} else "cancelled",
                error_code=updates.get("error_code"),
                error_detail=updates.get("error_detail") or updates.get("message"),
            )
        elif new_status == "completed":
            self._finish_active_run_tx(conn, job_id, "completed")

    def begin_stage_run(self, job_id: str, stage: str) -> int:
        if stage not in STAGE_BY_NAME:
            raise ValueError(f"Unknown pipeline stage: {stage}")
        with self.transaction() as conn:
            run_id = self._begin_stage_run_tx(conn, job_id, stage)
            conn.execute(
                "UPDATE tasks SET status = 'running', current_stage = ?, updated_at = ?, version = version + 1 WHERE job_id = ?",
                (stage, utc_now(), job_id),
            )
            return run_id

    def finish_stage_run(
        self,
        run_id: int,
        status: str,
        *,
        error_code: Optional[str] = None,
        error_detail: Optional[str] = None,
    ) -> None:
        if status not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ValueError(f"Invalid stage run status: {status}")
        with self.transaction() as conn:
            cursor = conn.execute(
                """UPDATE task_stage_runs SET status = ?, finished_at = ?,
                   error_code = ?, error_detail = ? WHERE id = ? AND status = 'running'""",
                (status, utc_now(), error_code, error_detail, run_id),
            )
            if cursor.rowcount != 1:
                raise RepositoryError(f"Stage run is not active: {run_id}")

    def list_stage_runs(self, job_id: str) -> List[Dict[str, Any]]:
        with self.transaction(immediate=False) as conn:
            rows = conn.execute(
                "SELECT * FROM task_stage_runs WHERE task_id = ? ORDER BY id", (job_id,)
            )
            return [
                {
                    "id": row["id"], "stage": row["stage"], "attempt": row["attempt"],
                    "status": row["status"], "started_at": row["started_at"],
                    "finished_at": row["finished_at"], "error_code": row["error_code"],
                    "error_detail": row["error_detail"],
                    "input_fingerprint": row["input_fingerprint"],
                    "config_fingerprint": row["config_fingerprint"],
                    "output_artifacts": self._json_load(row["output_artifacts"], []),
                }
                for row in rows
            ]

    def last_completed_stage_run(self, job_id: str, stage: str) -> Optional[Dict[str, Any]]:
        runs = [
            run for run in self.list_stage_runs(job_id)
            if run["stage"] == stage and run["status"] == "completed"
        ]
        return runs[-1] if runs else None

    def stage_config_fingerprint(self, stage: str, request_payload: Dict[str, Any]) -> str:
        if stage not in STAGE_BY_NAME:
            raise ValueError(f"Unknown pipeline stage: {stage}")
        keys = STAGE_BY_NAME[stage].config_keys
        config = {key: request_payload.get(key) for key in keys}
        return hashlib.sha256(self._json_dump(config).encode("utf-8")).hexdigest()

    def acquire_run_lease(self, job_id: str, token: str) -> bool:
        with self.transaction() as conn:
            cursor = conn.execute(
                """UPDATE tasks SET run_lock_token = ?, run_lock_at = ?,
                   updated_at = ?, version = version + 1
                   WHERE job_id = ? AND run_lock_token IS NULL
                   AND status NOT IN ('running', 'pending')""",
                (token, utc_now(), utc_now(), job_id),
            )
            return cursor.rowcount == 1

    def release_run_lease(self, job_id: str, token: str) -> bool:
        with self.transaction() as conn:
            cursor = conn.execute(
                """UPDATE tasks SET run_lock_token = NULL, run_lock_at = NULL,
                   updated_at = ?, version = version + 1
                   WHERE job_id = ? AND run_lock_token = ?""",
                (utc_now(), job_id, token),
            )
            return cursor.rowcount == 1

    def run_lease(self, job_id: str) -> Optional[str]:
        with self.transaction(immediate=False) as conn:
            row = conn.execute(
                "SELECT run_lock_token FROM tasks WHERE job_id = ?", (job_id,)
            ).fetchone()
            return str(row["run_lock_token"]) if row and row["run_lock_token"] else None

    def pragma_settings(self) -> Dict[str, Any]:
        """Expose effective settings for diagnostics and tests."""
        conn = self._connect()
        try:
            return {
                "foreign_keys": int(conn.execute("PRAGMA foreign_keys").fetchone()[0]),
                "journal_mode": str(conn.execute("PRAGMA journal_mode").fetchone()[0]),
                "busy_timeout": int(conn.execute("PRAGMA busy_timeout").fetchone()[0]),
            }
        finally:
            conn.close()
