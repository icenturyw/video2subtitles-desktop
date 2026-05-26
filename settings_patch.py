"""Patch the existing UI to make Whisper setup easier."""
import os
import subprocess
import sys
from pathlib import Path

from PyQt5.QtCore import QProcess, QProcessEnvironment
from PyQt5.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QComboBox,
)

import main_window as mw
from client_settings import (
    DEFAULT_MODEL_DIR,
    SUPPORTED_MODEL_SIZES,
    apply_settings_to_env,
    get_effective_settings,
    save_settings,
)
from whisper_config import find_python_executable


THEME = mw.THEME
OriginalSettingsDialog = mw.SettingsDialog
OriginalCheckServer = mw.MainWindow._check_server
OriginalShowSettings = mw.MainWindow._show_settings
OriginalStartProcessing = mw.MainWindow._start_processing

INSTALL_MODEL_CODE = r'''
import os
import sys
from pathlib import Path

model_dir = os.environ.get("WHISPER_MODEL_DIR", "").strip()
model_path = os.environ.get("WHISPER_MODEL_PATH", "").strip()
model_size = os.environ.get("MODEL_SIZE", "base").strip() or "base"
device = os.environ.get("DEVICE", "cpu")
compute_type = os.environ.get("COMPUTE_TYPE", "int8")

print("准备检查 faster-whisper 运行环境...", flush=True)
try:
    from faster_whisper import WhisperModel
except ImportError:
    print("ERROR: 未安装 faster-whisper。请先运行: pip install -r requirements.txt", flush=True)
    raise SystemExit(2)

model_id = model_path or model_size
print(f"正在安装/检查模型: {model_id}", flush=True)

kwargs = {"device": device, "compute_type": compute_type}
if model_dir:
    Path(model_dir).mkdir(parents=True, exist_ok=True)
    kwargs["download_root"] = model_dir
    print(f"模型缓存目录: {model_dir}", flush=True)

WhisperModel(model_id, **kwargs)
print("OK: 模型已就绪，可以开始转录。", flush=True)
'''


def _styled_line_edit(text=""):
    edit = QLineEdit(text)
    edit.setStyleSheet(f"""
        QLineEdit {{
            background-color: {THEME["bg_light"]};
            border: 1px solid {THEME["border"]};
            border-radius: 8px;
            padding: 10px 14px;
            font-size: 13px;
        }}
        QLineEdit:focus {{
            border-color: {THEME["accent"]};
        }}
    """)
    return edit


def _runtime_has_faster_whisper():
    try:
        run_kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "timeout": 3,
        }
        if os.name == "nt":
            run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            [find_python_executable(), "-c", "import faster_whisper"],
            **run_kwargs,
        )
        return result.returncode == 0
    except Exception:
        return False


def _open_folder(path):
    try:
        if os.name == "nt":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def _set_label_style(label, color, bold=True):
    weight = "600" if bold else "400"
    label.setStyleSheet(f"font-size: 12px; color: {color}; font-weight: {weight}; padding: 4px 12px;")


