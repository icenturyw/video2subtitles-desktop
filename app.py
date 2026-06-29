#!/usr/bin/env python3
"""Video2Subtitles - 视频字幕生成桌面客户端"""

import json as _json
import sys
import os
import time
from pathlib import Path
from typing import Optional

from client_settings import apply_saved_settings_to_env, get_effective_settings

# Apply persisted client settings before importing modules that read env vars.
apply_saved_settings_to_env()

from whisper_config import WHISPER_SERVER, WHISPER_MODEL_DIR

APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / ".cache"
LOCALIZATION_ENGINE_DIR = APP_DIR / "localization-engine"

# Add the bundled/custom whisper server to path so we can optionally use it directly.
# Default location: ./whisper-server. Override with WHISPER_SERVER_DIR when needed.
if WHISPER_SERVER.exists():
    sys.path.insert(0, str(WHISPER_SERVER))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont
from main_window import MainWindow, apply_theme
import settings_patch
import gpu_patch
import output_patch
import error_log_patch
import title_fetch_patch
import playlist_patch

settings_patch.install()
gpu_patch.install()
output_patch.install()
error_log_patch.install()
title_fetch_patch.install()
playlist_patch.install()

# ---------------------------------------------------------------------------
# Sidecar managers (initialized in main(), reused by restart_whisper_server)
# ---------------------------------------------------------------------------
from services.sidecar_manager import SidecarManager

_whisper_manager: SidecarManager | None = None
_localization_manager: SidecarManager | None = None
_qwen3_tts_manager: SidecarManager | None = None
_sidecars_shutting_down = False
_sidecars_shutdown_done = False


def _env_flag_enabled(value: str | None, *, default: bool = True) -> bool:
    """Parse a user-facing boolean environment/config value."""
    if value is None:
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off", "disable", "disabled")


def _should_stop_sidecars_on_exit() -> bool:
    """Return whether application exit should stop bundled sidecar services.

    Defaults to True because Whisper/Localization/Qwen3-TTS are app-managed
    helper services and Qwen3-TTS may keep GPU memory allocated after the UI
    closes. Advanced users can keep services alive by setting either
    V2S_STOP_SIDECARS_ON_EXIT=false or stop_sidecars_on_exit=false in settings.
    """
    env_value = os.environ.get("V2S_STOP_SIDECARS_ON_EXIT")
    if env_value is not None:
        return _env_flag_enabled(env_value, default=True)
    try:
        settings = get_effective_settings()
        return _env_flag_enabled(settings.get("stop_sidecars_on_exit"), default=True)
    except Exception:
        return True


def _set_service_status(status, detail=""):
    """Expose sidecar startup details to the already-running UI process."""
    os.environ["V2S_WHISPER_SERVICE_STATUS"] = status
    os.environ["V2S_WHISPER_SERVICE_DETAIL"] = detail
    os.environ["V2S_WHISPER_SERVICE_LOG"] = str(CACHE_DIR / "whisper-service.log")


def _set_localization_status(status, detail=""):
    """Expose localization engine status to the UI process."""
    os.environ["V2S_LOCALIZATION_STATUS"] = status
    os.environ["V2S_LOCALIZATION_DETAIL"] = detail
    os.environ["V2S_LOCALIZATION_LOG"] = str(CACHE_DIR / "localization-service.log")


def _set_qwen3_tts_status(status, detail=""):
    """Expose Qwen3-TTS service status to the UI process."""
    os.environ["V2S_QWEN3_TTS_STATUS"] = status
    os.environ["V2S_QWEN3_TTS_DETAIL"] = detail
    os.environ["V2S_QWEN3_TTS_LOG"] = str(CACHE_DIR / "qwen3-tts-service.log")


def _build_whisper_manager() -> SidecarManager:
    """Create a SidecarManager configured for the Whisper server."""
    def _find_script():
        for name in ("main.py", "server.py"):
            candidate = WHISPER_SERVER / name
            if candidate.exists():
                return candidate.name
        return "main.py"

    return SidecarManager(
        name="Whisper",
        port=8765,
        service_dir=WHISPER_SERVER,
        script_name=_find_script(),
        log_filename="whisper-service.log",
        log_dir=CACHE_DIR,
        extra_env={
            "API_AUTH_KEY": "",
            "WHISPER_SERVER_DIR": str(WHISPER_SERVER),
            "WHISPER_MODEL_DIR": str(WHISPER_MODEL_DIR),
        },
        extra_venv_dirs=[APP_DIR],
        startup_timeout=10.0,
        status_callback=lambda s, d: _set_service_status(s, d),
    )


def _build_localization_manager() -> SidecarManager:
    """Create a SidecarManager configured for the Localization Engine."""
    return SidecarManager(
        name="Localization Engine",
        port=8766,
        service_dir=LOCALIZATION_ENGINE_DIR,
        script_name="main.py",
        log_filename="localization-service.log",
        log_dir=CACHE_DIR,
        extra_venv_dirs=[APP_DIR],
        startup_timeout=10.0,
        status_callback=lambda s, d: _set_localization_status(s, d),
    )


def _build_qwen3_tts_manager() -> SidecarManager:
    """Create a SidecarManager configured for the Qwen3-TTS service."""
    QWEN3_TTS_DIR = APP_DIR / "qwen3-tts-engine"
    return SidecarManager(
        name="Qwen3-TTS",
        port=8767,
        service_dir=QWEN3_TTS_DIR,
        script_name="main.py",
        log_filename="qwen3-tts-service.log",
        log_dir=CACHE_DIR,
        extra_venv_dirs=[APP_DIR],
        startup_timeout=30.0,  # model loading can be slow
        status_callback=lambda s, d: _set_qwen3_tts_status(s, d),
    )


