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
    QCheckBox,
)

import main_window as mw
from client_settings import (
    DEFAULT_MODEL_DIR,
    SUPPORTED_DOWNLOAD_MODES,
    SUPPORTED_DOWNLOAD_QUALITIES,
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
OriginalSubtitleSetupUi = mw.SubtitleViewer._setup_ui
OriginalSubtitleShow = mw.SubtitleViewer.show_subtitles
OriginalSubtitleClear = mw.SubtitleViewer.clear

DOWNLOAD_MODE_LABELS = {
    "video": "保存 MP4 视频（推荐）",
    "transcribe_only": "仅用于转写，不保留视频",
    "audio": "仅音频转写（节省空间）",
}
DOWNLOAD_QUALITY_LABELS = {
    "best": "最高可用质量",
    "720p": "最高 720p",
    "480p": "最高 480p",
}

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


def _service_status_text():
    status = os.environ.get("V2S_WHISPER_SERVICE_STATUS", "").strip()
    detail = os.environ.get("V2S_WHISPER_SERVICE_DETAIL", "").strip()
    log_path = os.environ.get("V2S_WHISPER_SERVICE_LOG", "").strip()
    return status, detail, log_path


def _open_service_log():
    _, _, log_path = _service_status_text()
    if log_path and Path(log_path).exists():
        _open_folder(Path(log_path).parent)
        return True
    return False


def _combo_select_data(combo, value):
    for i in range(combo.count()):
        if combo.itemData(i) == value or combo.itemText(i) == value:
            combo.setCurrentIndex(i)
            return


class SettingsDialog(OriginalSettingsDialog):
    """Existing settings dialog plus model, download and diagnostics controls."""

    def __init__(self, parent=None, current_output_dir=None):
        super().__init__(parent, current_output_dir)
        self.setFixedSize(700, 840)
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

        self.download_mode_combo = QComboBox()
        for mode in SUPPORTED_DOWNLOAD_MODES:
            self.download_mode_combo.addItem(DOWNLOAD_MODE_LABELS.get(mode, mode), mode)
        _combo_select_data(self.download_mode_combo, self.model_settings.get("download_mode", "video"))
        form.addRow("在线视频下载模式:", self.download_mode_combo)

        self.download_quality_combo = QComboBox()
        for quality in SUPPORTED_DOWNLOAD_QUALITIES:
            self.download_quality_combo.addItem(DOWNLOAD_QUALITY_LABELS.get(quality, quality), quality)
        _combo_select_data(self.download_quality_combo, self.model_settings.get("download_quality", "best"))
        form.addRow("下载质量:", self.download_quality_combo)

        self.keep_video_check = QCheckBox("转写后保留下载的视频文件")
        self.keep_video_check.setChecked(self.model_settings.get("keep_downloaded_video", "true") == "true")
        form.addRow("", self.keep_video_check)

        install_layout = QHBoxLayout()
        install_layout.setSpacing(8)
        self.install_model_btn = QPushButton("安装/检查模型")
        self.install_model_btn.setFixedHeight(36)
        self.install_model_btn.clicked.connect(self._install_or_check_model)
        install_layout.addWidget(self.install_model_btn)

        self.save_model_btn = QPushButton("保存设置")
        self.save_model_btn.setObjectName("btn_secondary")
        self.save_model_btn.setFixedHeight(36)
        self.save_model_btn.clicked.connect(self._save_model_settings_only)
        install_layout.addWidget(self.save_model_btn)

        diagnostics_btn = QPushButton("一键检查环境")
        diagnostics_btn.setObjectName("btn_secondary")
        diagnostics_btn.setFixedHeight(36)
        diagnostics_btn.clicked.connect(self._run_diagnostics)
        install_layout.addWidget(diagnostics_btn)
        form.addRow("", install_layout)

        self.model_install_status = QLabel("本地视频可直接使用本地模型；在线链接会优先使用项目内置本地服务。")
        self.model_install_status.setWordWrap(True)
        self.model_install_status.setStyleSheet(f"font-size: 11px; color: {THEME['text_muted']};")
        form.addRow("", self.model_install_status)

        hint = QLabel("默认保存 MP4，便于输出目录保留原视频并生成 ChatGPT 分析包；选择音频-only 会节省空间，但完整视频包可能无法生成。")
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

        download_mode = self.download_mode_combo.currentData() or "video"
        keep_video = self.keep_video_check.isChecked() or download_mode == "video"
        return {
            "whisper_model_dir": str(model_dir_path),
            "whisper_model_path": model_path_str,
            "model_size": self.model_size_combo.currentText().strip(),
            "download_mode": download_mode,
            "download_quality": self.download_quality_combo.currentData() or "best",
            "keep_downloaded_video": "true" if keep_video else "false",
        }

    def _save_model_settings_only(self):
        settings = self._collect_model_settings(create_model_dir=True)
        if not settings:
            return None
        saved = save_settings(settings)
        apply_settings_to_env(saved, overwrite=True)
        self.model_settings = saved
        self._set_model_status("设置已保存。", "success")
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
            self._set_model_status("✅ 模型已就绪。现在可以添加视频并点击「开始处理」。", "success")
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

    def _run_diagnostics(self):
        try:
            from diagnostics import format_diagnostics_report, run_diagnostics

            result = run_diagnostics()
            report = format_diagnostics_report(result)
            title = "环境检查"
            if result.get("overall") == "error":
                QMessageBox.warning(self, title, report)
            else:
                QMessageBox.information(self, title, report)
        except Exception as exc:
            QMessageBox.warning(self, "环境检查失败", f"无法完成环境检查:\n{exc}")

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


def _subtitle_setup_ui_with_package(self):
    OriginalSubtitleSetupUi(self)
    self.package_btn = QPushButton("📦 生成 ChatGPT 包")
    self.package_btn.setObjectName("btn_secondary")
    self.package_btn.setFixedHeight(34)
    self.package_btn.setVisible(False)
    self.package_btn.clicked.connect(lambda: _generate_package_from_viewer(self))
    try:
        self.layout().insertWidget(1, self.package_btn)
    except Exception:
        pass


def _subtitle_show_with_package(self, key, subtitles, is_url=False):
    OriginalSubtitleShow(self, key, subtitles, is_url)
    if hasattr(self, "package_btn"):
        self.package_btn.setVisible(bool(subtitles))
        self.package_btn.setToolTip("为当前已完成任务生成轻量/完整 ChatGPT 上传包")


def _subtitle_clear_with_package(self):
    OriginalSubtitleClear(self)
    if hasattr(self, "package_btn"):
        self.package_btn.setVisible(False)


def _generate_package_from_viewer(viewer):
    file_path = getattr(viewer, "_file_path", None)
    if not file_path:
        return
    parent = viewer.window() if viewer.window() else None
    if parent and hasattr(parent, "_generate_chatgpt_package"):
        parent._generate_chatgpt_package(file_path)


def _patched_check_server(self):
    server_connected = False
    try:
        client = mw.WhisperApiClient()
        server_connected = client.health_check()
    except Exception:
        server_connected = False

    local_ready = _runtime_has_faster_whisper()
    service_status, service_detail, service_log = _service_status_text()

    if server_connected and local_ready:
        self.server_status.setText("● 本地+在线就绪")
        _set_label_style(self.server_status, THEME["success"])
        self.server_status.setToolTip(service_detail or "本地视频可直接转录；在线视频链接会通过项目内置本地服务下载处理。")
        if hasattr(self, "status_label"):
            self.status_label.setText("本地模型与内置在线链接服务均已就绪。")
    elif server_connected:
        self.server_status.setText("● 在线服务已连接")
        _set_label_style(self.server_status, THEME["success"])
        self.server_status.setToolTip(service_detail or "内置/远程 Whisper 服务已连接；本地模型未检测到时仍可走服务模式。")
        if hasattr(self, "status_label"):
            self.status_label.setText("在线链接服务已连接；本地模型未检测到或未安装。")
    elif local_ready:
        self.server_status.setText("● 本地模式就绪")
        _set_label_style(self.server_status, THEME["success"])
        self.server_status.setToolTip(service_detail or "本地视频可直接转录；在线视频链接会自动尝试启动内置服务。")
        if hasattr(self, "status_label"):
            if service_status in {"missing_dir", "missing_entry", "timeout", "error"}:
                self.status_label.setText(f"本地模式就绪；内置在线服务未就绪：{service_detail}")
            else:
                self.status_label.setText("本地模式就绪：可直接添加本地视频；在线视频链接会自动尝试启动内置服务。")
    else:
        self.server_status.setText("⚠ 需要安装模型/服务")
        _set_label_style(self.server_status, THEME["warning"])
        self.server_status.setToolTip(service_detail or "打开设置，点击「安装/检查模型」或「一键检查环境」。")
        if hasattr(self, "status_label"):
            self.status_label.setText(service_detail or "首次使用：请打开设置，选择模型后点击「安装/检查模型」。")


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
            "当前内置服务不可用，也未检测到可用的 faster-whisper。\n\n"
            "请点击右上角「⚙」→「Whisper 模型」→「安装/检查模型」或「一键检查环境」。\n"
            "如果提示缺少依赖，请先运行: pip install -r requirements.txt",
        )
        if hasattr(self, "status_label"):
            self.status_label.setText("请先在设置中安装/检查模型，或运行一键环境检查。")
        return

    return OriginalStartProcessing(self, *args, **kwargs)


def install():
    """Install the extended settings dialog and friendlier startup flow."""
    mw.SettingsDialog = SettingsDialog
    mw.MainWindow._check_server = _patched_check_server
    mw.MainWindow._show_settings = _patched_show_settings
    mw.MainWindow._start_processing = _patched_start_processing
    mw.SubtitleViewer._setup_ui = _subtitle_setup_ui_with_package
    mw.SubtitleViewer.show_subtitles = _subtitle_show_with_package
    mw.SubtitleViewer.clear = _subtitle_clear_with_package
