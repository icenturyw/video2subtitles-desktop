"""Reusable sidecar process manager for Video2Subtitles services.

Manages the lifecycle of background HTTP services (e.g. Whisper server,
Localization Engine). Handles start, health-check, restart and shutdown.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def _is_port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    """Return True if a TCP listener is accepting on host:port."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def _find_pid_on_port(port: int) -> Optional[str]:
    """Return PID string of the process listening on *port*, or None."""
    try:
        kwargs: Dict = {"capture_output": True, "text": True, "timeout": 3}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(["netstat", "-ano"], **kwargs)
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                if parts:
                    return parts[-1]
    except Exception:
        pass
    return None


def _kill_pid(pid: str) -> None:
    """Kill a process by PID string."""
    try:
        kwargs: Dict = {"capture_output": True, "timeout": 3}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            subprocess.run(["taskkill", "/F", "/PID", pid], **kwargs)
        else:
            os.kill(int(pid), signal.SIGTERM)
        time.sleep(0.5)
    except Exception:
        pass


def _find_python(venv_dirs: List[Path], fallback: Optional[str] = None) -> str:
    """Find the best Python interpreter from candidate venv directories."""
    if os.name == "nt":
        rel_paths = [Path("venv") / "Scripts" / "python.exe",
                      Path(".venv") / "Scripts" / "python.exe"]
    else:
        rel_paths = [Path("venv") / "bin" / "python",
                      Path(".venv") / "bin" / "python"]

    for vdir in venv_dirs:
        for rel in rel_paths:
            candidate = vdir / rel
            if candidate.exists():
                return str(candidate)
    return fallback or sys.executable or "python"


class SidecarManager:
    """Manages a single sidecar HTTP service process.

    Parameters
    ----------
    name : str
        Human-readable service name (e.g. "Whisper", "Localization Engine").
    port : int
        TCP port the service listens on.
    service_dir : Path
        Working directory to run the service process in.
    script_name : str
        Entry-point script name (default "main.py").
    log_filename : str
        Name of the log file under *log_dir*.
    log_dir : Path
        Directory for log files.
    extra_env : dict, optional
        Additional environment variables for the subprocess.
    extra_venv_dirs : list[Path], optional
        Additional directories to search for a venv Python.
    startup_timeout : float
        Seconds to wait for the service to become healthy.
    health_url : str, optional
        Override health URL (default http://127.0.0.1:{port}/health).
    status_callback : callable, optional
        Callback ``(status: str, detail: str) -> None`` for UI status updates.
    """

    def __init__(
        self,
        name: str,
        port: int,
        service_dir: Path,
        *,
        script_name: str = "main.py",
        log_filename: str = "service.log",
        log_dir: Path | None = None,
        extra_env: Dict[str, str] | None = None,
        extra_venv_dirs: List[Path] | None = None,
        startup_timeout: float = 10.0,
        health_url: str | None = None,
        status_callback: Callable[[str, str], None] | None = None,
    ):
        self.name = name
        self.port = port
        self.service_dir = Path(service_dir)
        self.script_name = script_name
        self.log_dir = Path(log_dir) if log_dir else self.service_dir
        self.log_path = self.log_dir / log_filename
        self.extra_env = extra_env or {}
        self.extra_venv_dirs = extra_venv_dirs or []
        self.startup_timeout = startup_timeout
        self.health_url = health_url or f"http://127.0.0.1:{port}/health"
        self.status_callback = status_callback or (lambda s, d: None)
        self._process: Optional[subprocess.Popen] = None

    # -- public API ---------------------------------------------------------

    def ensure_running(self) -> bool:
        """Start the sidecar if it is not already running.

        Returns True if the service is healthy after this call.
        """
        self._status("checking", f"正在检查 127.0.0.1:{self.port} {self.name} 服务...")

        if self._is_healthy():
            self._status("already_running", f"{self.name} 服务已在运行。")
            return True

        # Kill stale process on our port
        self._kill_stale()

        if not self.service_dir.exists():
            self._status("missing_dir", f"未找到 {self.name} 目录: {self.service_dir}")
            return False

        script = self.service_dir / self.script_name
        if not script.exists():
            self._status("missing_entry", f"未找到入口 {self.script_name}: {self.service_dir}")
            return False

        return self._start(script)

    def restart(self) -> bool:
        """Kill the running sidecar and start a fresh one."""
        self._status("restarting", f"正在重启 {self.name} 服务...")
        self._kill_stale()
        time.sleep(1)
        return self.ensure_running()

    def shutdown(self) -> None:
        """Terminate the sidecar process if we own it."""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._kill_stale()

    def is_healthy(self) -> bool:
        return self._is_healthy()

    def get_server_info(self) -> Optional[Dict]:
        """GET the health endpoint and return the JSON body, or None."""
        import urllib.request
        try:
            with urllib.request.urlopen(self.health_url, timeout=2) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    # -- internals ----------------------------------------------------------

    def _is_healthy(self) -> bool:
        import urllib.request
        try:
            with urllib.request.urlopen(self.health_url, timeout=1.2) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    def _kill_stale(self) -> None:
        pid = _find_pid_on_port(self.port)
        if pid:
            _kill_pid(pid)

    def _start(self, script: Path) -> bool:
        venv_dirs = [self.service_dir] + self.extra_venv_dirs
        python_exe = _find_python(venv_dirs)

        self.log_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update(self.extra_env)

        try:
            with self.log_path.open("a", encoding="utf-8", errors="replace") as log:
                log.write("\n" + "=" * 80 + "\n")
                log.write(time.strftime("%Y-%m-%d %H:%M:%S") + f" 启动 {self.name} 服务\n")
                log.write(f"python: {python_exe}\n")
                log.write(f"script: {script}\n")
                log.write(f"cwd: {self.service_dir}\n")
                log.flush()

                popen_kwargs: Dict = {
                    "cwd": str(self.service_dir),
                    "stdout": log,
                    "stderr": subprocess.STDOUT,
                    "env": env,
                }
                if os.name == "nt":
                    popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

                self._process = subprocess.Popen(
                    [python_exe, str(script)], **popen_kwargs
                )

            self._status(
                "starting",
                f"正在启动 {self.name} 服务，日志: {self.log_path}",
            )

            deadline = time.time() + self.startup_timeout
            while time.time() < deadline:
                time.sleep(0.5)
                if self._is_healthy():
                    self._status(
                        "started",
                        f"{self.name} 服务已启动，日志: {self.log_path}",
                    )
                    return True

            self._status(
                "timeout",
                f"已尝试启动但 {self.startup_timeout:.0f} 秒内未就绪，请查看日志: {self.log_path}",
            )
            return False

        except Exception as exc:
            self._status(
                "error",
                f"启动 {self.name} 服务失败: {exc}；日志: {self.log_path}",
            )
            return False

    def _status(self, status: str, detail: str = "") -> None:
        logger.info("[%s] %s: %s", self.name, status, detail)
        self.status_callback(status, detail)
