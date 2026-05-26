#!/usr/bin/env python3
"""Video2Subtitles - 视频字幕生成桌面客户端"""

import sys
import os
import subprocess
import time

from client_settings import apply_saved_settings_to_env

# Apply persisted client settings before importing modules that read env vars.
apply_saved_settings_to_env()

from whisper_config import WHISPER_SERVER, WHISPER_MODEL_DIR

# Add the bundled/custom whisper server to path so we can optionally use it directly.
# Default location: ./whisper-server. Override with WHISPER_SERVER_DIR when needed.
if WHISPER_SERVER.exists():
    sys.path.insert(0, str(WHISPER_SERVER))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont
from main_window import MainWindow, apply_theme
import settings_patch

settings_patch.install()


def _is_local_server_ready():
    """Return True when the local Whisper HTTP service is already reachable."""
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=1) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _find_server_script():
    """Find the bundled/custom Whisper server entry script.

    youtube-live-subtitles uses whisper-server/main.py. Older desktop bundles used
    whisper-server/server.py, so keep both for compatibility.
    """
    for name in ("main.py", "server.py"):
        candidate = WHISPER_SERVER / name
        if candidate.exists():
            return candidate
    return None


def _find_server_python():
    """Prefer the Whisper server venv, then project venv/current interpreter."""
    if os.name == "nt":
        candidates = [
            WHISPER_SERVER / "venv" / "Scripts" / "python.exe",
            WHISPER_SERVER / ".venv" / "Scripts" / "python.exe",
            os.path.dirname(sys.executable) and WHISPER_SERVER / "venv" / "Scripts" / "python.exe",
        ]
    else:
        candidates = [
            WHISPER_SERVER / "venv" / "bin" / "python",
            WHISPER_SERVER / ".venv" / "bin" / "python",
        ]

    for candidate in candidates:
        if candidate and PathLike_exists(candidate):
            return str(candidate)
    return sys.executable or "python"


def PathLike_exists(path):
    try:
        return path.exists()
    except Exception:
        return False


def _ensure_whisper_server():
    """Start the bundled/custom Whisper server in background when available.

    The desktop app should feel self-contained: online links can use the bundled
    service automatically, while local files can still fall back to faster-whisper
    when the service is absent.
    """
    if _is_local_server_ready():
        return

    if not WHISPER_SERVER.exists():
        return

    server_script = _find_server_script()
    if not server_script:
        return

    try:
        env = os.environ.copy()
        # Local sidecar is loopback-only by convention here. Disable the default
        # FastAPI API key ("your-secret-key") so desktop requests do not fail 401.
        env.setdefault("API_AUTH_KEY", "")
        env.setdefault("WHISPER_SERVER_DIR", str(WHISPER_SERVER))
        env.setdefault("WHISPER_MODEL_DIR", str(WHISPER_MODEL_DIR))

        popen_kwargs = {
            "cwd": str(WHISPER_SERVER),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "env": env,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        subprocess.Popen([_find_server_python(), str(server_script)], **popen_kwargs)
        for _ in range(20):
            time.sleep(0.5)
            if _is_local_server_ready():
                return
    except Exception:
        pass


def main():
    QApplication.setAttribute(9015, True)  # AA_UseHighDpiPixmaps
    QApplication.setAttribute(9024, True)  # AA_EnableHighDpiScaling

    app = QApplication(sys.argv)
    app.setApplicationName("Video2Subtitles")
    app.setApplicationDisplayName("Video2Subtitles - 视频字幕生成")

    # Load font
    font = QFont("Segoe UI", 10)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)

    # Apply dark theme
    apply_theme(app)

    # Auto-start bundled/custom whisper server in background when present.
    _ensure_whisper_server()

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
