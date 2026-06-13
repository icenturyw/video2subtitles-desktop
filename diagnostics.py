"""Runtime diagnostics for Video2Subtitles.

The checks here are intentionally lightweight and safe to run from the GUI.
They do not download models or start long-running work; they only verify that
required modules, helper binaries, paths, GPU visibility, and the local service
are reachable.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from client_settings import DEFAULT_MODEL_DIR, get_effective_settings, get_runtime_settings
from gpu_config import device_status
from whisper_config import WHISPER_SERVER

APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = WHISPER_SERVER / "output" if WHISPER_SERVER.exists() else APP_DIR / "output"
SERVICE_LOG = APP_DIR / ".cache" / "whisper-service.log"
LOCALIZATION_LOG = APP_DIR / ".cache" / "localization-service.log"


REQUIRED_MODULES = [
    ("PyQt5", "PyQt5"),
    ("requests", "requests"),
    ("faster-whisper", "faster_whisper"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("python-multipart", "multipart"),
]

# Optional modules for advanced features
OPTIONAL_MODULES = [
    ("WhisperX", "whisperx", "高级字幕对齐（WhisperX）"),
    ("edge-tts", "edge_tts", "语音合成（Edge-TTS）"),
]

# Localization engine base dependencies
LOCALIZATION_MODULES = [
    ("pydantic", "pydantic"),
    ("httpx", "httpx"),
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


def _check_optional_import(label: str, module_name: str, description: str) -> Dict[str, Any]:
    if importlib.util.find_spec(module_name) is not None:
        return _ok(label, "已安装")
    return _warn(label, "未安装", f"{description}不可用。如需使用请安装 {module_name}")


def _check_command(name: str, hint: str = "") -> Dict[str, Any]:
    path = shutil.which(name)
    if path:
        return _ok(name, "可用", path)
    return _error(name, "未找到", hint or f"请安装 {name} 并加入 PATH")


def _check_gpu(settings: Dict[str, Any]) -> Dict[str, Any]:
    runtime = get_runtime_settings(settings)
    status = device_status()
    gpu_name = status.get("gpu_name") or "未检测到 NVIDIA GPU"
    requested = f"{settings.get('device', 'auto')}/{settings.get('compute_type', 'auto')}"
    resolved = f"{runtime.get('resolved_device')}/{runtime.get('resolved_compute_type')}"
    detail = f"设置: {requested}；实际将使用: {resolved}；GPU: {gpu_name}"
    if runtime.get("resolved_device") == "cuda":
        return _ok("GPU 推理", "将使用 CUDA", detail)
    return _warn("GPU 推理", "当前将使用 CPU", detail + "。如有 RTX 4070，请确认 NVIDIA 驱动、nvidia-smi 和 faster-whisper CUDA 依赖可用。")


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
        return _ok(f"{port} 端口", "已有服务监听", f"{host}:{port}")
    return _warn(f"{port} 端口", "当前未监听", "客户端启动时会自动尝试拉起内置服务")


def _check_service_health() -> Dict[str, Any]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=1.2) as resp:
            body = resp.read().decode("utf-8", "replace")
            detail = "http://127.0.0.1:8765/health"
            try:
                data = json.loads(body)
                if data.get("device") or data.get("compute_type"):
                    detail += f"；服务设备: {data.get('device')}/{data.get('compute_type')}"
            except Exception:
                pass
            if 200 <= resp.status < 300:
                return _ok("本地 Whisper 服务", "已连接", detail)
            return _warn("本地 Whisper 服务", f"HTTP {resp.status}", "请查看服务日志")
    except Exception as exc:
        detail = f"未连接；日志位置: {SERVICE_LOG}"
        if str(exc):
            detail += f"；{exc}"
        return _warn("本地 Whisper 服务", "未连接", detail)


def _check_localization_port() -> Dict[str, Any]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.6)
    try:
        code = sock.connect_ex(("127.0.0.1", 8766))
    finally:
        sock.close()
    if code == 0:
        return _ok("8766 端口", "本地化引擎已监听", "127.0.0.1:8766")
    return _warn("8766 端口", "本地化引擎未监听", "翻译/配音功能需要本地化引擎。启动时会自动尝试拉起服务。")


def _check_localization_health() -> Dict[str, Any]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8766/health", timeout=1.2) as resp:
            body = resp.read().decode("utf-8", "replace")
            detail = "http://127.0.0.1:8766/health"
            try:
                data = json.loads(body)
                caps = data.get("capabilities", {})
                cap_parts = []
                if caps.get("translation"):
                    cap_parts.append("翻译")
                if caps.get("rendering"):
                    cap_parts.append("渲染")
                if caps.get("whisperx"):
                    cap_parts.append("WhisperX")
                tts_list = caps.get("tts", [])
                if tts_list:
                    cap_parts.append("TTS:" + ",".join(tts_list))
                if cap_parts:
                    detail += f"；能力: {', '.join(cap_parts)}"
                ffmpeg_status = "可用" if data.get("ffmpeg") else "不可用"
                detail += f"；FFmpeg: {ffmpeg_status}"
            except Exception:
                pass
            if 200 <= resp.status < 300:
                return _ok("本地化引擎", "已连接", detail)
            return _warn("本地化引擎", f"HTTP {resp.status}", f"请查看日志: {LOCALIZATION_LOG}")
    except Exception:
        return _warn("本地化引擎", "未连接", f"翻译/配音功能暂不可用。日志: {LOCALIZATION_LOG}")


def _check_fonts() -> Dict[str, Any]:
    """Check for common subtitle fonts on the system."""
    if os.name == "nt":
        fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        preferred = [
            ("msyh.ttc", "微软雅黑"),
            ("simhei.ttf", "黑体"),
            ("simsun.ttc", "宋体"),
        ]
    else:
        fonts_dir = Path("/usr/share/fonts")
        preferred = [
            ("NotoSansCJK", "Noto Sans CJK"),
            ("WenQuanYi", "文泉驿"),
            ("DroidSans", "Droid Sans"),
        ]

    found_fonts = []
    missing_fonts = []

    if fonts_dir.exists():
        # Build a lowercase set of available font filenames
        available = set()
        try:
            for f in fonts_dir.rglob("*"):
                if f.is_file():
                    available.add(f.name.lower())
        except PermissionError:
            pass

        for filename, display_name in preferred:
            if filename.lower() in available:
                found_fonts.append(display_name)
            else:
                missing_fonts.append(display_name)
    else:
        return _warn("字幕字体", "未找到系统字体目录", str(fonts_dir))

    if found_fonts:
        detail = f"已找到: {', '.join(found_fonts)}"
        if missing_fonts:
            detail += f"；缺少: {', '.join(missing_fonts)}"
        return _ok("字幕字体", f"{len(found_fonts)} 种可用", detail)
    return _warn("字幕字体", "未找到推荐中文字体", f"建议安装: {', '.join(m[1] for m in preferred[:1])}")


def _check_disk_space(path: Path) -> Dict[str, Any]:
    """Check available disk space for the output directory."""
    try:
        usage = shutil.disk_usage(str(path))
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        detail = f"可用: {free_gb:.1f} GB / 总计: {total_gb:.1f} GB"
        if free_gb < 1.0:
            return _error("磁盘空间", f"不足 ({free_gb:.1f} GB)", detail)
        if free_gb < 5.0:
            return _warn("磁盘空间", f"偏低 ({free_gb:.1f} GB)", detail + "。视频处理需要较大空间")
        return _ok("磁盘空间", f"{free_gb:.1f} GB 可用", detail)
    except Exception as exc:
        return _warn("磁盘空间", "无法检测", str(exc))


def run_diagnostics() -> Dict[str, Any]:
    settings = get_effective_settings()
    model_dir = Path(settings.get("whisper_model_dir") or DEFAULT_MODEL_DIR)
    output_dir = OUTPUT_DIR

    checks: List[Dict[str, Any]] = []

    # Core environment
    checks.append(
        _ok("Python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        if sys.version_info >= (3, 10)
        else _error("Python", "版本过低", "需要 Python 3.10+")
    )

    # Required modules
    checks.extend(_check_import(label, module) for label, module in REQUIRED_MODULES)

    # Localization engine modules
    for label, module in LOCALIZATION_MODULES:
        checks.append(_check_import(f"{label}（本地化引擎）", module))

    # GPU
    checks.append(_check_gpu(settings))

    # External tools
    checks.append(_check_command("yt-dlp", "请运行: pip install -r requirements.txt"))
    checks.append(_check_command("ffmpeg", "请安装 ffmpeg 并加入 PATH，用于合并 MP4、烧录字幕和生成 ChatGPT 包"))

    # Whisper server entry point
    checks.append(
        _ok("内置服务入口", "存在", str(WHISPER_SERVER / "main.py"))
        if (WHISPER_SERVER / "main.py").exists()
        else _error("内置服务入口", "未找到", str(WHISPER_SERVER / "main.py"))
    )

    # Directories
    checks.append(_check_writable_dir("模型目录", model_dir))
    checks.append(_check_writable_dir("输出目录", output_dir))

    # Disk space
    checks.append(_check_disk_space(output_dir))

    # Whisper service (port 8765)
    checks.append(_check_port())
    checks.append(_check_service_health())

    # Localization Engine (port 8766)
    checks.append(_check_localization_port())
    checks.append(_check_localization_health())

    # Fonts
    checks.append(_check_fonts())

    # Optional modules
    for label, module, desc in OPTIONAL_MODULES:
        checks.append(_check_optional_import(label, module, desc))

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
