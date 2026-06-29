"""Persistent client-side settings for Video2Subtitles."""
import json
import os
from pathlib import Path

from gpu_config import (
    SUPPORTED_COMPUTE_TYPES,
    SUPPORTED_DEVICES,
    clean_compute_type,
    clean_device,
    resolve_device_and_compute,
)


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
    # Auto prefers CUDA + float16 when an NVIDIA GPU is visible, otherwise CPU + int8.
    "device": "auto",
    "compute_type": "auto",
    # Keep MP4 by default. This protects the ChatGPT package workflow and makes
    # the default behavior explicit instead of burying it in yt-dlp arguments.
    "download_mode": "video",
    "download_quality": "best",
    "keep_downloaded_video": "true",
    "proxy_url": "",
    # Localization Engine settings
    "localization_engine_url": "http://127.0.0.1:8766",
    "localization_engine_auto_start": "true",
    "translation_provider": "openai_compatible",
    "translation_base_url": "",
    "translation_model": "",
    "translation_api_type": "auto",
    "translation_timeout": "60",
    "translation_concurrency": "8",
    "translation_max_batch_items": "50",
    "translation_output_format": "compact",
    "translation_quality": "fast",
    "translation_preset_id": "",
    "tts_preset_id": "",
    "default_target_language": "zh-CN",
    "subtitle_style_preset": "default",
    "tts_provider": "",
    "tts_voice": "",
    "tts_concurrency": "1",
    "tts_qwen_mode": "auto",
    "tts_qwen_instruct": "",
    "tts_qwen_ref_audio": "",
    "tts_qwen_ref_text": "",
    "tts_qwen_seed": "42",
    "tts_qwen_temperature": "",
    "tts_qwen_top_p": "",
    "tts_qwen_max_new_tokens": "",
    "tts_volcengine_endpoint": "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
    "tts_volcengine_api_key": "",
    "tts_volcengine_app_id": "",
    "tts_volcengine_access_key": "",
    "tts_volcengine_resource_id": "seed-tts-2.0",
    "tts_volcengine_model": "seed-tts-2.0-expressive",
    "tts_volcengine_format": "mp3",
    "tts_volcengine_sample_rate": "24000",
    "tts_volcengine_speech_rate": "0",
    "tts_volcengine_loudness_rate": "0",
    "tts_openai_base_url": "",
    "tts_openai_api_key": "",
    "tts_openai_model": "tts-1",
    "tts_openai_format": "mp3",
    "tts_openai_sample_rate": "24000",
    "tts_openai_speed": "1.0",
    "tts_consistency_mode": "stable",
    "tts_segment_gap": "0.04",
    "low_vram_mode": "true",
    "original_audio_volume": "0.0",
    # Localization dialog persistent settings
    "translation_api_key": "",
    "localization_mode": "subtitle",
    "source_language_dialog": "auto (自动检测)",
    "target_language_dialog": "zh-CN (简体中文)",
    "subtitle_mode_dialog": "bilingual (双语)",
    "export_srt": "true",
    "export_ass": "true",
    "burn_subtitles": "true",
    "embed_soft_subtitles": "false",
    "mute_original_audio": "true",
    "original_audio_volume_display": "0",
}

ENV_BY_KEY = {
    "whisper_model_dir": "WHISPER_MODEL_DIR",
    "whisper_model_path": "WHISPER_MODEL_PATH",
    "model_size": "MODEL_SIZE",
    "device": "DEVICE",
    "compute_type": "COMPUTE_TYPE",
    "download_mode": "V2S_DOWNLOAD_MODE",
    "download_quality": "V2S_DOWNLOAD_QUALITY",
    "keep_downloaded_video": "V2S_KEEP_DOWNLOADED_VIDEO",
    "proxy_url": "V2S_PROXY",
    "localization_engine_url": "LOCALIZATION_ENGINE_URL",
    "translation_base_url": "V2S_TRANSLATION_BASE_URL",
    "translation_model": "V2S_TRANSLATION_MODEL",
    "translation_api_key": "V2S_TRANSLATION_API_KEY",
    "default_target_language": "V2S_TARGET_LANGUAGE",
    "tts_volcengine_endpoint": "VOLCENGINE_TTS_ENDPOINT",
    "tts_volcengine_api_key": "VOLCENGINE_TTS_API_KEY",
    "tts_volcengine_app_id": "VOLCENGINE_TTS_APP_ID",
    "tts_volcengine_access_key": "VOLCENGINE_TTS_ACCESS_KEY",
    "tts_volcengine_resource_id": "VOLCENGINE_TTS_RESOURCE_ID",
    "tts_volcengine_model": "VOLCENGINE_TTS_MODEL",
    "tts_volcengine_format": "VOLCENGINE_TTS_FORMAT",
    "tts_volcengine_sample_rate": "VOLCENGINE_TTS_SAMPLE_RATE",
    "tts_volcengine_speech_rate": "VOLCENGINE_TTS_SPEECH_RATE",
    "tts_volcengine_loudness_rate": "VOLCENGINE_TTS_LOUDNESS_RATE",
    "tts_openai_base_url": "OPENAI_TTS_BASE_URL",
    "tts_openai_api_key": "OPENAI_TTS_API_KEY",
    "tts_openai_model": "OPENAI_TTS_MODEL",
    "tts_openai_format": "OPENAI_TTS_FORMAT",
    "tts_openai_sample_rate": "OPENAI_TTS_SAMPLE_RATE",
    "tts_openai_speed": "OPENAI_TTS_SPEED",
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
    settings["device"] = clean_device(settings.get("device", "auto"))
    settings["compute_type"] = clean_compute_type(settings.get("compute_type", "auto"))
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
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
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


def get_runtime_settings(settings=None):
    """Return settings with auto device/compute resolved for faster-whisper."""
    cleaned = _clean_settings(settings or get_effective_settings())
    resolved_device, resolved_compute = resolve_device_and_compute(
        cleaned.get("device", "auto"),
        cleaned.get("compute_type", "auto"),
    )
    runtime = cleaned.copy()
    runtime["resolved_device"] = resolved_device
    runtime["resolved_compute_type"] = resolved_compute
    return runtime


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
    runtime = get_runtime_settings(cleaned)
    env_values = cleaned.copy()
    env_values["device"] = runtime["resolved_device"]
    env_values["compute_type"] = runtime["resolved_compute_type"]
    env_values["V2S_DEVICE_SETTING"] = cleaned.get("device", "auto")
    env_values["V2S_COMPUTE_TYPE_SETTING"] = cleaned.get("compute_type", "auto")

    for key, env_name in ENV_BY_KEY.items():
        value = env_values.get(key, "").strip()
        if value:
            if overwrite or not os.environ.get(env_name):
                os.environ[env_name] = value
        elif overwrite:
            os.environ.pop(env_name, None)
    os.environ["V2S_DEVICE_SETTING"] = cleaned.get("device", "auto")
    os.environ["V2S_COMPUTE_TYPE_SETTING"] = cleaned.get("compute_type", "auto")
    return cleaned


def apply_saved_settings_to_env():
    """Apply saved settings without overriding externally supplied env vars."""
    return apply_settings_to_env(load_settings(), overwrite=False)
