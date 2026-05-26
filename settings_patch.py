"""Patch the existing settings dialog with Whisper model path controls."""
from pathlib import Path

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


THEME = mw.THEME
OriginalSettingsDialog = mw.SettingsDialog


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


class SettingsDialog(OriginalSettingsDialog):
    """Existing settings dialog plus client-side model path settings."""

    def __init__(self, parent=None, current_output_dir=None):
        super().__init__(parent, current_output_dir)
        self.setFixedSize(620, 680)
        self.model_settings = get_effective_settings()
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
        form.addRow("模型缓存目录:", model_dir_layout)

        model_path_layout = QHBoxLayout()
        model_path_layout.setSpacing(8)
        self.model_path_input = _styled_line_edit(
            self.model_settings.get("whisper_model_path") or ""
        )
        self.model_path_input.setPlaceholderText("留空则按模型大小从缓存目录加载/下载")
        model_path_layout.addWidget(self.model_path_input, 1)
        model_path_btn = QPushButton("浏览...")
        model_path_btn.setObjectName("btn_secondary")
        model_path_btn.setFixedHeight(34)
        model_path_btn.clicked.connect(self._browse_model_path)
        model_path_layout.addWidget(model_path_btn)
        form.addRow("具体模型目录:", model_path_layout)

        hint = QLabel("保存后写入 .cache/settings.json，并自动应用到转录进程。")
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

    def accept(self):
        model_dir = self.model_dir_input.text().strip()
        if model_dir:
            try:
                Path(model_dir).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                QMessageBox.warning(self, "模型目录无效", f"无法创建模型缓存目录:\n{e}")
                return

        model_path = self.model_path_input.text().strip()
        if model_path and not Path(model_path).is_dir():
            QMessageBox.warning(self, "模型路径无效", "具体模型目录不存在，请重新选择或留空。")
            return

        settings = save_settings({
            "whisper_model_dir": model_dir,
            "whisper_model_path": model_path,
            "model_size": self.model_size_combo.currentText().strip(),
        })
        apply_settings_to_env(settings, overwrite=True)
        self.model_settings = settings
        super().accept()


def install():
    """Install this extended dialog into the main window module."""
    mw.SettingsDialog = SettingsDialog
