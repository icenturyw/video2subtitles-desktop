"""Persistent client-side settings for Video2Subtitles."""
import json
import os
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
SETTINGS_DIR = APP_DIR / ".cache"
SETTINGS_PATH = SETTINGS_DIR / "settings.json"
DEFAULT_MODEL_DIR = APP_DIR / "models"
SUPPORTED_MODEL_SIZES = [
    "tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo",
]
SUPPORTED_DOWNLOAD_MODES = ["video", "transcribe_only", "audio"]
SUPPORTED_DOWNLOAD_QUALITIES = ["best", "720p", "480p"]

DEFAULT_SETTINGS = {
    "whisper_model_dir": str(DEFAULT_MODEL_DIR),
    "whisper_model_path": "",
    "model_size": "base",
    # Keep MP4 by default. This protects the ChatGPT package workflow and makes
    # the default behavior explicit instead of burying it in yt-dlp arguments.
    "download_mode": "video",
    "download_quality": "best",
    "keep_downloaded_video": "true",
}

ENV_BY_KEY = {
    "whisper_model_dir": "WHISPER_MODEL_DIR",
    "whisper_model_path": "WHISPER_MODEL_PATH",
    "model_size": "MODEL_SIZE",
    "download_mode": "V2S_DOWNLOAD_MODE",
    "download_quality": "V2S_DOWNLOAD_QUALITY",
    "keep_downloaded_video": "V2S_KEEP_DOWNLOADED_VIDEO",
}


def _as_bool_text(value, default="true"):
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value if value is not None else default).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return "true"
    if text in {"0", "false", "no", "off", "n"}:
        return "false"
    return default


def _clean_settings(data):
    settings = DEFAULT_SETTINGS.copy()
    if isinstance(data, dict):
        for key in settings:
            value = data.get(key)
            if value is not None:
                settings[key] = str(value).strip()
    if settings["model_size"] not in SUPPORTED_MODEL_SIZES:
        settings["model_size"] = DEFAULT_SETTINGS["model_size"]
    if not settings["whisper_model_dir"]:
        settings["whisper_model_dir"] = DEFAULT_SETTINGS["whisper_model_dir"]
    if settings["download_mode"] not in SUPPORTED_DOWNLOAD_MODES:
        settings["download_mode"] = DEFAULT_SETTINGS["download_mode"]
    if settings["download_quality"] not in SUPPORTED_DOWNLOAD_QUALITIES:
        settings["download_quality"] = DEFAULT_SETTINGS["download_quality"]
    settings["keep_downloaded_video"] = _as_bool_text(settings.get("keep_downloaded_video"), DEFAULT_SETTINGS["keep_downloaded_video"])
    if settings["download_mode"] == "video":
        settings["keep_downloaded_video"] = "true"
    return settings


def load_settings():
    """Load saved client settings, falling back to safe defaults."""
    if not SETTINGS_PATH.exists():
        return DEFAULT_SETTINGS.copy()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return _clean_settings(data)
    except Exception:
        return DEFAULT_SETTINGS.copy()


def get_effective_settings():
    """Return saved settings with explicit environment variables overlaid."""
    settings = load_settings()
    for key, env_name in ENV_BY_KEY.items():
        value = os.environ.get(env_name, "").strip()
        if value:
            settings[key] = value
    return _clean_settings(settings)


def save_settings(settings):
    """Persist client settings to .cache/settings.json."""
    current = load_settings()
    if isinstance(settings, dict):
        current.update(settings)
    cleaned = _clean_settings(current)
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return cleaned


def apply_settings_to_env(settings, overwrite=True):
    """Apply settings to the current process environment."""
    cleaned = _clean_settings(settings)
    for key, env_name in ENV_BY_KEY.items():
        value = cleaned.get(key, "").strip()
        if value:
            if overwrite or not os.environ.get(env_name):
                os.environ[env_name] = value
        elif overwrite:
            os.environ.pop(env_name, None)
    return cleaned


def apply_saved_settings_to_env():
    """Apply saved settings without overriding externally supplied env vars."""
    return apply_settings_to_env(load_settings(), overwrite=False)
