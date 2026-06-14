from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from PyQt5.QtCore import QMetaObject, QTimer, Qt, Q_ARG
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QMessageBox, QProgressBar, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)

_THEME = {
    "bg_dark": "#1a1b2e",
    "bg_medium": "#232540",
    "bg_light": "#2d2f54",
    "bg_card": "#363870",
    "accent": "#7c6ff0",
    "text_primary": "#e8eaff",
    "text_secondary": "#9a9cc0",
    "text_muted": "#6b6d92",
    "border": "#3d3f6b",
}

_STYLE_SHEET = f"""
QDialog {{ background-color: {_THEME["bg_dark"]}; }}
QGroupBox {{
    border: 1px solid {_THEME["border"]};
    border-radius: 10px;
    margin-top: 14px;
    padding: 16px 12px 10px 12px;
    font-weight: 600;
    color: {_THEME["text_primary"]};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 8px;
    color: {_THEME["accent"]};
}}
QLabel {{ color: {_THEME["text_secondary"]}; font-size: 12px; }}
QPushButton {{
    background-color: {_THEME["accent"]};
    border: none;
    border-radius: 6px;
    padding: 8px 18px;
    color: #fff;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton:hover {{ background-color: #8d7ff5; }}
QPushButton:disabled {{ background-color: {_THEME["text_muted"]}; }}
QProgressBar {{
    border: 1px solid {_THEME["border"]};
    border-radius: 6px;
    text-align: center;
    color: {_THEME["text_primary"]};
    background-color: {_THEME["bg_light"]};
}}
QProgressBar::chunk {{ background-color: {_THEME["accent"]}; border-radius: 6px; }}
QComboBox {{
    background-color: {_THEME["bg_light"]};
    border: 1px solid {_THEME["border"]};
    border-radius: 6px;
    padding: 6px 10px;
    color: {_THEME["text_primary"]};
    font-size: 12px;
}}
QTextEdit {{
    background-color: {_THEME["bg_medium"]};
    border: 1px solid {_THEME["border"]};
    border-radius: 6px;
    color: {_THEME["text_primary"]};
    font-family: Consolas, monospace;
    font-size: 11px;
}}
"""

QWEN3_TTS_PORT = 8767
QWEN3_TTS_HOST = "127.0.0.1"
QWEN3_TTS_DIR = Path(__file__).resolve().parent.parent / "qwen3-tts-engine"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"


def _http_get(path: str, timeout: float = 5.0):
    import urllib.request
    url = f"http://{QWEN3_TTS_HOST}:{QWEN3_TTS_PORT}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post(path: str, body: dict, timeout: float = 30.0):
    import urllib.request
    url = f"http://{QWEN3_TTS_HOST}:{QWEN3_TTS_PORT}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"},
                                  method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post_no_body(path: str, timeout: float = 30.0):
    import urllib.request
    url = f"http://{QWEN3_TTS_HOST}:{QWEN3_TTS_PORT}{path}"
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
        if not payload:
            return {}
        return json.loads(payload.decode("utf-8"))


