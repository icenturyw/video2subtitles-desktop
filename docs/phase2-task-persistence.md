# Phase 2: task persistence and recovery

## Architecture

The localization sidecar now uses `TaskRepository` as the persistence boundary.
`JsonTaskRepository` remains available for compatibility, while production
startup selects `SQLiteTaskRepository`. API and pipeline code never issue SQL.

The SQLite database is `localization-engine/data/tasks.sqlite3`. Each connection
enables foreign keys, WAL, and a 5-second busy timeout. Mutations use explicit
transactions; task updates increment `version` and optionally accept
`expected_version` for optimistic locking.

The pipeline stage registry follows the actual order:

1. `prepare`
2. `normalize`
3. `translate`
4. `subtitle_export`
5. `tts`
6. `audio_mix`
7. `render`
8. `finalize`

Every transition creates a new immutable attempt. Failed attempts retain their
error and output references. Task stage updates and attempt transitions commit
in the same database transaction.

## Database tables

| Table | Purpose |
| --- | --- |
| `schema_migrations` | Applied schema versions |
| `tasks` | Current task state, request, optimistic version, retry lease |
| `task_stage_runs` | Per-stage attempts, fingerprints, errors and output IDs |
| `task_artifacts` | Current and superseded output metadata |
| `task_events` | Migration, retry and recovery audit events |

Indexes cover task status, update time, current stage, stage-run foreign keys,
artifact ownership/current state, and event ownership.

## Legacy JSON migration

At startup, `tasks.json` is validated before import. If new records exist, the
file is copied to `tasks.json.backup.YYYYMMDD_HHMMSS` and all records are
imported in one transaction. Existing job IDs are skipped, repeated startup is
idempotent, and neither the source nor its backup is deleted. Parse, validation,
or database failures return an explicit error and roll back the whole import.

## Artifacts

`ArtifactManager` creates `work`, `artifacts`, `temp`, and `logs` below an
isolated task directory. Relative paths and resolved symlinks are contained
within that task. Writers produce a temporary file and successful outputs are
promoted with `os.replace`. Name collisions receive a revision suffix; previous
files remain on disk and only their database row becomes non-current.

`manifest.json` is an atomic export of repository data and is never read as an
independent source of truth. Source videos can be registered as external files
without copying them.

## History and recovery API

- `GET /jobs`: keyword/status/stage/date filters, page/page size, safe sorting.
- `GET /jobs/{id}/detail`: task, stage attempts, all artifacts and events.
- `POST /jobs/{id}/retry`: retry from `from_stage`; `failed` and `all` are valid.
- `POST /jobs/{id}/retry-failed`: retry the newest failed/interrupted attempt.
- `POST /jobs/{id}/rerun`: rerun from `prepare`.
- `POST /jobs/{id}/resume`: rerun the current stage of an interrupted task.

Retry planning validates completed upstream stages and required files. Changed
stage configuration can move the start earlier. Downstream artifacts become
non-current without file deletion. A persisted lease prevents concurrent retry
requests for one task.

On startup, leftover `running` or `pending` tasks become `interrupted`, their
active attempt is closed, retry leases are cleared, and a `PROCESS_INTERRUPTED`
error/event is recorded.

## Manual acceptance

1. Start the sidecar with an existing `data/tasks.json`; verify the timestamped
   backup, database creation, migration counts in logs, and original JSON.
2. Create several jobs; query `/jobs` with combined filters and page through the
   results. Open `/jobs/{id}/detail` and inspect attempts and artifacts.
3. Cause translation to fail, use `retry-failed`, and verify a second translate
   attempt appears while the first failure remains.
4. Rerun from `subtitle_export`; verify downstream artifact rows become
   non-current and old files remain present.
5. Double-click retry or issue two requests concurrently; one must return a 409.
6. Terminate the process during rendering, restart, verify `interrupted` and
   `PROCESS_INTERRUPTED`, then call `/resume`.
7. Open the desktop history dialog and verify keyword/status filters and 50-row
   paging retain the existing table interactions.
