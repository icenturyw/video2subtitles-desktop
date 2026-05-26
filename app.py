#!/usr/bin/env python3
"""Video2Subtitles - 视频字幕生成桌面客户端"""

import sys
import os
import subprocess
import time
from pathlib import Path

# Add whisper server to path so we can optionally use it directly
WHISPER_SERVER = Path(os.environ.get(
    "WHISPER_SERVER_DIR",
    str(Path(__file__).parent.parent / "youtube-live-subtitles" / "whisper-server"),
))
if WHISPER_SERVER.exists():
    sys.path.insert(0, str(WHISPER_SERVER))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont
from main_window import MainWindow, apply_theme


def _ensure_whisper_server():
    """Kill old server, start fresh server in background."""
    import urllib.request

    # Kill any existing process on port 8765 to force fresh code
    try:
        out = subprocess.check_output(
            'netstat -ano | findstr :8765',
            shell=True, text=True, timeout=5,
        )
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(":8765") and "LISTENING" in line:
                pid = parts[-1]
                subprocess.run(['taskkill', '/F', '/PID', pid],
                               capture_output=True, timeout=5)
                time.sleep(0.5)
    except Exception:
        pass

    if not WHISPER_SERVER.exists():
        return

    venv_python = WHISPER_SERVER / "venv" / "Scripts" / "python.exe"
    server_script = WHISPER_SERVER / "server.py"
    if not venv_python.exists() or not server_script.exists():
        return

    try:
        subprocess.Popen(
            [str(venv_python), str(server_script)],
            cwd=str(WHISPER_SERVER),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for _ in range(20):
            time.sleep(0.5)
            try:
                urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=1)
                return
            except Exception:
                continue
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

    # Auto-start whisper server in background
    _ensure_whisper_server()

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
