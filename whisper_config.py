"""Shared Whisper paths and runtime options for Video2Subtitles."""
import os
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
DEFAULT_WHISPER_SERVER_DIR = APP_DIR / "whisper-server"
DEFAULT_MODEL_DIR = APP_DIR / "models"


def _path_from_env(name, default=None):
    value = os.environ.get(name, "").strip()
    if value:
        return Path(value).expanduser().resolve()
    if default is not None:
        return Path(default).expanduser().resolve()
    return None


def get_whisper_server_dir():
    """Return the Whisper server directory."""
    return _path_from_env("WHISPER_SERVER_DIR", DEFAULT_WHISPER_SERVER_DIR)


def get_model_dir():
    """Return the faster-whisper model cache/root directory."""
    return _path_from_env("WHISPER_MODEL_DIR", DEFAULT_MODEL_DIR)


def get_model_path():
    """Return a user-specified exact model directory, if provided."""
    return _path_from_env("WHISPER_MODEL_PATH")


def get_model_name_or_path():
    """Return the model identifier passed to faster-whisper."""
    model_path = get_model_path()
    if model_path:
        return str(model_path)
    return os.environ.get("MODEL_SIZE", "base").strip() or "base"


def find_python_executable():
    """Prefer bundled/project virtualenvs, then fall back to current Python."""
    whisper_server = get_whisper_server_dir()
    if os.name == "nt":
        candidates = [
            whisper_server / "venv" / "Scripts" / "python.exe",
            APP_DIR / ".venv" / "Scripts" / "python.exe",
            APP_DIR / "venv" / "Scripts" / "python.exe",
        ]
    else:
        candidates = [
            whisper_server / "venv" / "bin" / "python",
            APP_DIR / ".venv" / "bin" / "python",
            APP_DIR / "venv" / "bin" / "python",
        ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable or "python"


WHISPER_SERVER = get_whisper_server_dir()
WHISPER_MODEL_DIR = get_model_dir()