class SettingsDialog(OriginalSettingsDialog):
    """Existing settings dialog plus model installation controls."""

    def __init__(self, parent=None, current_output_dir=None):
        super().__init__(parent, current_output_dir)
        self.setFixedSize(660, 760)
        self.model_settings = get_effective_settings()
        self._model_install_process = None
        self._append_model_group()

    def _append_model_group(self):
        group = QGroupBox("Whisper 模型")
        form = QFormLayout(group)
        form.setSpacing(12)

        self.model_size_combo = QComboBox()
        self.model_size_combo.addItems(SUPPORTED_MODEL_SIZES)
        model_size = self.model_settings.get("model_size", "base")
        if model_size in SUPPORTED_MODEL_SIZES:
            self.model_size_combo.setCurrentText(model_size)
        form.addRow("模型大小:", self.model_size_combo)

        model_dir_layout = QHBoxLayout()
        model_dir_layout.setSpacing(8)
        self.model_dir_input = _styled_line_edit(
            self.model_settings.get("whisper_model_dir") or str(DEFAULT_MODEL_DIR)
        )
        model_dir_layout.addWidget(self.model_dir_input, 1)

        model_dir_btn = QPushButton("浏览...")
        model_dir_btn.setObjectName("btn_secondary")
        model_dir_btn.setFixedHeight(34)
        model_dir_btn.clicked.connect(self._browse_model_dir)
        model_dir_layout.addWidget(model_dir_btn)

        open_dir_btn = QPushButton("打开")
        open_dir_btn.setObjectName("btn_secondary")
        open_dir_btn.setFixedHeight(34)
        open_dir_btn.clicked.connect(self._open_model_dir)
        model_dir_layout.addWidget(open_dir_btn)
        form.addRow("模型缓存目录:", model_dir_layout)

        model_path_layout = QHBoxLayout()
        model_path_layout.setSpacing(8)
        self.model_path_input = _styled_line_edit(
            self.model_settings.get("whisper_model_path") or ""
        )
        self.model_path_input.setPlaceholderText("可选：留空则按模型大小从缓存目录加载/下载")
        model_path_layout.addWidget(self.model_path_input, 1)

        model_path_btn = QPushButton("浏览...")
        model_path_btn.setObjectName("btn_secondary")
        model_path_btn.setFixedHeight(34)
        model_path_btn.clicked.connect(self._browse_model_path)
        model_path_layout.addWidget(model_path_btn)

        clear_path_btn = QPushButton("清空")
        clear_path_btn.setObjectName("btn_secondary")
        clear_path_btn.setFixedHeight(34)
        clear_path_btn.clicked.connect(lambda: self.model_path_input.setText(""))
        model_path_layout.addWidget(clear_path_btn)
        form.addRow("具体模型目录:", model_path_layout)

        install_layout = QHBoxLayout()
        install_layout.setSpacing(8)
        self.install_model_btn = QPushButton("安装/检查模型")
        self.install_model_btn.setFixedHeight(36)
        self.install_model_btn.clicked.connect(self._install_or_check_model)
        install_layout.addWidget(self.install_model_btn)

        self.save_model_btn = QPushButton("保存模型设置")
        self.save_model_btn.setObjectName("btn_secondary")
        self.save_model_btn.setFixedHeight(36)
        self.save_model_btn.clicked.connect(self._save_model_settings_only)
        install_layout.addWidget(self.save_model_btn)
        form.addRow("", install_layout)

        self.model_install_status = QLabel("本地视频不需要服务器；首次使用建议先点击「安装/检查模型」。")
        self.model_install_status.setWordWrap(True)
        self.model_install_status.setStyleSheet(f"font-size: 11px; color: {THEME['text_muted']};")
        form.addRow("", self.model_install_status)

        hint = QLabel("在线链接下载仍需要 Whisper Server；本地视频可直接走 faster-whisper。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size: 11px; color: {THEME['text_muted']};")
        form.addRow("", hint)

        layout = self.layout()
        insert_at = max(0, layout.count() - 2)  # before stretch + buttons
        layout.insertWidget(insert_at, group)

    def _browse_model_dir(self):
        start_dir = self.model_dir_input.text().strip() or str(DEFAULT_MODEL_DIR)
        folder = QFileDialog.getExistingDirectory(self, "选择模型缓存目录", start_dir)
        if folder:
            self.model_dir_input.setText(folder)

    def _browse_model_path(self):
        start_dir = (
            self.model_path_input.text().strip()
            or self.model_dir_input.text().strip()
            or str(Path.home())
        )
        folder = QFileDialog.getExistingDirectory(self, "选择具体模型目录", start_dir)
        if folder:
            self.model_path_input.setText(folder)

    def _collect_model_settings(self, create_model_dir=True):
        model_dir = self.model_dir_input.text().strip() or str(DEFAULT_MODEL_DIR)
        try:
            model_dir_path = Path(model_dir).expanduser().resolve()
            if create_model_dir:
                model_dir_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            QMessageBox.warning(self, "模型目录无效", f"无法创建模型缓存目录:\n{e}")
            return None

        model_path = self.model_path_input.text().strip()
        model_path_str = ""
        if model_path:
            model_path_path = Path(model_path).expanduser().resolve()
            if not model_path_path.is_dir():
                QMessageBox.warning(self, "模型路径无效", "具体模型目录不存在，请重新选择或留空。")
                return None
            model_path_str = str(model_path_path)

        return {
            "whisper_model_dir": str(model_dir_path),
            "whisper_model_path": model_path_str,
            "model_size": self.model_size_combo.currentText().strip(),
        }

    def _save_model_settings_only(self):
        settings = self._collect_model_settings(create_model_dir=True)
        if not settings:
            return None
        saved = save_settings(settings)
        apply_settings_to_env(saved, overwrite=True)
        self.model_settings = saved
        self._set_model_status("模型设置已保存。", "success")
        return saved

    def _install_or_check_model(self):
        settings = self._save_model_settings_only()
        if not settings:
            return

        self.install_model_btn.setEnabled(False)
        self.save_model_btn.setEnabled(False)
        self._set_model_status("正在安装/检查模型，请保持网络连接；窗口可以先放着。", "info")

        env = QProcessEnvironment.systemEnvironment()
        env.insert("WHISPER_MODEL_DIR", settings["whisper_model_dir"])
        env.insert("MODEL_SIZE", settings["model_size"])
        if settings.get("whisper_model_path"):
            env.insert("WHISPER_MODEL_PATH", settings["whisper_model_path"])
        else:
            env.remove("WHISPER_MODEL_PATH")
        env.insert("DEVICE", os.environ.get("DEVICE", "cpu"))
        env.insert("COMPUTE_TYPE", os.environ.get("COMPUTE_TYPE", "int8"))

        self._model_install_process = QProcess(self)
        self._model_install_process.setProcessEnvironment(env)
        self._model_install_process.setProcessChannelMode(QProcess.SeparateChannels)
        self._model_install_process.readyReadStandardOutput.connect(self._read_model_install_stdout)
        self._model_install_process.readyReadStandardError.connect(self._read_model_install_stderr)
        self._model_install_process.finished.connect(self._on_model_install_finished)
        self._model_install_process.errorOccurred.connect(self._on_model_install_error)
        self._model_install_process.start(find_python_executable(), ["-c", INSTALL_MODEL_CODE])

    def _read_model_install_stdout(self):
        if not self._model_install_process:
            return
        output = bytes(self._model_install_process.readAllStandardOutput()).decode("utf-8", "replace").strip()
        if output:
            self._set_model_status(output.splitlines()[-1][-220:], "info")

    def _read_model_install_stderr(self):
        if not self._model_install_process:
            return
        output = bytes(self._model_install_process.readAllStandardError()).decode("utf-8", "replace").strip()
        if output:
            self._set_model_status(output.splitlines()[-1][-220:], "warning")

    def _on_model_install_finished(self, exit_code, exit_status):
        self.install_model_btn.setEnabled(True)
        self.save_model_btn.setEnabled(True)
        if exit_code == 0:
            self._set_model_status("✅ 模型已就绪。现在可以添加本地视频并点击「开始处理」。", "success")
            parent = self.parent()
            if parent and hasattr(parent, "_check_server"):
                parent._check_server()
        else:
            self._set_model_status(
                "❌ 模型安装/检查失败。请确认已运行 pip install -r requirements.txt，且网络可访问模型下载源。",
                "error",
            )
        self._model_install_process = None

    def _on_model_install_error(self, error):
        self.install_model_btn.setEnabled(True)
        self.save_model_btn.setEnabled(True)
        self._set_model_status(f"❌ 无法启动模型安装进程: {error}", "error")
        self._model_install_process = None

    def _open_model_dir(self):
        settings = self._collect_model_settings(create_model_dir=True)
        if settings:
            _open_folder(Path(settings["whisper_model_dir"]))

    def _set_model_status(self, text, level="info"):
        colors = {
            "success": THEME["success"],
            "info": THEME["info"],
            "warning": THEME["warning"],
            "error": THEME["error"],
        }
        self.model_install_status.setText(text)
        self.model_install_status.setStyleSheet(
            f"font-size: 11px; color: {colors.get(level, THEME['text_muted'])}; font-weight: 600;"
        )

    def accept(self):
        settings = self._collect_model_settings(create_model_dir=True)
        if not settings:
            return
        saved = save_settings(settings)
        apply_settings_to_env(saved, overwrite=True)
        self.model_settings = saved
        super().accept()


