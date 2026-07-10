"""Workspace adapter for the localization engine.

Bridges the engine's internal operations with the project_workspace module
from the parent project, providing safe file operations within each task's
isolated workspace directory.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional, Union

from engine.errors import WorkspaceError

logger = logging.getLogger("engine.workspace")

# Add parent project root to path so we can import project_workspace
_ENGINE_DIR = Path(__file__).resolve().parent.parent  # localization-engine/
_PROJECT_ROOT = _ENGINE_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


PathLike = Union[str, os.PathLike]
MANAGED_DIRECTORIES = ("input", "cache", "output", "logs")


class WorkspaceManager:
    """Own and validate all paths used by one isolated task workspace.

    ``allowed_root`` can be supplied by the service (or through
    ``V2S_WORKSPACE_ROOT``) to prevent callers from selecting a workspace
    outside the configured storage tree.  Independently of that outer guard,
    every path returned by :meth:`resolve` is checked against the task's own
    workspace, including resolved symlink targets.
    """

    def __init__(
        self,
        workspace_dir: PathLike,
        *,
        allowed_root: Optional[PathLike] = None,
        create: bool = False,
    ) -> None:
        if not workspace_dir:
            raise WorkspaceError("workspace_dir is required")

        configured_root = allowed_root or os.environ.get("V2S_WORKSPACE_ROOT")
        self._allowed_root = (
            Path(configured_root).expanduser().resolve()
            if configured_root
            else None
        )
        workspace = Path(workspace_dir).expanduser().resolve()
        if self._allowed_root is not None and not self._is_within(
            self._allowed_root, workspace
        ):
            raise WorkspaceError(
                f"Workspace is outside allowed root: {workspace}",
                error_code="WORKSPACE_OUTSIDE_ROOT",
            )

        if create:
            workspace.mkdir(parents=True, exist_ok=True)
        if not workspace.exists():
            raise WorkspaceError(f"Workspace directory does not exist: {workspace}")
        if not workspace.is_dir():
            raise WorkspaceError(f"Workspace path is not a directory: {workspace}")
        self._root = workspace

    @staticmethod
    def _is_within(base: Path, target: Path) -> bool:
        try:
            target.relative_to(base)
            return True
        except (ValueError, OSError):
            return False

    @property
    def root(self) -> Path:
        return self._root

    @property
    def allowed_root(self) -> Optional[Path]:
        return self._allowed_root

    def ensure(self) -> "WorkspaceManager":
        for directory in MANAGED_DIRECTORIES:
            self.resolve(directory).mkdir(parents=True, exist_ok=True)
        return self

    def resolve(self, *parts: PathLike, must_exist: bool = False) -> Path:
        if not parts:
            return self._root
        relative = Path(*[os.fspath(part) for part in parts])
        if relative.is_absolute():
            raise WorkspaceError(
                f"Absolute paths are not allowed inside a workspace: {relative}",
                error_code="WORKSPACE_PATH_ESCAPE",
            )
        target = (self._root / relative).resolve()
        if not self._is_within(self._root, target):
            raise WorkspaceError(
                f"Path escapes workspace: {relative}",
                error_code="WORKSPACE_PATH_ESCAPE",
            )
        if must_exist and not target.exists():
            raise WorkspaceError(
                f"Workspace path does not exist: {target}",
                error_code="WORKSPACE_PATH_NOT_FOUND",
            )
        return target

    def path(self, area: str, *parts: PathLike, must_exist: bool = False) -> Path:
        if area not in MANAGED_DIRECTORIES:
            raise WorkspaceError(
                f"Unknown workspace area: {area}",
                error_code="WORKSPACE_AREA_INVALID",
            )
        return self.resolve(area, *parts, must_exist=must_exist)

    @property
    def input_dir(self) -> Path:
        return self.path("input")

    @property
    def cache_dir(self) -> Path:
        return self.path("cache")

    @property
    def output_dir(self) -> Path:
        return self.path("output")

    @property
    def logs_dir(self) -> Path:
        return self.path("logs")


def resolve_workspace(workspace_dir: str) -> Path:
    """Validate and resolve a workspace directory path.

    Args:
        workspace_dir: Path to an existing project workspace directory.

    Returns:
        Resolved Path to the workspace.

    Raises:
        ValueError: If the path is invalid or not a directory.
    """
    return WorkspaceManager(workspace_dir).root


def ensure_log_dir(workspace_dir: Path) -> Path:
    """Ensure the logs subdirectory exists."""
    log_dir = WorkspaceManager(workspace_dir).logs_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_log_path(workspace_dir: Path, name: str = "localization.log") -> Path:
    """Get the path for a log file within the workspace."""
    log_dir = ensure_log_dir(workspace_dir)
    manager = WorkspaceManager(workspace_dir)
    path = manager.resolve("logs", name)
    if path.parent != log_dir:
        raise WorkspaceError(
            f"Log filename must not contain directories: {name}",
            error_code="WORKSPACE_PATH_ESCAPE",
        )
    return path


def get_source_subtitle(workspace_dir: Path) -> Optional[Path]:
    """Find the source subtitle file in the workspace.

    Looks for common subtitle formats in the subtitles/ or subtitle/ directory.
    """
    for dir_name in ("subtitles", "subtitle"):
        subs_dir = Path(workspace_dir) / dir_name
        if not subs_dir.exists():
            continue
        for ext in ("srt", "ass", "vtt"):
            for f in sorted(subs_dir.iterdir()):
                if f.suffix.lower() == f".{ext}" and f.is_file():
                    return f
    return None


def get_source_video(workspace_dir: Path) -> Optional[Path]:
    """Find the source video file in the workspace."""
    for subdir in ("source", "raw"):
        source_dir = Path(workspace_dir) / subdir
        if not source_dir.exists():
            continue
        video_exts = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv"}
        for f in sorted(source_dir.iterdir()):
            if f.suffix.lower() in video_exts and f.is_file():
                return f
    return None


def write_log(workspace_dir: Path, message: str,
              name: str = "localization.log") -> None:
    """Append a log line to the workspace log file."""
    try:
        log_path = get_log_path(workspace_dir, name)
        with open(log_path, "a", encoding="utf-8", errors="replace") as f:
            import time
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] {message}\n")
    except Exception as exc:
        logger.debug("Failed to write log: %s", exc)


def read_log_tail(workspace_dir: Path, max_lines: int = 100,
                  name: str = "localization.log") -> tuple[list[str], bool]:
    """Read the last N lines of a workspace log file.

    Returns:
        Tuple of (lines, truncated) where truncated indicates if the log
        had more lines than max_lines.
    """
    log_path = get_log_path(workspace_dir, name)
    if not log_path.exists():
        return [], False
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        if len(all_lines) > max_lines:
            return all_lines[-max_lines:], True
        return all_lines, False
    except Exception:
        return [], False