def _ensure_whisper_server() -> bool:
    """Start the Whisper sidecar. Checks for device mismatch and restarts if needed."""
    global _whisper_manager
    if _sidecars_shutting_down:
        _set_service_status("stopping", "应用正在退出，跳过启动本地 Whisper 服务。")
        return False
    if _whisper_manager is None:
        _whisper_manager = _build_whisper_manager()

    expected_device = os.environ.get("DEVICE", "cpu")

    # If already running, check device match
    info = _whisper_manager.get_server_info()
    if info is not None:
        current_device = info.get("device")
        if current_device and current_device != expected_device:
            _set_service_status(
                "restarting",
                f"服务设备({current_device})与设置({expected_device})不匹配，正在重启...",
            )
            _whisper_manager.shutdown()
            time.sleep(1)
        else:
            _set_service_status("already_running", "本地 Whisper 服务已经在运行。")
            return True

    return _whisper_manager.ensure_running()


def restart_whisper_server():
    """Kill the running sidecar and start a fresh one with current env vars."""
    global _whisper_manager
    if _whisper_manager is None:
        _whisper_manager = _build_whisper_manager()
    return _whisper_manager.restart()


def _ensure_localization_engine() -> bool:
    """Start the Localization Engine sidecar if auto-start is enabled."""
    global _localization_manager
    if _sidecars_shutting_down:
        _set_localization_status("stopping", "应用正在退出，跳过启动本地化引擎。")
        return False

    settings = get_effective_settings()
    auto_start = settings.get("localization_engine_auto_start", "true")
    if auto_start.lower() not in ("true", "1", "yes", "on"):
        _set_localization_status("disabled", "自动启动已关闭")
        return False

    if _localization_manager is None:
        _localization_manager = _build_localization_manager()

    return _localization_manager.ensure_running()


def ensure_qwen3_tts_engine() -> bool:
    """Start the Qwen3-TTS sidecar. Returns True if healthy."""
    global _qwen3_tts_manager
    if _sidecars_shutting_down:
        _set_qwen3_tts_status("stopping", "应用正在退出，跳过启动 Qwen3-TTS 服务。")
        return False
    if _qwen3_tts_manager is None:
        _qwen3_tts_manager = _build_qwen3_tts_manager()
    return _qwen3_tts_manager.ensure_running()


def shutdown_qwen3_tts_engine():
    """Shut down the Qwen3-TTS sidecar."""
    global _qwen3_tts_manager
    if _qwen3_tts_manager is not None:
        _qwen3_tts_manager.shutdown()
        _qwen3_tts_manager = None


def _shutdown_manager(manager: SidecarManager | None, name: str) -> None:
    if manager is None:
        return
    try:
        manager.shutdown()
    except Exception as exc:
        try:
            print(f"停止 {name} 服务失败: {exc}", file=sys.stderr)
        except Exception:
            pass


def shutdown_sidecars_on_exit() -> None:
    """Stop app-managed sidecar services when the desktop app exits.

    This intentionally also builds managers for known bundled service ports so
    services started from secondary windows (for example the Qwen3-TTS manager
    dialog) are not left orphaned after the main window is closed. Set
    V2S_STOP_SIDECARS_ON_EXIT=false to keep them running across UI restarts.
    """
    global _whisper_manager, _localization_manager, _qwen3_tts_manager
    global _sidecars_shutting_down, _sidecars_shutdown_done

    if _sidecars_shutdown_done:
        return
    _sidecars_shutdown_done = True

    if not _should_stop_sidecars_on_exit():
        return

    _sidecars_shutting_down = True

    qwen_manager = _qwen3_tts_manager or _build_qwen3_tts_manager()
    localization_manager = _localization_manager or _build_localization_manager()
    whisper_manager = _whisper_manager or _build_whisper_manager()

    # Stop high-memory/GPU services first, then supporting services.
    for manager, name in (
        (qwen_manager, "Qwen3-TTS"),
        (localization_manager, "Localization Engine"),
        (whisper_manager, "Whisper"),
    ):
        _shutdown_manager(manager, name)

    _qwen3_tts_manager = None
    _localization_manager = None
    _whisper_manager = None


def get_qwen3_tts_manager() -> Optional[SidecarManager]:
    return _qwen3_tts_manager


def main():
    QApplication.setAttribute(9015, True)  # AA_UseHighDpiPixmaps
    QApplication.setAttribute(9024, True)  # AA_EnableHighDpiScaling

    app = QApplication(sys.argv)
    app.setApplicationName("Video2Subtitles")
    app.setApplicationDisplayName("Video2Subtitles - 视频字幕生成")

    # Load font (Segoe UI for English, Microsoft YaHei for Chinese)
    font = QFont("Microsoft YaHei", 10)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)

    # Apply dark theme
    apply_theme(app)

    # Auto-start bundled/custom whisper server in background when present.
    _ensure_whisper_server()

    # Auto-start localization engine (failure does not block the app).
    try:
        _ensure_localization_engine()
    except Exception as exc:
        _set_localization_status("error", f"启动失败: {exc}")

    # Auto-start Qwen3-TTS sidecar when dub + qwen3-tts is configured.
    try:
        _settings = get_effective_settings()
        if (
            str(_settings.get("localization_mode", "") or "").strip() == "dub"
            and "qwen3-tts" in str(_settings.get("tts_provider", "") or "").lower()
        ):
            import threading as _threading
            _threading.Thread(
                target=lambda: ensure_qwen3_tts_engine(),
                daemon=True,
                name="qwen3-tts-autostart",
            ).start()
    except Exception:
        pass  # Do not block app startup

    app.aboutToQuit.connect(shutdown_sidecars_on_exit)

    window = MainWindow()
    window.show()

    exit_code = app.exec_()
    shutdown_sidecars_on_exit()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
