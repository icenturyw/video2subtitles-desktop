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
    if _qwen3_tts_manager is None:
        _qwen3_tts_manager = _build_qwen3_tts_manager()
    return _qwen3_tts_manager.ensure_running()


def shutdown_qwen3_tts_engine():
    """Shut down the Qwen3-TTS sidecar."""
    global _qwen3_tts_manager
    if _qwen3_tts_manager is not None:
        _qwen3_tts_manager.shutdown()


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

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