class QwenTTSInstallDialog(QDialog):
    _model_load_done = pyqtSignal(bool, str)
    _service_start_done = pyqtSignal(bool)
    _test_done = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Qwen3-TTS 管理")
        self.setMinimumSize(520, 520)
        self.setStyleSheet(_STYLE_SHEET)

        self._service_running = False
        self._model_loaded = False
        self._status_timer = QTimer()
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(3000)

        self._model_load_done.connect(self._on_model_load_done)
        self._service_start_done.connect(self._on_service_start_done)
        self._test_done.connect(self._on_test_done)
        self._setup_ui()
        self._refresh_status()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        title = QLabel("Qwen3-TTS 本地语音引擎")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {_THEME['text_primary']};"
        )
        layout.addWidget(title)

        # Status
        status_group = QGroupBox("服务状态")
        status_layout = QVBoxLayout(status_group)
        self._status_label = QLabel("检查中...")
        self._status_label.setStyleSheet(
            f"color: {_THEME['text_muted']}; font-size: 13px;"
        )
        status_layout.addWidget(self._status_label)

        self._model_label = QLabel("模型: -")
        self._model_label.setStyleSheet(
            f"color: {_THEME['text_secondary']}; font-size: 12px;"
        )
        status_layout.addWidget(self._model_label)

        self._device_label = QLabel("设备: -")
        self._device_label.setStyleSheet(
            f"color: {_THEME['text_secondary']}; font-size: 12px;"
        )
        status_layout.addWidget(self._device_label)

        status_btn_layout = QHBoxLayout()
        self._start_btn = QPushButton("启动服务")
        self._start_btn.clicked.connect(self._start_service)
        self._stop_btn = QPushButton("停止服务")
        self._stop_btn.clicked.connect(self._stop_service)
        self._stop_btn.setEnabled(False)
        status_btn_layout.addWidget(self._start_btn)
        status_btn_layout.addWidget(self._stop_btn)
        status_layout.addLayout(status_btn_layout)
        layout.addWidget(status_group)

        # Model management
        model_group = QGroupBox("模型管理")
        model_form = QFormLayout(model_group)
        model_form.setSpacing(8)

        self._model_selector = QComboBox()
        self._model_selector.addItems([
            "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice （标准 - 推荐）",
            "Qwen/Qwen3-TTS-12Hz-0.6B-Base （标准 + 声音克隆）",
            "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice （高级）",
            "Qwen/Qwen3-TTS-12Hz-1.7B-Base （高级 + 声音克隆）",
            "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign （高级 + 音色设计）",
        ])
        model_form.addRow("模型:", self._model_selector)

        model_btn_layout = QHBoxLayout()
        self._load_btn = QPushButton("加载模型")
        self._load_btn.clicked.connect(self._load_model)
        self._load_btn.setEnabled(False)
        self._unload_btn = QPushButton("卸载模型")
        self._unload_btn.clicked.connect(self._unload_model)
        self._unload_btn.setEnabled(False)
        model_btn_layout.addWidget(self._load_btn)
        model_btn_layout.addWidget(self._unload_btn)
        model_form.addRow("", model_btn_layout)
        layout.addWidget(model_group)

        # Test
        test_group = QGroupBox("测试")
        test_layout = QVBoxLayout(test_group)
        self._test_btn = QPushButton("生成测试语音")
        self._test_btn.clicked.connect(self._test_synthesis)
        self._test_btn.setEnabled(False)
        test_layout.addWidget(self._test_btn)
        layout.addWidget(test_group)

        # Log
        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout(log_group)
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumHeight(120)
        log_layout.addWidget(self._log_view)
        layout.addWidget(log_group)

        # Close
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _log(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        text = f"[{timestamp}] {msg}"
        if threading.current_thread() is threading.main_thread():
            self._log_view.append(text)
        else:
            QMetaObject.invokeMethod(
                self._log_view, "append", Qt.QueuedConnection,
                Q_ARG(str, text)
            )

    def _refresh_status(self):
        try:
            info = _http_get("/health", timeout=2)
            self._service_running = True
            self._model_loaded = info.get("loaded_model") is not None
            self._status_label.setText("● 运行中")
            self._status_label.setStyleSheet(
                f"color: #4ade80; font-size: 13px;"
            )
            self._model_label.setText(
                f"模型: {info.get('loaded_model', '未加载')}"
            )
            self._device_label.setText(
                f"设备: {info.get('device', '?')} | "
                f"精度: {info.get('dtype', '?')} | "
                f"FlashAttention: {info.get('flash_attention', False)}"
            )
            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(True)
            self._load_btn.setEnabled(True)
            self._unload_btn.setEnabled(self._model_loaded)
            self._test_btn.setEnabled(self._model_loaded)
        except Exception:
            self._service_running = False
            self._model_loaded = False
            self._status_label.setText("○ 未运行")
            self._status_label.setStyleSheet(
                f"color: {_THEME['text_muted']}; font-size: 13px;"
            )
            self._model_label.setText("模型: -")
            self._device_label.setText("设备: -")
            self._start_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._load_btn.setEnabled(False)
            self._unload_btn.setEnabled(False)
            self._test_btn.setEnabled(False)

    def _start_service(self):
        self._start_btn.setEnabled(False)
        self._start_btn.setText("启动中...")

        def _run():
            try:
                from services.sidecar_manager import SidecarManager
                mgr = SidecarManager(
                    name="Qwen3-TTS",
                    port=QWEN3_TTS_PORT,
                    service_dir=QWEN3_TTS_DIR,
                    script_name="main.py",
                    log_filename="qwen3-tts-service.log",
                    log_dir=CACHE_DIR,
                    extra_venv_dirs=[Path(__file__).resolve().parent.parent],
                    startup_timeout=30.0,
                )
                ok = mgr.ensure_running()
                if not ok:
                    self._service_start_done.emit(False)
                else:
                    self._service_start_done.emit(True)
            except Exception as e:
                self._log(f"启动错误: {e}")
                self._service_start_done.emit(False)

        threading.Thread(target=_run, daemon=True).start()

    def _on_service_start_done(self, ok: bool):
        self._start_btn.setText("启动服务")
        self._start_btn.setEnabled(True)
        if ok:
            QTimer.singleShot(500, self._refresh_status)
        else:
            self._log("服务启动失败，请查看日志")

    def _stop_service(self):
        try:
            _http_post_no_body("/models/unload", timeout=3)
        except Exception:
            pass
        try:
            _http_post_no_body("/shutdown", timeout=2)
        except Exception:
            pass
        from services.sidecar_manager import _find_pid_on_port, _kill_pid
        pid = _find_pid_on_port(QWEN3_TTS_PORT)
        if pid:
            _kill_pid(pid)
        self._log("服务已停止")
        self._refresh_status()

    def _load_model(self):
        model_text = self._model_selector.currentText()
        model_id = model_text.split("（")[0].strip()
        self._load_btn.setEnabled(False)
        self._load_btn.setText("加载中...")

        def _run():
            try:
                _http_post("/models/load", {"model_id": model_id}, timeout=1800)
                self._model_load_done.emit(True, f"模型加载成功: {model_id}")
            except Exception as e:
                self._model_load_done.emit(False, f"模型加载失败: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def _on_model_load_done(self, ok: bool, msg: str):
        self._log(msg)
        self._load_btn.setText("加载模型")
        self._load_btn.setEnabled(True)
        QTimer.singleShot(500, self._refresh_status)

    def _unload_model(self):
        try:
            _http_post_no_body("/models/unload", timeout=30)
            self._log("模型已卸载")
        except Exception as e:
            self._log(f"卸载失败: {e}")
        self._refresh_status()

    def _test_synthesis(self):
        self._test_btn.setEnabled(False)
        self._test_btn.setText("生成中...")

        def _run():
            try:
                import urllib.request
                base_url = f"http://{QWEN3_TTS_HOST}:{QWEN3_TTS_PORT}"

                # Detect loaded model type to choose correct endpoint
                health_resp = urllib.request.urlopen(f"{base_url}/health", timeout=5)
                model_info = json.loads(health_resp.read().decode("utf-8"))
                loaded_model = model_info.get("loaded_model", "")
                caps = model_info.get("capabilities", {})

                test_text = "你好，这是一个测试语音。Qwen3-TTS 本地配音引擎运行正常。"

                if caps.get("custom_voice"):
                    endpoint = "/synthesize/custom-voice"
                    body = {
                        "text": test_text,
                        "speaker": "Vivian",
                        "language": "zh",
                    }
                elif caps.get("voice_clone"):
                    endpoint = "/synthesize/voice-clone"
                    body = {
                        "text": test_text,
                        "language": "zh",
                    }
                elif caps.get("voice_design"):
                    endpoint = "/synthesize/voice-design"
                    body = {
                        "text": test_text,
                        "instruct": "A natural voice speaking Chinese",
                        "language": "zh",
                    }
                else:
                    self._log("当前模型不支持任何合成模式")
                    return

                req = urllib.request.Request(
                    f"{base_url}{endpoint}",
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    out_dir = Path.home() / "Desktop"
                    out_path = out_dir / "qwen3_tts_test.wav"
                    out_path.write_bytes(resp.read())
                    duration = resp.headers.get("X-Duration", "?")
                    self._log(f"测试音频已保存: {out_path} (时长: {duration}s)")
            except Exception as e:
                self._log(f"测试失败: {e}")
            finally:
                self._test_done.emit()

        threading.Thread(target=_run, daemon=True).start()

    def _on_test_done(self):
        self._test_btn.setText("生成测试语音")
        self._test_btn.setEnabled(True)
