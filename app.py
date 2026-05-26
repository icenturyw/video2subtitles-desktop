#!/usr/bin/env python3
"""Video2Subtitles - 视频字幕生成桌面客户端"""

import json as _json
import sys
import os
import subprocess
import time
from pathlib import Path

from client_settings import apply_saved_settings_to_env

# Apply persisted client settings before importing modules that read env vars.
apply_saved_settings_to_env()

from whisper_config import WHISPER_SERVER, WHISPER_MODEL_DIR

APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / ".cache"
SERVICE_LOG_PATH = CACHE_DIR / "whisper-service.log"

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

settings_patch.install()
gpu_patch.install()
output_patch.install()


def _get_server_device():
    """Return (device, compute_type) reported by the running sidecar, or (None, None)."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=1) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
            return data.get("device"), data.get("compute_type")
    except Exception:
        return None, None


def _find_server_pid():
    """Return PID of the process listening on port 8765, or None."""
    try:
        kwargs = {"capture_output": True, "text": True, "timeout": 3}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(["netstat", "-ano"], **kwargs)
        for line in result.stdout.splitlines():
            if ":8765" in line and "LISTENING" in line:
                parts = line.strip().split()
                if parts:
                    return parts[-1]
    except Exception:
        pass
    return None


def _kill_server_process():
    """Kill the process listening on port 8765 if any."""
    pid = _find_server_pid()
    if pid:
        try:
            kwargs = {"capture_output": True, "timeout": 3}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                subprocess.run(["taskkill", "/F", "/PID", pid], **kwargs)
            else:
                import signal
                os.kill(int(pid), signal.SIGTERM)
            time.sleep(0.5)
        except Exception:
            pass


def restart_whisper_server():
    """Kill the running sidecar and start a fresh one with current env vars."""
    _set_service_status("restarting", "正在重启 Whisper 服务以应用新设置...")
    _kill_server_process()
    time.sleep(1)
    return _ensure_whisper_server()


def _set_service_status(status, detail=""):
    """Expose sidecar startup details to the already-running UI process."""
    os.environ["V2S_WHISPER_SERVICE_STATUS"] = status
    os.environ["V2S_WHISPER_SERVICE_DETAIL"] = detail
    os.environ["V2S_WHISPER_SERVICE_LOG"] = str(SERVICE_LOG_PATH)


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
            APP_DIR / ".venv" / "Scripts" / "python.exe",
            APP_DIR / "venv" / "Scripts" / "python.exe",
        ]
    else:
        candidates = [
            WHISPER_SERVER / "venv" / "bin" / "python",
            WHISPER_SERVER / ".venv" / "bin" / "python",
            APP_DIR / ".venv" / "bin" / "python",
            APP_DIR / "venv" / "bin" / "python",
        ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable or "python"


def _ensure_whisper_server():
    """Start the bundled/custom Whisper server in background when available.

    The desktop app should feel self-contained: online links can use the bundled
    service automatically, while local files can still fall back to faster-whisper
    when the service is absent.
    """
    _set_service_status("checking", "正在检查 127.0.0.1:8765 本地 Whisper 服务...")

    expected_device = os.environ.get("DEVICE", "cpu")

    if _is_local_server_ready():
        current_device, _ = _get_server_device()
        if current_device and current_device != expected_device:
            _set_service_status(
                "restarting",
                f"服务设备({current_device})与设置({expected_device})不匹配，正在重启...",
            )
            _kill_server_process()
            time.sleep(1)
        else:
            _set_service_status("already_running", "本地 Whisper 服务已经在运行。")
            return True

    if not WHISPER_SERVER.exists():
        _set_service_status("missing_dir", f"未找到 Whisper 服务目录: {WHISPER_SERVER}")
        return False

    server_script = _find_server_script()
    if not server_script:
        _set_service_status("missing_entry", f"未找到服务入口 main.py/server.py: {WHISPER_SERVER}")
        return False

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    python_exe = _find_server_python()

    try:
        env = os.environ.copy()
        # Local sidecar is loopback-only by convention here. Disable the default
        # FastAPI API key ("your-secret-key") so desktop requests do not fail 401.
        env.setdefault("API_AUTH_KEY", "")
        env.setdefault("WHISPER_SERVER_DIR", str(WHISPER_SERVER))
        env.setdefault("WHISPER_MODEL_DIR", str(WHISPER_MODEL_DIR))

        with SERVICE_LOG_PATH.open("a", encoding="utf-8", errors="replace") as log:
            log.write("\n" + "=" * 80 + "\n")
            log.write(time.strftime("%Y-%m-%d %H:%M:%S") + " 启动本地 Whisper 服务\n")
            log.write(f"python: {python_exe}\n")
            log.write(f"script: {server_script}\n")
            log.write(f"cwd: {WHISPER_SERVER}\n")
            log.flush()

            popen_kwargs = {
                "cwd": str(WHISPER_SERVER),
                "stdout": log,
                "stderr": subprocess.STDOUT,
                "env": env,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            subprocess.Popen([python_exe, str(server_script)], **popen_kwargs)

        _set_service_status("starting", f"正在启动本地 Whisper 服务，日志: {SERVICE_LOG_PATH}")
        for _ in range(20):
            time.sleep(0.5)
            if _is_local_server_ready():
                _set_service_status("started", f"本地 Whisper 服务已启动，日志: {SERVICE_LOG_PATH}")
                return True

        _set_service_status("timeout", f"已尝试启动但 10 秒内未就绪，请查看日志: {SERVICE_LOG_PATH}")
        return False
    except Exception as exc:
        _set_service_status("error", f"启动本地 Whisper 服务失败: {exc}；日志: {SERVICE_LOG_PATH}")
        return False


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