def _patched_check_server(self):
    server_connected = False
    try:
        client = mw.WhisperApiClient()
        server_connected = client.health_check()
    except Exception:
        server_connected = False

    local_ready = _runtime_has_faster_whisper()

    if server_connected and local_ready:
        self.server_status.setText("● 本地+在线就绪")
        _set_label_style(self.server_status, THEME["success"])
        self.server_status.setToolTip("本地视频可直接转录；在线视频链接也可通过 Whisper Server 下载处理。")
    elif server_connected:
        self.server_status.setText("● 在线服务已连接")
        _set_label_style(self.server_status, THEME["success"])
        self.server_status.setToolTip("Whisper Server 已连接；如需纯本地转录，请安装 faster-whisper。")
    elif local_ready:
        self.server_status.setText("● 本地模式就绪")
        _set_label_style(self.server_status, THEME["success"])
        self.server_status.setToolTip("本地视频可直接转录；在线视频链接需要 Whisper Server。")
        if hasattr(self, "status_label"):
            self.status_label.setText("本地模式就绪：可直接添加本地视频；在线视频链接需要启动 Whisper Server。")
    else:
        self.server_status.setText("⚠ 需要安装模型")
        _set_label_style(self.server_status, THEME["warning"])
        self.server_status.setToolTip("打开设置，点击「安装/检查模型」；如缺少依赖，请先运行 pip install -r requirements.txt。")
        if hasattr(self, "status_label"):
            self.status_label.setText("首次使用：请打开设置，选择模型后点击「安装/检查模型」。")


def _patched_show_settings(self):
    OriginalShowSettings(self)
    try:
        self._check_server()
    except Exception:
        pass


def _patched_start_processing(self, *args, **kwargs):
    server_connected = False
    try:
        server_connected = mw.WhisperApiClient().health_check()
    except Exception:
        server_connected = False

    if not server_connected and not _runtime_has_faster_whisper():
        QMessageBox.warning(
            self,
            "需要先安装本地模型",
            "当前没有连接 Whisper Server，也未检测到可用的 faster-whisper。\n\n"
            "请点击右上角「⚙」→「Whisper 模型」→「安装/检查模型」。\n"
            "如果提示缺少依赖，请先运行: pip install -r requirements.txt",
        )
        if hasattr(self, "status_label"):
            self.status_label.setText("请先在设置中安装/检查模型。")
        return

    return OriginalStartProcessing(self, *args, **kwargs)


def install():
    """Install the extended settings dialog and friendlier startup flow."""
    mw.SettingsDialog = SettingsDialog
    mw.MainWindow._check_server = _patched_check_server
    mw.MainWindow._show_settings = _patched_show_settings
    mw.MainWindow._start_processing = _patched_start_processing
