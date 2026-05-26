"""Runtime diagnostics for Video2Subtitles.

The checks here are intentionally lightweight and safe to run from the GUI.
They do not download models or start long-running work; they only verify that
required modules, helper binaries, paths, and the local service are reachable.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from client_settings import DEFAULT_MODEL_DIR, get_effective_settings
from whisper_config import WHISPER_SERVER

APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = WHISPER_SERVER / "output" if WHISPER_SERVER.exists() else APP_DIR / "output"
SERVICE_LOG = APP_DIR / ".cache" / "whisper-service.log"


REQUIRED_MODULES = [
    ("PyQt5", "PyQt5"),
    ("requests", "requests"),
    ("faster-whisper", "faster_whisper"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("python-multipart", "multipart"),
]


def _ok(name: str, message: str, detail: str = "") -> Dict[str, Any]:
    return {"name": name, "status": "ok", "message": message, "detail": detail}


def _warn(name: str, message: str, detail: str = "") -> Dict[str, Any]:
    return {"name": name, "status": "warning", "message": message, "detail": detail}


def _error(name: str, message: str, detail: str = "") -> Dict[str, Any]:
    return {"name": name, "status": "error", "message": message, "detail": detail}


def _check_import(label: str, module_name: str) -> Dict[str, Any]:
    if importlib.util.find_spec(module_name) is not None:
        return _ok(label, "已安装")
    return _error(label, "未安装", f"请运行: {sys.executable} -m pip install -r requirements.txt")


def _check_command(name: str, hint: str = "") -> Dict[str, Any]:
    path = shutil.which(name)
    if path:
        return _ok(name, "可用", path)
    return _error(name, "未找到", hint or f"请安装 {name} 并加入 PATH")


def _check_writable_dir(name: str, path: Path) -> Dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".v2s_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return _ok(name, "可写", str(path))
    except Exception as exc:
        return _error(name, "不可写", f"{path}；{exc}")


def _check_port(host: str = "127.0.0.1", port: int = 8765) -> Dict[str, Any]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.6)
    try:
        code = sock.connect_ex((host, port))
    finally:
        sock.close()
    if code == 0:
        return _ok("8765 端口", "已有服务监听", f"{host}:{port}")
    return _warn("8765 端口", "当前未监听", "客户端启动时会自动尝试拉起内置服务")


def _check_service_health() -> Dict[str, Any]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=1.2) as resp:
            if 200 <= resp.status < 300:
                return _ok("本地 Whisper 服务", "已连接", "http://127.0.0.1:8765/health")
            return _warn("本地 Whisper 服务", f"HTTP {resp.status}", "请查看服务日志")
    except Exception as exc:
        detail = f"未连接；日志位置: {SERVICE_LOG}"
        if str(exc):
            detail += f"；{exc}"
        return _warn("本地 Whisper 服务", "未连接", detail)


def run_diagnostics() -> Dict[str, Any]:
    settings = get_effective_settings()
    model_dir = Path(settings.get("whisper_model_dir") or DEFAULT_MODEL_DIR)
    output_dir = OUTPUT_DIR

    checks: List[Dict[str, Any]] = []
    checks.append(
        _ok("Python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        if sys.version_info >= (3, 10)
        else _error("Python", "版本过低", "需要 Python 3.10+")
    )
    checks.extend(_check_import(label, module) for label, module in REQUIRED_MODULES)
    checks.append(_check_command("yt-dlp", "请运行: pip install -r requirements.txt"))
    checks.append(_check_command("ffmpeg", "请安装 ffmpeg 并加入 PATH，用于合并 MP4 和生成 ChatGPT 包"))
    checks.append(
        _ok("内置服务入口", "存在", str(WHISPER_SERVER / "main.py"))
        if (WHISPER_SERVER / "main.py").exists()
        else _error("内置服务入口", "未找到", str(WHISPER_SERVER / "main.py"))
    )
    checks.append(_check_writable_dir("模型目录", model_dir))
    checks.append(_check_writable_dir("输出目录", output_dir))
    checks.append(_check_port())
    checks.append(_check_service_health())

    has_error = any(item["status"] == "error" for item in checks)
    has_warning = any(item["status"] == "warning" for item in checks)
    if has_error:
        overall = "error"
    elif has_warning:
        overall = "warning"
    else:
        overall = "ok"
    return {"overall": overall, "checks": checks}


def format_diagnostics_report(result: Dict[str, Any]) -> str:
    icons = {"ok": "✅", "warning": "⚠️", "error": "❌"}
    lines = []
    for item in result.get("checks", []):
        icon = icons.get(item.get("status"), "•")
        line = f"{icon} {item.get('name', '')}: {item.get('message', '')}"
        if item.get("detail"):
            line += f"\n    {item['detail']}"
        lines.append(line)
    overall = result.get("overall", "unknown")
    if overall == "ok":
        header = "环境检查通过，可以开始处理视频。"
    elif overall == "warning":
        header = "环境基本可用，但有警告项，建议按提示检查。"
    else:
        header = "环境存在阻断项，请先处理红色错误。"
    return header + "\n\n" + "\n".join(lines)
