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
from typing import Optional

logger = logging.getLogger("engine.workspace")

# Add parent project root to path so we can import project_workspace
_ENGINE_DIR = Path(__file__).resolve().parent.parent  # localization-engine/
_PROJECT_ROOT = _ENGINE_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def resolve_workspace(workspace_dir: str) -> Path:
    """Validate and resolve a workspace directory path.

    Args:
        workspace_dir: Path to an existing project workspace directory.

    Returns:
        Resolved Path to the workspace.

    Raises:
        ValueError: If the path is invalid or not a directory.
    """
    if not workspace_dir:
        raise ValueError("workspace_dir is required")
    ws = Path(workspace_dir).resolve()
    if not ws.exists():
        raise ValueError(f"Workspace directory does not exist: {ws}")
    if not ws.is_dir():
        raise ValueError(f"Workspace path is not a directory: {ws}")
    return ws


def ensure_log_dir(workspace_dir: Path) -> Path:
    """Ensure the logs subdirectory exists."""
    log_dir = Path(workspace_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_log_path(workspace_dir: Path, name: str = "localization.log") -> Path:
    """Get the path for a log file within the workspace."""
    log_dir = ensure_log_dir(workspace_dir)
    return log_dir / name


def get_source_subtitle(workspace_dir: Path) -> Optional[Path]:
    """Find the source subtitle file in the workspace.

    Looks for common subtitle formats in the subtitles/ directory.
    """
    subs_dir = Path(workspace_dir) / "subtitles"
    if not subs_dir.exists():
        return None
    for ext in ("srt", "ass", "vtt"):
        for f in sorted(subs_dir.iterdir()):
            if f.suffix.lower() == f".{ext}" and f.is_file():
                return f
    return None


def get_source_video(workspace_dir: Path) -> Optional[Path]:
    """Find the source video file in the workspace."""
    source_dir = Path(workspace_dir) / "source"
    if not source_dir.exists():
        return None
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
