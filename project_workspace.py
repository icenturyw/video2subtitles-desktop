"""Project workspace management for task isolation and safe file handling."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from subtitle_utils import sanitize_filename

# Windows reserved characters and names
_WINDOWS_RESERVED = re.compile(r'[<>:"|?*]')
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Subdirectories created for each project workspace
WORKSPACE_SUBDIRS = (
    "source",
    "subtitles",
    "translation",
    "audio",
    "audio/tts",
    "rendered",
    "logs",
)


def safe_filename(name: Any, fallback: str = "video") -> str:
    """Create a safe filename from an arbitrary string.

    - Replaces Windows-illegal characters
    - Strips leading/trailing dots and spaces
    - Handles Windows reserved names
    - Preserves Unicode (Chinese, Japanese, etc.)
    - Truncates overly long names to 120 characters
    """
    base = sanitize_filename(name, fallback)

    # Handle Windows reserved names (e.g. CON, NUL)
    stem = base.split(".")[0].upper() if base else ""
    if stem in _WINDOWS_RESERVED_NAMES:
        base = f"_{base}"

    # Truncate if too long (preserve extension space)
    if len(base) > 120:
        base = base[:120].rstrip("._")

    return base if base else fallback


def safe_dirname(name: Any, job_id: str, fallback: str = "video") -> str:
    """Create a project directory name from title and job_id.

    Format: <safe_title>__<job_id_short>
    """
    title_part = safe_filename(name, fallback)
    id_part = job_id[:8] if job_id else uuid.uuid4().hex[:8]
    return f"{title_part}__{id_part}"


def validate_path_within(base_dir: Path, target_path: Path) -> bool:
    """Check that target_path is within base_dir (prevents path traversal)."""
    try:
        base = Path(base_dir).resolve()
        target = Path(target_path).resolve()
        target.relative_to(base)
        return True
    except (ValueError, OSError):
        return False


def ensure_workspace_dirs(workspace_dir: Path) -> None:
    """Create the standard subdirectory structure for a workspace."""
    workspace = Path(workspace_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    for subdir in WORKSPACE_SUBDIRS:
        (workspace / subdir).mkdir(parents=True, exist_ok=True)


def workspace_path(workspace_dir: Path, *parts: str) -> Path:
    """Build a path within a workspace, validating it stays inside."""
    base = Path(workspace_dir).resolve()
    target = (base / Path(*parts)).resolve()
    if not validate_path_within(base, target):
        raise ValueError(
            f"Path traversal detected: {target} is outside workspace {base}"
        )
    return target


def file_fingerprint(file_path: Path) -> str:
    """Compute SHA-256 fingerprint of a file (first 64 hex chars)."""
    sha = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                sha.update(chunk)
        return sha.hexdigest()
    except (OSError, IOError):
        return ""


def atomic_write_json(file_path: Path, data: Any) -> None:
    """Atomically write JSON data to a file using write-to-temp + rename."""
    target = Path(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in the same directory (for atomic rename on same fs)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # Atomic rename (works on Windows for new files,
        # on POSIX always atomic)
        if os.path.exists(str(target)):
            os.replace(str(tmp_path), str(target))
        else:
            os.rename(str(tmp_path), str(target))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def clean_stage_artifacts(workspace_dir: Path, stage: str) -> None:
    """Remove artifacts from a specific pipeline stage, preserving others.

    Only removes known stage output locations.
    """
    ws = Path(workspace_dir)

    stage_cleanup = {
        "translate": [
            ws / "subtitles" / "*.srt",
            ws / "subtitles" / "*.ass",
            ws / "translation" / "segments.json",
            ws / "translation" / "checkpoints",
        ],
        "subtitle_export": [
            ws / "subtitles" / "*.srt",
            ws / "subtitles" / "*.ass",
        ],
        "tts": [
            ws / "audio" / "tts" / "*.wav",
            ws / "audio" / "tts" / "index.json",
        ],
        "render": [
            ws / "rendered" / "*.mp4",
            ws / "rendered" / "*.partial",
        ],
    }

    import glob as glob_mod
    patterns = stage_cleanup.get(stage, [])
    for pattern in patterns:
        for match in glob_mod.glob(str(pattern)):
            try:
                os.unlink(match)
            except OSError:
                pass


def create_project_workspace(
    output_root: Path,
    title: str,
    job_id: Optional[str] = None,
) -> Path:
    """Create a new project workspace under output_root.

    Args:
        output_root: Root output directory (e.g., project output/).
        title: Video title for readable naming.
        job_id: UUID for the job; generated if not provided.

    Returns:
        Path to the created workspace directory.
    """
    if job_id is None:
        job_id = str(uuid.uuid4())

    dirname = safe_dirname(title, job_id)
    workspace = Path(output_root) / dirname

    if workspace.exists():
        # Avoid collision by appending counter
        for i in range(2, 100):
            candidate = Path(output_root) / f"{dirname}_{i}"
            if not candidate.exists():
                workspace = candidate
                break

    ensure_workspace_dirs(workspace)

    # Write a small lock/metadata file
    meta = {
        "job_id": job_id,
        "title": title,
        "workspace_dir": str(workspace),
        "created_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
    }
    atomic_write_json(workspace / ".workspace.json", meta)

    return workspace


class ProjectWorkspace:
    """Manages a single project workspace directory."""

    def __init__(self, workspace_dir: Path):
        self._dir = Path(workspace_dir).resolve()

    @property
    def dir(self) -> Path:
        return self._dir

    @property
    def source_dir(self) -> Path:
        return self._dir / "source"

    @property
    def subtitles_dir(self) -> Path:
        return self._dir / "subtitles"

    @property
    def translation_dir(self) -> Path:
        return self._dir / "translation"

    @property
    def audio_dir(self) -> Path:
        return self._dir / "audio"

    @property
    def tts_dir(self) -> Path:
        return self._dir / "audio" / "tts"

    @property
    def rendered_dir(self) -> Path:
        return self._dir / "rendered"

    @property
    def logs_dir(self) -> Path:
        return self._dir / "logs"

    @property
    def manifest_path(self) -> Path:
        return self._dir / "manifest.json"

    def path(self, *parts: str) -> Path:
        """Build a validated path within this workspace."""
        return workspace_path(self._dir, *parts)

    def ensure(self) -> None:
        """Create workspace directories if they don't exist."""
        ensure_workspace_dirs(self._dir)

    def read_meta(self) -> Dict[str, Any]:
        """Read workspace metadata."""
        meta_path = self._dir / ".workspace.json"
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def clean_stage(self, stage: str) -> None:
        """Clean artifacts from a specific pipeline stage."""
        clean_stage_artifacts(self._dir, stage)
