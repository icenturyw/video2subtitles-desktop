import os
import json
import time
import threading
import webbrowser
from pathlib import Path

from PyQt5.QtCore import (
    Qt, QObject, QThread, QProcess, pyqtSignal, QTimer, QSize, QRectF,
    QPropertyAnimation, QEasingCurve, pyqtProperty,
)
from PyQt5.QtGui import (
    QFont, QColor, QPalette, QIcon, QPixmap, QPainter, QPen,
    QLinearGradient, QBrush, QCursor, QTextCursor, QFontDatabase
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QListWidget, QListWidgetItem, QTextEdit,
    QFileDialog, QProgressBar, QMessageBox, QSplitter, QFrame,
    QMenu, QAction, QStatusBar, QToolBar, QComboBox, QSpinBox,
    QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QCheckBox,
    QGroupBox, QTabWidget, QScrollArea, QAbstractItemView,
    QSystemTrayIcon, QStyle, QStackedWidget, QSizePolicy,
    QGraphicsDropShadowEffect, QToolButton, QButtonGroup,
    QStyleOptionViewItem,
)

from api_client import WhisperApiClient
from local_whisper import LocalWhisperTranscriber, WHISPER_SERVER
from history import HistoryManager
from subtitle_utils import format_subtitle_time, sanitize_filename
from localization_client import LocalizationClient
from client_settings import get_effective_settings
from ui.localization_dialog import LocalizationDialog, localization_runtime_config


THEME = {
    "bg_dark": "#1a1b2e",
    "bg_medium": "#232540",
    "bg_light": "#2d2f54",
    "bg_card": "#363870",
    "bg_hover": "#404280",
    "accent": "#7c6ff0",
    "accent_hover": "#9489f5",
    "accent_dark": "#5a4ed8",
    "success": "#4ade80",
    "warning": "#fbbf24",
    "error": "#f87171",
    "info": "#60a5fa",
    "text_primary": "#e8eaff",
    "text_secondary": "#9a9cc0",
    "text_muted": "#6b6d92",
    "border": "#3d3f6b",
    "shadow": "rgba(0,0,0,0.3)",
}


def apply_theme(app):
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(THEME["bg_dark"]))
    palette.setColor(QPalette.WindowText, QColor(THEME["text_primary"]))
    palette.setColor(QPalette.Base, QColor(THEME["bg_medium"]))
    palette.setColor(QPalette.AlternateBase, QColor(THEME["bg_light"]))
    palette.setColor(QPalette.ToolTipBase, QColor(THEME["bg_light"]))
    palette.setColor(QPalette.ToolTipText, QColor(THEME["text_primary"]))
    palette.setColor(QPalette.Text, QColor(THEME["text_primary"]))
    palette.setColor(QPalette.Button, QColor(THEME["bg_light"]))
    palette.setColor(QPalette.ButtonText, QColor(THEME["text_primary"]))
    palette.setColor(QPalette.BrightText, QColor(THEME["text_primary"]))
    palette.setColor(QPalette.Link, QColor(THEME["accent"]))
    palette.setColor(QPalette.Highlight, QColor(THEME["accent"]))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    app.setStyleSheet(f"""
        QMainWindow {{
            background-color: {THEME["bg_dark"]};
        }}
        QWidget {{
            font-family: 'Segoe UI', 'Microsoft YaHei', 'PingFang SC', sans-serif;
            color: {THEME["text_primary"]};
        }}
        QPushButton {{
            background-color: {THEME["accent"]};
            color: white;
            border: none;
            border-radius: 8px;
            padding: 10px 24px;
            font-size: 13px;
            font-weight: 600;
            min-height: 20px;
        }}
        QPushButton:hover {{
            background-color: {THEME["accent_hover"]};
        }}
        QPushButton:pressed {{
            background-color: {THEME["accent_dark"]};
        }}
        QPushButton:disabled {{
            background-color: {THEME["bg_light"]};
            color: {THEME["text_muted"]};
        }}
        QPushButton#btn_secondary {{
            background-color: {THEME["bg_light"]};
            color: {THEME["text_primary"]};
            border: 1px solid {THEME["border"]};
        }}
        QPushButton#btn_secondary:hover {{
            background-color: {THEME["bg_hover"]};
            border-color: {THEME["accent"]};
        }}
        QPushButton#btn_danger {{
            background-color: #dc3545;
        }}
        QPushButton#btn_danger:hover {{
            background-color: #e85d6a;
        }}
        QPushButton#btn_success {{
            background-color: {THEME["success"]};
            color: {THEME["bg_dark"]};
        }}
        QPushButton#btn_icon {{
            background-color: transparent;
            border: 1px solid {THEME["border"]};
            border-radius: 8px;
            padding: 8px;
            min-width: 36px;
            min-height: 36px;
        }}
        QPushButton#btn_icon:hover {{
            background-color: {THEME["bg_hover"]};
            border-color: {THEME["accent"]};
        }}
        QListWidget {{
            background-color: {THEME["bg_medium"]};
            border: 1px solid {THEME["border"]};
            border-radius: 12px;
            padding: 4px;
            outline: none;
        }}
        QListWidget::item {{
            border-radius: 8px;
            padding: 0px;
            margin: 2px 0px;
        }}
        QListWidget::item:selected {{
            background-color: {THEME["accent"]}33;
        }}
        QListWidget::item:hover {{
            background-color: {THEME["bg_hover"]};
        }}
        QTextEdit {{
            background-color: {THEME["bg_medium"]};
            border: 1px solid {THEME["border"]};
            border-radius: 12px;
            padding: 16px;
            font-size: 14px;
            line-height: 1.6;
            selection-background-color: {THEME["accent"]}66;
        }}
        QProgressBar {{
            background-color: {THEME["bg_light"]};
            border: none;
            border-radius: 6px;
            height: 12px;
            text-align: center;
            font-size: 10px;
            font-weight: 600;
            color: white;
        }}
        QProgressBar::chunk {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {THEME["accent"]}, stop:1 {THEME["info"]});
            border-radius: 6px;
        }}
        QComboBox {{
            background-color: {THEME["bg_light"]};
            border: 1px solid {THEME["border"]};
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 13px;
            min-height: 20px;
        }}
        QComboBox:hover {{
            border-color: {THEME["accent"]};
        }}
        QComboBox::drop-down {{
            border: none;
            padding-right: 8px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {THEME["bg_medium"]};
            border: 1px solid {THEME["border"]};
            border-radius: 8px;
            selection-background-color: {THEME["accent"]};
            color: {THEME["text_primary"]};
        }}
        QScrollBar:vertical {{
            background-color: {THEME["bg_dark"]};
            width: 8px;
            border-radius: 4px;
        }}
        QScrollBar::handle:vertical {{
            background-color: {THEME["bg_hover"]};
            border-radius: 4px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background-color: {THEME["accent"]};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollBar:horizontal {{
            background-color: {THEME["bg_dark"]};
            height: 8px;
            border-radius: 4px;
        }}
        QScrollBar::handle:horizontal {{
            background-color: {THEME["bg_hover"]};
            border-radius: 4px;
            min-width: 30px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background-color: {THEME["accent"]};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}
        QSplitter::handle {{
            background-color: {THEME["border"]};
            width: 1px;
        }}
        QStatusBar {{
            background-color: {THEME["bg_medium"]};
            border-top: 1px solid {THEME["border"]};
            color: {THEME["text_secondary"]};
            font-size: 12px;
            padding: 4px 12px;
        }}
        QMenu {{
            background-color: {THEME["bg_medium"]};
            border: 1px solid {THEME["border"]};
            border-radius: 10px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 8px 32px 8px 16px;
            border-radius: 6px;
        }}
        QMenu::item:selected {{
            background-color: {THEME["accent"]};
        }}
        QMenu::separator {{
            height: 1px;
            background-color: {THEME["border"]};
            margin: 4px 8px;
        }}
        QGroupBox {{
            border: 1px solid {THEME["border"]};
            border-radius: 12px;
            margin-top: 16px;
            padding: 20px 16px 12px 16px;
            font-weight: 600;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 4px 12px;
            color: {THEME["accent"]};
        }}
        QLineEdit {{
            background-color: {THEME["bg_light"]};
            border: 1px solid {THEME["border"]};
            border-radius: 8px;
            padding: 8px 14px;
            font-size: 13px;
        }}
        QLineEdit:focus {{
            border-color: {THEME["accent"]};
        }}
        QCheckBox {{
            spacing: 8px;
            font-size: 13px;
        }}
        QCheckBox::indicator {{
            width: 18px;
            height: 18px;
            border-radius: 4px;
            border: 2px solid {THEME["border"]};
            background-color: {THEME["bg_light"]};
        }}
        QCheckBox::indicator:checked {{
            background-color: {THEME["accent"]};
            border-color: {THEME["accent"]};
        }}
        QToolTip {{
            background-color: {THEME["bg_medium"]};
            border: 1px solid {THEME["border"]};
            border-radius: 6px;
            padding: 6px 12px;
            color: {THEME["text_primary"]};
            font-size: 12px;
        }}
        QTabWidget::pane {{
            border: 1px solid {THEME["border"]};
            border-radius: 12px;
            background-color: {THEME["bg_medium"]};
        }}
        QTabBar::tab {{
            background-color: transparent;
            color: {THEME["text_muted"]};
            border: none;
            padding: 10px 20px;
            font-size: 13px;
            font-weight: 500;
        }}
        QTabBar::tab:selected {{
            color: {THEME["accent"]};
            border-bottom: 2px solid {THEME["accent"]};
        }}
        QTabBar::tab:hover {{
            color: {THEME["text_primary"]};
        }}
    """)


def create_shadow(widget):
    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(20)
    shadow.setColor(QColor(0, 0, 0, 80))
    shadow.setOffset(0, 4)
    widget.setGraphicsEffect(shadow)
    return widget


class CardFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            CardFrame {{
                background-color: {THEME["bg_card"]};
                border: 1px solid {THEME["border"]};
                border-radius: 16px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)


class VideoItemWidget(QWidget):
    def __init__(self, file_path, parent=None, is_url=False, title=None):
        super().__init__(parent)
        self.file_path = str(file_path) if not is_url else file_path
        self.is_url = is_url
        self.title = title
        self.status = "pending"
        self.progress = 0
        self.message = ""
        self.subtitles = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(36, 36)
        self.icon_label.setAlignment(Qt.AlignCenter)
        font = QFont("Segoe UI", 16)
        self.icon_label.setFont(font)
        self.update_icon()
        layout.addWidget(self.icon_label)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        if self.is_url:
            display = self.title if self.title else str(self.file_path)
            if len(display) > 55:
                display = display[:52] + "..."
            self.name_label = QLabel(display)
            self.name_label.setStyleSheet(f"font-size: 13px; font-weight: 600; font-family: 'Segoe UI', 'Microsoft YaHei', 'PingFang SC', sans-serif; color: {THEME['info']};")
            self.name_label.setToolTip(str(self.file_path))
        else:
            name = Path(self.file_path).name
            if len(name) > 50:
                name = name[:47] + "..."
            self.name_label = QLabel(name)
            self.name_label.setStyleSheet(f"font-size: 13px; font-weight: 600; font-family: 'Segoe UI', 'Microsoft YaHei', 'PingFang SC', sans-serif; color: {THEME['text_primary']};")
            self.name_label.setToolTip(str(self.file_path))
        info_layout.addWidget(self.name_label)

        status_layout = QHBoxLayout()
        status_layout.setSpacing(8)

        if self.is_url:
            self.size_label = QLabel("🌐 在线视频")
        else:
            sz = Path(self.file_path).stat().st_size if Path(self.file_path).exists() else 0
            self.size_label = QLabel(self._format_size(sz) if sz else "N/A")
        self.size_label.setStyleSheet(f"font-size: 11px; color: {THEME['text_muted']};")
        status_layout.addWidget(self.size_label)

        self.dot_label = QLabel("·")
        self.dot_label.setStyleSheet(f"font-size: 11px; color: {THEME['text_muted']};")
        status_layout.addWidget(self.dot_label)

        self.status_label = QLabel("等待处理")
        status_layout.addWidget(self.status_label)

        status_layout.addStretch()
        info_layout.addLayout(status_layout)

        layout.addLayout(info_layout, 1)

        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(2)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(140)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        progress_layout.addWidget(self.progress_bar, 0, Qt.AlignRight)

        self.pct_label = QLabel("0%")
        self.pct_label.setStyleSheet(f"font-size: 11px; color: {THEME['text_muted']};")
        self.pct_label.setAlignment(Qt.AlignRight)
        progress_layout.addWidget(self.pct_label)

        layout.addLayout(progress_layout)

        self.update_status("pending")

    def update_icon(self):
        if self.is_url:
            status_icons = {
                "pending": "🔗",
                "queued": "⏳",
                "downloading": "⬇️",
                "processing": "🔄",
                "completed": "✅",
                "error": "❌",
                "cancelled": "⏹",
                "cached": "📦",
            }
        else:
            status_icons = {
                "pending": "🎬",
                "queued": "⏳",
                "downloading": "⬇️",
                "processing": "🔄",
                "completed": "✅",
                "error": "❌",
                "cancelled": "⏹",
                "cached": "📦",
            }
        self.icon_label.setText(status_icons.get(self.status, "🔗" if self.is_url else "🎬"))

    def update_status(self, status, progress=0, message=""):
        self.status = status
        self.progress = progress
        if message:
            self.message = message

        status_colors = {
            "pending": THEME["text_muted"],
            "queued": THEME["info"],
            "downloading": THEME["warning"],
            "processing": THEME["accent"],
            "completed": THEME["success"],
            "error": THEME["error"],
            "cancelled": THEME["text_muted"],
            "cached": THEME["warning"],
        }
        status_texts = {
            "pending": "等待处理",
            "queued": "排队中",
            "downloading": "下载中",
            "processing": "处理中",
            "completed": "已完成",
            "error": "失败",
            "cancelled": "已取消",
            "cached": "从缓存加载",
        }

        color = status_colors.get(status, THEME["text_muted"])
        text = message or status_texts.get(status, status)

        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"font-size: 11px; color: {color}; font-weight: 500;")

        self.progress_bar.setValue(int(progress))
        self.pct_label.setText(f"{int(progress)}%")

        self.update_icon()

        if status == "completed":
            self.progress_bar.setStyleSheet(f"""
                QProgressBar::chunk {{
                    background-color: {THEME["success"]};
                    border-radius: 4px;
                }}
            """)
        elif status == "error":
            self.progress_bar.setStyleSheet(f"""
                QProgressBar::chunk {{
                    background-color: {THEME["error"]};
                    border-radius: 4px;
                }}
            """)
        else:
            self.progress_bar.setStyleSheet("")

    @staticmethod
    def _format_size(size):
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


class SubtitleViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("字幕预览")
        title.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {THEME['text_primary']};")
        header.addWidget(title)
        header.addStretch()

        self.open_dir_btn = QPushButton("📂 打开输出目录")
        self.open_dir_btn.setObjectName("btn_secondary")
        self.open_dir_btn.setFixedHeight(34)
        self.open_dir_btn.setVisible(False)
        self.open_dir_btn.clicked.connect(self._open_output_dir)
        header.addWidget(self.open_dir_btn)

        self.export_btn = QPushButton("导出 SRT")
        self.export_btn.setObjectName("btn_secondary")
        self.export_btn.setFixedHeight(34)
        self.export_btn.setVisible(False)
        self.export_btn.clicked.connect(self._export_srt)
        header.addWidget(self.export_btn)

        self.export_vtt_btn = QPushButton("导出 VTT")
        self.export_vtt_btn.setObjectName("btn_secondary")
        self.export_vtt_btn.setFixedHeight(34)
        self.export_vtt_btn.setVisible(False)
        self.export_vtt_btn.clicked.connect(self._export_vtt)
        header.addWidget(self.export_vtt_btn)

        self.export_txt_btn = QPushButton("导出 TXT")
        self.export_txt_btn.setObjectName("btn_secondary")
        self.export_txt_btn.setFixedHeight(34)
        self.export_txt_btn.setVisible(False)
        self.export_txt_btn.clicked.connect(self._export_txt)
        header.addWidget(self.export_txt_btn)

        layout.addLayout(header)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlaceholderText("选择已完成的任务查看字幕内容...")
        self.text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: {THEME["bg_medium"]};
                border: 1px solid {THEME["border"]};
                border-radius: 12px;
                padding: 16px;
                font-size: 14px;
                line-height: 1.7;
            }}
        """)
        layout.addWidget(self.text_edit)

        self._subtitles = None
        self._file_path = None

    def show_subtitles(self, key, subtitles, is_url=False):
        try:
            self._file_path = key
            self._subtitles = subtitles
            self.open_dir_btn.setVisible(True)
            self.export_btn.setVisible(True)
            self.export_vtt_btn.setVisible(True)
            self.export_txt_btn.setVisible(True)

            if is_url:
                display_name = str(key)
                if len(display_name) > 60:
                    display_name = display_name[:57] + "..."
            else:
                display_name = Path(str(key)).name

            html = []
            html.append(f'<div style="color: {THEME["text_secondary"]}; font-size: 12px; margin-bottom: 12px;">')
            html.append(f'📄 {display_name} | 共 {len(subtitles)} 条字幕')
            html.append('</div>')
            html.append('<table style="width: 100%; border-collapse: collapse;">')

            for i, sub in enumerate(subtitles, 1):
                start = sub.get("start", 0)
                end = sub.get("end", 0)
                text = sub.get("text", "")
                translation = sub.get("translation", "")

                start_str = self._format_time(start)
                end_str = self._format_time(end)

                bg = "#232540" if i % 2 == 1 else "#2d2f54"

                html.append(
                    f'<tr style="background-color: {bg};">'
                    f'<td style="padding: 8px 12px; color: {THEME["text_muted"]}; width: 50px; text-align: center; font-size: 12px;">{i}</td>'
                    f'<td style="padding: 8px 6px; color: {THEME["info"]}; width: 160px; font-size: 12px; font-family: monospace;">{start_str} → {end_str}</td>'
                    f'<td style="padding: 8px 12px;">{text}</td>'
                )
                if translation:
                    html.append(
                        f'<td style="padding: 8px 12px; color: {THEME["text_secondary"]}; font-style: italic;">{translation}</td>'
                    )
                html.append('</tr>')

            html.append('</table>')
            self.text_edit.setHtml("".join(html))
        except Exception as e:
            self.text_edit.setPlainText(f"显示字幕时出错: {str(e)[:100]}")

    def clear(self):
        self._subtitles = None
        self._file_path = None
        self.open_dir_btn.setVisible(False)
        self.export_btn.setVisible(False)
        self.export_vtt_btn.setVisible(False)
        self.export_txt_btn.setVisible(False)
        self.text_edit.clear()

    def _open_output_dir(self):
        if self._file_path:
            parent = self.window() if self.window() else None
            if hasattr(parent, '_open_output_dir'):
                parent._open_output_dir(self._file_path)

    def _export_srt(self):
        self._export("srt")

    def _export_vtt(self):
        self._export("vtt")

    def _export_txt(self):
        self._export("txt")

    def _export(self, fmt):
        if not self._subtitles or not self._file_path:
            return

        file_path_str = str(self._file_path)
        if file_path_str.startswith("http://") or file_path_str.startswith("https://"):
            default_name = "subtitles"
        else:
            default_name = Path(file_path_str).stem
        filter_map = {
            "srt": "SRT 字幕文件 (*.srt)",
            "vtt": "VTT 字幕文件 (*.vtt)",
            "txt": "文本文件 (*.txt)",
        }

        path, _ = QFileDialog.getSaveFileName(
            self, f"导出 {fmt.upper()} 字幕",
            str(Path.home() / f"{default_name}.{fmt}"),
            filter_map.get(fmt, f"*.{fmt}"),
        )
        if not path:
            return

        client = WhisperApiClient()
        save_fn = getattr(client, f"save_{fmt}")
        save_fn(self._subtitles, path)

        QMessageBox.information(self, "导出成功", f"字幕已导出至:\n{path}")

    @staticmethod
    def _format_time(seconds):
        return format_subtitle_time(seconds, ",")


class TitleFetcher(QObject):
    title_ready = pyqtSignal(str, str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url
        self._proc = None

    def start(self):
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.MergedChannels)
        self._proc.finished.connect(self._on_finished)
        self._proc.start('yt-dlp', ['--print', 'title', '--no-playlist', self.url])

    def _on_finished(self, exit_code):
        if exit_code == 0 and self._proc:
            import locale
            enc = locale.getpreferredencoding()
            raw = bytes(self._proc.readAll()).decode(enc, 'replace').strip()
            if raw:
                self.title_ready.emit(self.url, raw[:100])


class WorkerThread(QThread):
    progress_updated = pyqtSignal(str, int, str, str)
    task_completed = pyqtSignal(str, object, str)
    task_error = pyqtSignal(str, str)
    all_done = pyqtSignal()

    def __init__(self, files, is_urls=None, language="auto", service="local"):
        super().__init__()
        if not isinstance(files, (list, tuple)):
            files = []
        self.items = list(files) if files else []
        self.language = language
        self.service = service
        self._running = True
        self._cancel_event = threading.Event()
        self.client = WhisperApiClient()
        self.local = LocalWhisperTranscriber()
        self._use_api = self.client.health_check()

    def run(self):
        try:
            if not self._use_api:
                print("Whisper server not available, using local transcriber")

            if not self.items:
                print("No items to process")
                self.all_done.emit()
                return

            for path_or_url, is_url in self.items:
                if not self._running or self._cancel_event.is_set():
                    break

                key = str(path_or_url)
                self.progress_updated.emit(key, 0, "准备处理...", "queued")

                try:
                    if is_url:
                        self._process_url(key)
                    else:
                        self._process_local_file(key)
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    self.task_error.emit(key, str(e)[:200])
        except Exception as e:
            import traceback
            traceback.print_exc()

        self.all_done.emit()

    def _process_url(self, url):
        try:
            self.progress_updated.emit(url, 5, "正在提交到服务器...", "queued")

            if not self._use_api:
                self.task_error.emit(url, "URL 下载需要 Whisper 服务器")
                return

            result = self.client.transcribe_url(url, self.language, self.service)
            if not isinstance(result, dict):
                self.task_error.emit(url, f"服务器返回异常: {result}")
                return
            if "error" in result:
                self.task_error.emit(url, result["error"])
                return

            task_id = result.get("task_id", "")
            if not task_id:
                self.task_error.emit(url, "服务器未返回任务 ID")
                return

            def on_progress(p, m, s):
                try:
                    status = s if s != "transcribing" else "processing"
                    self.progress_updated.emit(url, int(p), str(m or ""), str(status))
                except Exception:
                    pass

            task_result = self.client.wait_for_result(
                task_id,
                progress_callback=on_progress,
                cancel_checker=lambda: self._cancel_event.is_set(),
            )

            if not isinstance(task_result, dict):
                self.task_error.emit(url, "服务器返回异常结果")
                return

            status = task_result.get("status")
            if status == "completed":
                subtitles = task_result.get("subtitles", [])
                lang = task_result.get("detected_language", "unknown")
                self.task_completed.emit(url, subtitles or [], lang or "unknown")
            elif status == "cancelled":
                pass
            else:
                self.task_error.emit(
                    url,
                    task_result.get("message", "未知错误"),
                )
        except Exception as e:
            self.task_error.emit(url, f"处理失败: {str(e)[:100]}")

    def _process_local_file(self, file_path):
        self.progress_updated.emit(file_path, 0, "准备处理...", "queued")

        if self._use_api:
            cached = self.client.get_task_status(Path(file_path).stem)
            if cached and cached.get("status") == "completed":
                subs = cached.get("subtitles", [])
                lang = cached.get("detected_language", "unknown")
                self.progress_updated.emit(file_path, 100, "从缓存加载", "cached")
                self.task_completed.emit(file_path, subs, lang)
                return

            result = self.client.upload_file(file_path, self.language, self.service)
            if "error" not in result:
                task_id = result.get("task_id", Path(file_path).stem)
                task_result = self.client.wait_for_result(
                    task_id,
                    progress_callback=lambda p, m, s: self.progress_updated.emit(
                        file_path, p, m, "processing" if s != "completed" else "completed"
                    ),
                    cancel_checker=lambda: self._cancel_event.is_set(),
                )
                status = task_result.get("status")
                if status == "completed":
                    subs = task_result.get("subtitles", [])
                    lang = task_result.get("detected_language", "unknown")
                    self.task_completed.emit(file_path, subs, lang)
                    return
                if status == "cancelled":
                    return
                self.task_error.emit(file_path, task_result.get("message", "未知错误"))
                return

            self.progress_updated.emit(file_path, 0, "API 失败，切换到本地模式...", "processing")

        def on_progress(p, m, s):
            self.progress_updated.emit(file_path, p, m, s)

        subtitles, lang = self.local.transcribe(file_path, self.language, on_progress)
        if lang == "error":
            self.task_error.emit(file_path, subtitles[0] if isinstance(subtitles, list) and subtitles else "转写失败")
        else:
            self.task_completed.emit(file_path, subtitles, lang)

    def stop(self):
        self._running = False
        self._cancel_event.set()


class PackageProgressDialog(QDialog):
    """Small modeless progress window for ChatGPT package generation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._finished = False
        self.setObjectName("package_progress_dialog")
        self.setWindowTitle("正在生成 ChatGPT 包")
        self.setModal(False)
        self.setMinimumSize(460, 190)
        self.setStyleSheet(f"""
            QDialog#package_progress_dialog {{
                background-color: {THEME["bg_dark"]};
                border: 1px solid {THEME["border"]};
                border-radius: 14px;
            }}
            QLabel#package_title {{
                color: {THEME["text_primary"]};
                font-size: 17px;
                font-weight: 800;
            }}
            QLabel#package_message {{
                color: {THEME["info"]};
                font-size: 13px;
                font-weight: 600;
            }}
            QLabel#package_detail {{
                color: {THEME["text_muted"]};
                font-size: 11px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.title_label = QLabel("📦 正在生成 ChatGPT 分析包")
        self.title_label.setObjectName("package_title")
        layout.addWidget(self.title_label)

        self.message_label = QLabel("准备生成...")
        self.message_label.setObjectName("package_message")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)

        self.detail_label = QLabel("")
        self.detail_label.setObjectName("package_detail")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        hint = QLabel("生成过程中窗口会持续显示，完成或失败后会弹出提醒。")
        hint.setObjectName("package_detail")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def start_progress(self, source_video, output_dir):
        self._finished = False
        self.title_label.setText("📦 正在生成 ChatGPT 分析包")
        self.detail_label.setText(f"来源: {Path(str(source_video)).name}\n输出目录: {output_dir}")
        self.update_progress("正在准备生成包...", 5)
        self.show()
        self.raise_()
        self.activateWindow()

    def update_progress(self, message, percent=None):
        self.message_label.setText(str(message or "正在处理..."))
        if percent is None:
            percent = min(95, max(5, self.progress_bar.value() + 6))
        self.progress_bar.setValue(max(0, min(100, int(percent))))

    def mark_finished(self, success=True, message=""):
        self._finished = True
        if success:
            self.title_label.setText("✅ ChatGPT 分析包生成完成")
            self.update_progress(message or "生成完成", 100)
        else:
            self.title_label.setText("❌ ChatGPT 分析包生成失败")
            self.update_progress(message or "生成失败", 0)

    def closeEvent(self, event):
        if not self._finished:
            event.ignore()
            return
        super().closeEvent(event)


class ChatGPTPackageWorker(QThread):
    progress = pyqtSignal(str)
    progress_detail = pyqtSignal(str, int)
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, source_video, srt_path, output_dir, title="", parent=None):
        super().__init__(parent)
        self.source_video = Path(source_video)
        self.srt_path = Path(srt_path)
        self.output_dir = Path(output_dir)
        self.title = title or self.source_video.stem

    def _emit_progress(self, message, percent):
        self.progress.emit(message)
        self.progress_detail.emit(message, int(percent))

    def run(self):
        import shutil
        import subprocess

        try:
            run_kwargs = {"check": True}
            if os.name == "nt":
                run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                self.failed.emit("未找到 ffmpeg，无法生成压缩视频和关键帧。请先安装 ffmpeg 或把 ffmpeg 加入 PATH。")
                return
            if not self.source_video.exists():
                self.failed.emit(f"找不到原视频文件: {self.source_video}")
                return
            if not self.srt_path.exists():
                self.failed.emit(f"找不到字幕文件: {self.srt_path}")
                return

            self._emit_progress("正在准备 ChatGPT 包目录...", 8)
            package_dir = self.output_dir / "chatgpt_package"
            frames_dir = package_dir / "frames"
            package_dir.mkdir(parents=True, exist_ok=True)
            frames_dir.mkdir(parents=True, exist_ok=True)

            self._emit_progress("正在复制字幕文件...", 12)
            package_srt = package_dir / self.srt_path.name
            shutil.copy2(str(self.srt_path), str(package_srt))

            proxy_video = package_dir / f"{self.source_video.stem}_proxy_480p.mp4"
            self._emit_progress("正在生成 480p 低帧率代理视频...", 25)
            subprocess.run(
                [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(self.source_video),
                    "-vf", "scale=-2:480,fps=10",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
                    "-an", str(proxy_video),
                ],
                **run_kwargs,
            )

            self._emit_progress("正在抽取关键帧...", 50)
            for old_frame in frames_dir.glob("frame_*.jpg"):
                old_frame.unlink(missing_ok=True)
            subprocess.run(
                [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(self.source_video),
                    "-vf", "fps=1/30,scale=-2:720,format=yuvj420p",
                    "-q:v", "4", str(frames_dir / "frame_%04d.jpg"),
                ],
                **run_kwargs,
            )

            frames = sorted(frames_dir.glob("frame_*.jpg"))
            if not frames:
                subprocess.run(
                    [
                        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(self.source_video),
                        "-frames:v", "1",
                        "-vf", "scale=-2:720,format=yuvj420p",
                        "-q:v", "4", str(frames_dir / "frame_0001.jpg"),
                    ],
                    **run_kwargs,
                )
                frames = sorted(frames_dir.glob("frame_*.jpg"))
            self._emit_progress("正在写入包清单 manifest.json...", 68)
            manifest = {
                "title": self.title,
                "source_video": str(self.source_video),
                "subtitle_file": package_srt.name,
                "proxy_video": proxy_video.name,
                "light_upload_zip": "chatgpt_upload_light.zip",
                "full_upload_zip": "chatgpt_upload_full.zip",
                "frame_interval_seconds": 30,
                "frames_dir": "frames",
                "frame_count": len(frames),
                "recommended_upload_to_chatgpt": [
                    "chatgpt_upload_light.zip",
                ],
                "upload_note": "默认上传 chatgpt_upload_light.zip 即可，它不包含 MP4，体积更小且只算 1 个文件。需要连续动作或完整画面上下文时再上传 chatgpt_upload_full.zip。",
                "note": "字幕保留完整语义；frames 用于报告中的关键视觉参考；proxy 视频只放在 full 包中作为补充材料。",
            }
            (package_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            import zipfile
            light_zip = package_dir / "chatgpt_upload_light.zip"
            full_zip = package_dir / "chatgpt_upload_full.zip"
            for zip_path in (light_zip, full_zip):
                if zip_path.exists():
                    zip_path.unlink()

            self._emit_progress("正在生成轻量上传包...", 80)
            with zipfile.ZipFile(light_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(package_srt, package_srt.name)
                zf.write(package_dir / "manifest.json", "manifest.json")
                for frame in frames:
                    zf.write(frame, f"frames/{frame.name}")

            self._emit_progress("正在生成完整上传包...", 92)
            with zipfile.ZipFile(full_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(package_srt, package_srt.name)
                zf.write(proxy_video, proxy_video.name)
                zf.write(package_dir / "manifest.json", "manifest.json")
                for frame in frames:
                    zf.write(frame, f"frames/{frame.name}")

            self._emit_progress("ChatGPT 包生成完成", 100)
            self.completed.emit(str(package_dir))
        except subprocess.CalledProcessError as e:
            self.failed.emit(f"ffmpeg 处理失败，退出码: {e.returncode}")
        except Exception as e:
            self.failed.emit(str(e)[:300])


class LocalizationWorker(QThread):
    progress_updated = pyqtSignal(str, int, str, str)
    task_completed = pyqtSignal(str, str)
    task_error = pyqtSignal(str, str)

    def __init__(self, file_path, srt_path, source_video, config, output_dir):
        super().__init__()
        self.file_path = file_path
        self.srt_path = Path(srt_path)
        self.source_video = Path(source_video)
        self.config = config
        self.output_dir = Path(output_dir)
        self._cancelled = False
        self._job_id = None
        self._client = LocalizationClient()

    def stop(self):
        self._cancelled = True
        if self._job_id:
            try:
                self._client.cancel_job(self._job_id)
            except Exception:
                pass

    def run(self):
        try:
            self.progress_updated.emit(self.file_path, 0, "准备本地化工作空间...", "processing")

            workspace = self.srt_path.parent / "localization_workspace"
            for d in ["source", "subtitles", "translation", "rendered", "audio", "audio/tts", "checkpoints", "logs", "temp"]:
                (workspace / d).mkdir(parents=True, exist_ok=True)

            import shutil
            raw_video = workspace / "source" / self.source_video.name
            try:
                shutil.copy2(str(self.source_video), str(raw_video))
            except Exception as e:
                self.task_error.emit(self.file_path, f"复制视频文件失败: {e}")
                return

            source_srt_name = self.srt_path.name
            source_sub = workspace / "subtitles" / source_srt_name
            try:
                shutil.copy2(str(self.srt_path), str(source_sub))
            except Exception as e:
                self.task_error.emit(self.file_path, f"复制字幕文件失败: {e}")
                return

            self.progress_updated.emit(self.file_path, 5, "连接本地化引擎...", "processing")
            if not self._client.health_check():
                self.task_error.emit(self.file_path, "本地化引擎未启动，请先启动服务")
                return

            self.progress_updated.emit(self.file_path, 10, "提交翻译任务...", "processing")
            cfg = self.config
            result = self._client.create_job(
                workspace_dir=str(workspace),
                source_video=str(raw_video),
                source_subtitle=str(source_sub),
                source_language=cfg.get("source_language", "auto"),
                target_language=cfg.get("target_language", "zh"),
                subtitle_mode=cfg.get("subtitle_mode", "bilingual"),
                burn_subtitles=cfg.get("burn_subtitles", False),
                embed_soft_subtitles=cfg.get("embed_soft_subtitles", False),
                dubbing_enabled=cfg.get("dubbing_enabled", False),
                tts_provider=cfg.get("tts_provider", "edge-tts"),
                tts_voice=cfg.get("tts_voice", ""),
                translation=cfg.get("translation_config"),
            )

            if "error" in result:
                self.task_error.emit(self.file_path, result["error"])
                return

            self._job_id = result.get("job_id")
            if not self._job_id:
                self.task_error.emit(self.file_path, "引擎未返回任务 ID")
                return

            def on_progress(p, m, s):
                self.progress_updated.emit(self.file_path, int(p), str(m or ""), str(s or "processing"))

            final = self._client.wait_for_result(
                self._job_id,
                progress_callback=on_progress,
                poll_interval=1.0,
                cancel_checker=lambda: self._cancelled,
            )

            status = final.get("status")
            if status == "completed":
                trans_dir = workspace / "translation"
                output_srt = trans_dir / f"{self.srt_path.stem}_translated.srt"
                if not output_srt.exists():
                    output_srt = trans_dir / source_srt_name
                if not output_srt.exists():
                    srt_files = list(trans_dir.glob("*.srt"))
                    if srt_files:
                        output_srt = srt_files[0]
                if output_srt.exists():
                    dst = self.srt_path.parent / f"{self.srt_path.stem}_translated.srt"
                    shutil.copy2(str(output_srt), str(dst))
                    self.task_completed.emit(self.file_path, str(dst))
                else:
                    self.task_completed.emit(self.file_path, "")
            elif status == "cancelled":
                pass
            else:
                self.task_error.emit(self.file_path, final.get("message", "处理失败"))

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.task_error.emit(self.file_path, str(e)[:200])


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.video_items = {}
        self.worker = None
        self.package_worker = None
        self._package_progress_dialog = None
        self._stopped = False
        self._localization_config = None
        self._localization_worker = None
        self.output_dir = Path(WHISPER_SERVER) / "output" if WHISPER_SERVER.exists() else Path.cwd() / "output"
        self.history = HistoryManager(self.output_dir / "history.json")
        self._setup_ui()
        self._refresh_localization_config_from_settings()
        self._check_server()

    def _setup_ui(self):
        self.setWindowTitle("Video2Subtitles - 视频字幕生成")
        self.setMinimumSize(1200, 750)
        self.setGeometry(100, 100, 1400, 850)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._create_toolbar(main_layout)
        self._create_url_bar(main_layout)
        self._create_content(main_layout)
        self._create_statusbar()

    def _create_toolbar(self, parent_layout):
        toolbar = QWidget()
        toolbar.setStyleSheet(f"""
            QWidget {{
                background-color: {THEME["bg_medium"]};
                border-bottom: 1px solid {THEME["border"]};
            }}
        """)
        toolbar.setFixedHeight(64)
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)

        logo = QLabel("🎬")
        logo.setStyleSheet("font-size: 24px;")
        layout.addWidget(logo)

        title = QLabel("Video2Subtitles")
        title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {THEME['text_primary']}; letter-spacing: 1px;")
        layout.addWidget(title)

        subtitle = QLabel("视频字幕生成工具")
        subtitle.setStyleSheet(f"font-size: 12px; color: {THEME['text_muted']}; padding-top: 4px;")
        layout.addWidget(subtitle)

        layout.addStretch()

        self.server_status = QLabel()
        self.server_status.setStyleSheet(f"font-size: 12px; color: {THEME['text_muted']}; padding: 4px 12px;")
        self.server_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.server_status)

        self.add_files_btn = QPushButton("📁 添加视频")
        self.add_files_btn.setObjectName("btn_secondary")
        self.add_files_btn.setFixedHeight(38)
        self.add_files_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.add_files_btn.clicked.connect(self._add_files)
        layout.addWidget(self.add_files_btn)

        self.add_folder_btn = QPushButton("📂 添加目录")
        self.add_folder_btn.setObjectName("btn_secondary")
        self.add_folder_btn.setFixedHeight(38)
        self.add_folder_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.add_folder_btn.clicked.connect(self._add_folder)
        layout.addWidget(self.add_folder_btn)

        self.start_btn = QPushButton("▶ 开始处理")
        self.start_btn.setFixedHeight(38)
        self.start_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.start_btn.clicked.connect(lambda: self._start_processing())
        self.start_btn.setEnabled(False)
        layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setObjectName("btn_danger")
        self.stop_btn.setFixedHeight(38)
        self.stop_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.stop_btn.clicked.connect(self._stop_processing)
        self.stop_btn.setEnabled(False)
        layout.addWidget(self.stop_btn)

        self.output_dir_btn = QPushButton("📁 输出目录")
        self.output_dir_btn.setObjectName("btn_secondary")
        self.output_dir_btn.setFixedHeight(38)
        self.output_dir_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.output_dir_btn.clicked.connect(self._change_output_dir)
        self.output_dir_btn.setToolTip(str(self.output_dir))
        layout.addWidget(self.output_dir_btn)

        self.localize_btn = QPushButton("🌐 本地化")
        self.localize_btn.setObjectName("btn_secondary")
        self.localize_btn.setFixedHeight(38)
        self.localize_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.localize_btn.clicked.connect(self._show_localization_dialog)
        layout.addWidget(self.localize_btn)

        settings_btn = QPushButton("⚙")
        settings_btn.setObjectName("btn_icon")
        settings_btn.setFixedSize(38, 38)
        settings_btn.setCursor(QCursor(Qt.PointingHandCursor))
        settings_btn.clicked.connect(self._show_settings)
        layout.addWidget(settings_btn)

        parent_layout.addWidget(toolbar)

    def _create_url_bar(self, parent_layout):
        bar = QWidget()
        bar.setStyleSheet(f"""
            QWidget {{
                background-color: {THEME["bg_medium"]};
                border-bottom: 1px solid {THEME["border"]};
            }}
        """)
        bar.setFixedHeight(56)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(10)

        icon = QLabel("🔗")
        icon.setStyleSheet("font-size: 16px;")
        layout.addWidget(icon)

        label = QLabel("视频链接:")
        label.setStyleSheet(f"font-size: 13px; color: {THEME['text_secondary']}; font-weight: 500;")
        layout.addWidget(label)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("输入 YouTube / Bilibili 视频链接，回车添加...")
        self.url_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {THEME["bg_light"]};
                border: 1px solid {THEME["border"]};
                border-radius: 8px;
                padding: 8px 14px;
                font-size: 13px;
                color: {THEME["text_primary"]};
            }}
            QLineEdit:focus {{
                border-color: {THEME["accent"]};
            }}
        """)
        self.url_input.returnPressed.connect(self._add_url)
        layout.addWidget(self.url_input, 1)

        self.add_url_btn = QPushButton("添加链接")
        self.add_url_btn.setFixedHeight(34)
        self.add_url_btn.setFixedWidth(100)
        self.add_url_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.add_url_btn.clicked.connect(self._add_url)
        layout.addWidget(self.add_url_btn)

        parent_layout.addWidget(bar)

    def _create_content(self, parent_layout):
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(2)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(20, 16, 10, 16)
        left_layout.setSpacing(12)

        list_header = QHBoxLayout()
        list_title = QLabel("视频列表")
        list_title.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {THEME['text_primary']};")
        list_header.addWidget(list_title)

        self.count_label = QLabel("0 个文件")
        self.count_label.setStyleSheet(f"font-size: 12px; color: {THEME['text_muted']}; padding-top: 4px;")
        list_header.addWidget(self.count_label)
        list_header.addStretch()

        self.clear_btn = QPushButton("清空")
        self.clear_btn.setObjectName("btn_secondary")
        self.clear_btn.setFixedHeight(30)
        self.clear_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.clear_btn.clicked.connect(self._clear_all)
        self.clear_btn.setEnabled(False)
        list_header.addWidget(self.clear_btn)

        self.retry_all_btn = QPushButton("重试失败")
        self.retry_all_btn.setObjectName("btn_secondary")
        self.retry_all_btn.setFixedHeight(30)
        self.retry_all_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.retry_all_btn.clicked.connect(self._retry_failed)
        self.retry_all_btn.setEnabled(False)
        list_header.addWidget(self.retry_all_btn)

        self.history_btn = QPushButton("历史记录")
        self.history_btn.setObjectName("btn_secondary")
        self.history_btn.setFixedHeight(30)
        self.history_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.history_btn.clicked.connect(self._show_history)
        list_header.addWidget(self.history_btn)

        left_layout.addLayout(list_header)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.file_list.setSpacing(4)
        self.file_list.currentRowChanged.connect(self._on_selection_changed)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._show_context_menu)
        self.file_list.setAcceptDrops(True)
        self.file_list.dragEnterEvent = self._drag_enter_event
        self.file_list.dragMoveEvent = self._drag_move_event
        self.file_list.dropEvent = self._drop_event
        left_layout.addWidget(self.file_list)

        splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 16, 20, 16)
        right_layout.setSpacing(0)

        self.subtitle_viewer = SubtitleViewer()
        right_layout.addWidget(self.subtitle_viewer)

        splitter.addWidget(right_panel)
        splitter.setSizes([550, 600])

        parent_layout.addWidget(splitter, 1)

    def _create_statusbar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("就绪 - 请添加视频文件")
        self.status_bar.addWidget(self.status_label, 1)

        self.progress_bar_total = QProgressBar()
        self.progress_bar_total.setRange(0, 100)
        self.progress_bar_total.setValue(0)
        self.progress_bar_total.setFixedWidth(200)
        self.progress_bar_total.setFixedHeight(18)
        self.progress_bar_total.setTextVisible(True)
        self.progress_bar_total.setFormat("总进度: %p%")
        self.status_bar.addPermanentWidget(self.progress_bar_total)

    def _check_server(self):
        try:
            client = WhisperApiClient()
            if client.health_check():
                info = client.get_server_info()
                self.server_status.setText("● 服务器已连接")
                self.server_status.setStyleSheet(f"font-size: 12px; color: {THEME['success']}; font-weight: 600; padding: 4px 12px;")
            else:
                self.server_status.setText("○ 服务器未连接")
                self.server_status.setStyleSheet(f"font-size: 12px; color: {THEME['error']}; font-weight: 600; padding: 4px 12px;")
        except Exception:
            self.server_status.setText("○ 服务器未连接")
            self.server_status.setStyleSheet(f"font-size: 12px; color: {THEME['error']}; font-weight: 600; padding: 4px 12px;")

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择视频文件", "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm *.m4v *.mpg *.mpeg);;所有文件 (*.*)",
        )
        if not paths:
            return
        self._add_videos(paths)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择视频目录")
        if not folder:
            return
        client = WhisperApiClient()
        videos = client.scan_video_files([folder])
        if not videos:
            QMessageBox.information(self, "提示", "该目录下未找到支持的视频文件")
            return
        self._add_videos([str(v) for v in videos])

    def _add_videos(self, paths):
        added = 0
        for p in paths:
            p = str(Path(p).resolve())
            if p in self.video_items:
                continue
            item = QListWidgetItem()
            widget = VideoItemWidget(p)
            item.setSizeHint(widget.sizeHint())
            self.file_list.addItem(item)
            self.file_list.setItemWidget(item, widget)
            self.video_items[p] = {"item": item, "widget": widget}

            entry = self.history.get(p)
            if entry:
                subs = self.history.get_subtitles(p)
                if subs:
                    widget.subtitles = subs
                    out_dir = entry.get("output_dir") or ""
                    msg = f"✅ 已有字幕 ({entry.get('language', '?')})"
                    if out_dir:
                        msg += f" · {Path(out_dir).name}"
                    widget.update_status("completed", 100, msg)
                else:
                    entry = self.history.get(p)
                    srt_path = entry.get("srt_path") if entry else ""
                    widget.update_status("completed", 100, f"历史记录 ({Path(srt_path).name})")
            added += 1

        if added > 0:
            self._update_counts()
            self.start_btn.setEnabled(len(self.video_items) > 0)
            self.clear_btn.setEnabled(len(self.video_items) > 0)
            self.status_label.setText(f"已添加 {added} 个文件（含历史记录），共 {len(self.video_items)} 个")

    def _clear_all(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "请先停止当前任务")
            return
        self.file_list.clear()
        self.video_items.clear()
        self.subtitle_viewer.clear()
        self._update_counts()
        self.start_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.retry_all_btn.setEnabled(False)
        self.progress_bar_total.setValue(0)
        self.status_label.setText("就绪 - 请添加视频文件")

    def _retry_failed(self):
        failed = []
        for path, data in self.video_items.items():
            widget = data["widget"]
            if widget.status == "error":
                failed.append(path)
                widget.update_status("pending")

        if not failed:
            QMessageBox.information(self, "提示", "没有失败的任务")
            return

        self._start_processing(specific_files=failed)
        self.status_label.setText(f"正在重试 {len(failed)} 个失败的任务...")

    def _start_processing(self, specific_files=None):
        try:
            self._stopped = False
            self._refresh_localization_config_from_settings()
            if specific_files is None:
                pending = [(p, d.get("is_url", False)) for p, d in self.video_items.items()
                           if d["widget"].status in ("pending", "error")]
                if not pending:
                    QMessageBox.information(self, "提示", "没有待处理的任务")
                    return
                items = pending
            elif not isinstance(specific_files, (list, tuple)):
                items = [(p, d.get("is_url", False)) for p, d in self.video_items.items()
                         if d["widget"].status in ("pending", "error")]
                if not items:
                    return
            else:
                items = [(p, self.video_items[p].get("is_url", False)) for p in specific_files if p in self.video_items]

            if not items:
                return

            for p, _ in items:
                if p in self.video_items:
                    self.video_items[p]["widget"].update_status("queued")

            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.add_files_btn.setEnabled(False)
            self.add_folder_btn.setEnabled(False)

            language = "auto"
            if hasattr(self, "lang_combo"):
                language_text = self.lang_combo.currentText().strip()
                language = language_text.split("(", 1)[0].strip() or "auto"
            self.worker = WorkerThread(items, language=language, service="local")
            self.worker.progress_updated.connect(self._on_progress)
            self.worker.task_completed.connect(self._on_completed)
            self.worker.task_error.connect(self._on_error)
            self.worker.all_done.connect(self._on_all_done)
            self.worker.start()

            self.status_label.setText(f"正在处理 {len(items)} 个任务...")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(tb)
            QMessageBox.critical(self, "启动失败", f"无法启动处理线程:\n{str(e)[:200]}\n\n详情:\n{tb[-500:]}")
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.add_files_btn.setEnabled(True)
            self.add_folder_btn.setEnabled(True)

    def _stop_processing(self):
        self._stopped = True
        if self.worker:
            self.worker.stop()
            self.worker.wait(2000)
        if self._localization_worker:
            self._localization_worker.stop()
            self._localization_worker.wait(2000)
            self._localization_worker = None
        for path, data in self.video_items.items():
            widget = data["widget"]
            if widget.status in ("queued", "downloading", "processing", "pending"):
                widget.update_status("cancelled", widget.progress, "已取消")
        self._on_all_done()

    def _on_progress(self, file_path, progress, message, status):
        try:
            if self._stopped:
                return
            if file_path in self.video_items:
                self.video_items[file_path]["widget"].update_status(status, progress, message)
        except Exception as e:
            print(f"_on_progress error: {e}")

    def _on_completed(self, file_path, subtitles, language):
        try:
            if self._stopped:
                return
            if file_path in self.video_items:
                widget = self.video_items[file_path]["widget"]
                widget.update_status("completed", 100, f"完成 ({language})")
                widget.subtitles = subtitles

                srt_path = self._save_output(file_path, subtitles, widget.is_url, language)
                if srt_path:
                    out_dir_name = srt_path.parent.name
                    msg = f"完成 ({language}) · {out_dir_name}/"
                    widget.update_status("completed", 100, msg)

                idx = self.file_list.row(self.video_items[file_path]["item"])
                if idx == self.file_list.currentRow():
                    self.subtitle_viewer.show_subtitles(file_path, subtitles, widget.is_url)

                if self._localization_config and self._localization_config.get("is_translate_mode"):
                    widget.update_status("processing", 50, "开始翻译字幕...")
                    self._start_localization(file_path, str(srt_path))

            self._update_progress()
        except Exception as e:
            print(f"_on_completed error: {e}")

    def _on_error(self, file_path, error_msg):
        try:
            if self._stopped:
                return
            if file_path and file_path in self.video_items:
                self.video_items[file_path]["widget"].update_status("error", 0, error_msg[:80])
            elif not file_path:
                QMessageBox.critical(self, "错误", error_msg)
            self._update_progress()
        except Exception as e:
            print(f"Error in _on_error: {e}")

    def _on_all_done(self):
        try:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.add_files_btn.setEnabled(True)
            self.add_folder_btn.setEnabled(True)

            completed = sum(1 for d in self.video_items.values() if d["widget"].status == "completed")
            failed = sum(1 for d in self.video_items.values() if d["widget"].status == "error")
            cancelled = sum(1 for d in self.video_items.values() if d["widget"].status == "cancelled")
            total = len(self.video_items)

            self.retry_all_btn.setEnabled(failed > 0)

            if total > 0:
                parts = [f"完成: {completed}/{total}"]
                if failed:
                    parts.append(f"失败: {failed}/{total}")
                if cancelled:
                    parts.append(f"已取消: {cancelled}/{total}")
                self.status_label.setText("  ".join(parts))
        except Exception as e:
            print(f"_on_all_done error: {e}")

    def _change_output_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", str(self.output_dir))
        if folder:
            self.output_dir = Path(folder)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.output_dir_btn.setToolTip(str(self.output_dir))
            self.status_label.setText(f"输出目录: {self.output_dir}")

    def _save_output(self, key, subtitles, is_url=False, language="unknown"):
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            client = WhisperApiClient()
            import shutil

            if is_url:
                video_id = self._get_video_id(key)
                title = self.video_items.get(key, {}).get("title") or ""
                display_title = title
                base = self._sanitize_filename(title) if title else video_id
                base = base[:80]
                sub_dir = self.output_dir / base
                sub_dir.mkdir(parents=True, exist_ok=True)

                # Copy downloaded video from server's temp dir
                whisper_temp = WHISPER_SERVER / "temp"
                if whisper_temp.exists():
                    src_name = None
                    for f in whisper_temp.glob(f"{video_id}.*"):
                        if f.suffix in ['.mp4', '.mkv', '.webm']:
                            src_name = f.name
                            break
                    if src_name:
                        ext = Path(src_name).suffix
                        shutil.copy2(str(whisper_temp / src_name), str(sub_dir / f"{base}{ext}"))
            else:
                src = Path(key)
                base = src.stem
                display_title = src.name  # filename.ext
                sub_dir = self.output_dir / base
                sub_dir.mkdir(parents=True, exist_ok=True)

                dst = sub_dir / src.name
                try:
                    shutil.copy2(str(src), str(dst))
                except Exception as e:
                    print(f"Copy video failed: {e}")

            srt_path = sub_dir / f"{base}.srt"
            client.save_srt(subtitles, str(srt_path))

            self.history.put(key, self.history.make_entry(
                subtitles, language, srt_path, sub_dir, is_url, display_title
            ))

            return srt_path
        except Exception as e:
            print(f"Save output failed: {e}")
            return None

    def _get_video_id(self, url):
        import re, hashlib
        match = re.search(r'(?:v=|\/videos\/|embed\/|youtu.be\/|\/v\/|\/e\/|watch\?v=|&v=)([^#&\n]*)', url)
        if match:
            return match.group(1)
        return hashlib.md5(url.encode()).hexdigest()[:11]

    def _on_title_fetched(self, url, title):
        if hasattr(self, '_fetchers') and url in self._fetchers:
            del self._fetchers[url]
        if url in self.video_items:
            self.video_items[url]["title"] = title
            widget = self.video_items[url]["widget"]
            widget.title = title
            display = title[:55] + "..." if len(title) > 55 else title
            widget.name_label.setText(display)
            widget.name_label.setToolTip(title)

    @staticmethod
    def _sanitize_filename(name):
        return sanitize_filename(name)

    def _add_url(self):
        try:
            raw = self.url_input.text().strip()
            if not raw:
                return

            url = raw if raw.startswith(("http://", "https://")) else "https://" + raw

            import re
            domain_match = re.match(r"https?://([^/]+)", url)
            if not domain_match:
                QMessageBox.warning(self, "无效链接", "请输入有效的视频链接（以 http:// 或 https:// 开头）")
                return

            domain = domain_match.group(1).lower()
            supported = (
                "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
                "bilibili.com", "www.bilibili.com",
                "douyin.com", "www.douyin.com",
                "v.qq.com", "ixigua.com", "www.ixigua.com",
                "youtube-nocookie.com",
            )
            is_supported = any(d in domain or domain in d for d in supported)

            if not is_supported:
                ret = QMessageBox.question(
                    self, "确认添加",
                    f"该域名 ({domain}) 不在已知视频平台列表中，\n"
                    f"仍可能通过 yt-dlp 下载，是否继续？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if ret != QMessageBox.Yes:
                    return

            self.url_input.clear()

            if url in self.video_items:
                QMessageBox.information(self, "提示", "该链接已在列表中")
                return

            item = QListWidgetItem()
            widget = VideoItemWidget(url, is_url=True, title=None)
            item.setSizeHint(widget.sizeHint())
            self.file_list.addItem(item)
            self.file_list.setItemWidget(item, widget)
            self.video_items[url] = {"item": item, "widget": widget, "is_url": True, "title": None}

            # Fetch title asynchronously via QProcess (no threading issues)
            fetcher = TitleFetcher(url, self)
            fetcher.title_ready.connect(self._on_title_fetched)
            if not hasattr(self, '_fetchers'):
                self._fetchers = {}
            self._fetchers[url] = fetcher
            fetcher.start()

            entry = self.history.get(url)
            if entry:
                subs = self.history.get_subtitles(url)
                if subs:
                    widget.subtitles = subs
                    msg = f"✅ 已有字幕 ({entry.get('language', '?')})"
                    out_dir = entry.get("output_dir") or ""
                    if out_dir:
                        msg += f" · {Path(out_dir).name}"
                    widget.update_status("completed", 100, msg)

            self._update_counts()
            self.start_btn.setEnabled(len(self.video_items) > 0)
            self.clear_btn.setEnabled(len(self.video_items) > 0)
            self.status_label.setText("链接已添加" + ("（历史记录已载入）" if self.history.exists(url) else "，点击「开始处理」下载并生成字幕"))
        except Exception as e:
            QMessageBox.critical(self, "添加失败", f"无法添加链接:\n{str(e)[:200]}")

    def _show_context_menu(self, pos):
        item = self.file_list.itemAt(pos)
        if not item:
            return

        widget = self.file_list.itemWidget(item)
        key = widget.file_path
        is_url = widget.is_url

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {THEME["bg_medium"]};
                border: 1px solid {THEME["border"]};
                border-radius: 10px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 8px 24px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background-color: {THEME["accent"]};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {THEME["border"]};
                margin: 4px 8px;
            }}
        """)

        remove_action = QAction("🗑 移除此项", self)
        remove_action.triggered.connect(lambda: self._remove_file(key))
        menu.addAction(remove_action)

        if widget.status in ("error", "cancelled"):
            label = "🔄 重新处理" if widget.status == "cancelled" else "🔄 重试"
            retry_action = QAction(label, self)
            retry_action.triggered.connect(lambda: self._start_processing(specific_files=[key]))
            menu.addAction(retry_action)

        if widget.status == "completed" and widget.subtitles:
            menu.addSeparator()
            export_srt = QAction("📄 导出 SRT", self)
            export_srt.triggered.connect(lambda: self._export_single(key, "srt"))
            menu.addAction(export_srt)
            export_vtt = QAction("📄 导出 VTT", self)
            export_vtt.triggered.connect(lambda: self._export_single(key, "vtt"))
            menu.addAction(export_vtt)
            export_txt = QAction("📄 导出 TXT", self)
            export_txt.triggered.connect(lambda: self._export_single(key, "txt"))
            menu.addAction(export_txt)
            package_action = QAction("📦 生成 ChatGPT 分析包", self)
            package_action.triggered.connect(lambda: self._generate_chatgpt_package(key))
            menu.addAction(package_action)

        menu.addSeparator()
        open_out = QAction("📂 打开输出目录", self)
        open_out.triggered.connect(lambda: self._open_output_dir(key))
        menu.addAction(open_out)

        if is_url:
            open_url = QAction("🌐 在浏览器中打开", self)
            open_url.triggered.connect(lambda: webbrowser.open(key))
            menu.addAction(open_url)
        else:
            show_file = QAction("📁 源文件位置", self)
            show_file.triggered.connect(lambda: os.startfile(Path(key).parent))
            menu.addAction(show_file)

        if widget.status == "completed":
            del_action = QAction("🗑 删除输出文件和记录", self)
            del_action.triggered.connect(lambda: self._delete_output(key))
            menu.addAction(del_action)

        menu.exec_(self.file_list.viewport().mapToGlobal(pos))

    def _delete_output(self, key):
        ret = QMessageBox.question(
            self, "确认删除",
            "确定要删除该视频的输出文件和历史记录吗？\n（列表项将保留，可重新处理）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        out_dir = self.history.get_output_dir(key)
        self.history.remove(key)
        if out_dir and Path(out_dir).exists():
            import shutil
            shutil.rmtree(str(Path(out_dir)), ignore_errors=True)
        if key in self.video_items:
            self.video_items[key]["widget"].subtitles = None
            self.video_items[key]["widget"].update_status("pending", 0, "已删除历史记录")
        self.status_label.setText("输出和记录已删除")

    def _remove_file(self, key):
        if key in self.video_items:
            data = self.video_items.pop(key)
            self.file_list.takeItem(self.file_list.row(data["item"]))
            self._update_counts()
            self.start_btn.setEnabled(len(self.video_items) > 0)
            self.clear_btn.setEnabled(len(self.video_items) > 0)
            if len(self.video_items) == 0:
                self.subtitle_viewer.clear()

    def _open_output_dir(self, key):
        out_dir = self.history.get_output_dir(key)
        if out_dir and Path(out_dir).exists():
            os.startfile(str(Path(out_dir)))
        else:
            os.startfile(str(self.output_dir))

    def _find_output_video(self, key, out_dir):
        candidates = []
        video_exts = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".wmv", ".flv"}
        out_path = Path(out_dir)
        if out_path.exists():
            candidates.extend(
                p for p in out_path.iterdir()
                if p.is_file() and p.suffix.lower() in video_exts and "_proxy_" not in p.stem
            )
        if key in self.video_items:
            widget = self.video_items[key]["widget"]
            if not widget.is_url:
                src = Path(key)
                if src.exists():
                    candidates.insert(0, src)
        return candidates[0] if candidates else None

    def _open_path_safely(self, path):
        try:
            path = Path(path)
            if os.name == "nt":
                os.startfile(str(path))
            elif os.sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", str(path)])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            if hasattr(self, "status_label"):
                self.status_label.setText(f"打开目录失败: {exc}")

    def _set_chatgpt_package_button_enabled(self, enabled):
        btn = getattr(getattr(self, "subtitle_viewer", None), "package_btn", None)
        if btn is not None:
            btn.setEnabled(bool(enabled))
            btn.setText("📦 生成 ChatGPT 包" if enabled else "📦 生成中...")

    def _position_package_progress_dialog(self):
        dialog = getattr(self, "_package_progress_dialog", None)
        if dialog is None or not dialog.isVisible():
            return
        dialog.adjustSize()
        parent_rect = self.frameGeometry()
        width = max(dialog.width(), 460)
        height = max(dialog.height(), 190)
        dialog.resize(width, height)
        x = parent_rect.right() - width - 28
        y = parent_rect.bottom() - height - 72
        dialog.move(max(parent_rect.left() + 24, x), max(parent_rect.top() + 24, y))

    def _begin_chatgpt_package_progress(self, source_video, output_dir):
        if self._package_progress_dialog is None:
            self._package_progress_dialog = PackageProgressDialog(self)
        self._set_chatgpt_package_button_enabled(False)
        self._package_progress_dialog.start_progress(source_video, output_dir)
        self._position_package_progress_dialog()
        QTimer.singleShot(0, self._position_package_progress_dialog)

    def _on_chatgpt_package_progress(self, message, percent=None):
        if hasattr(self, "status_label"):
            self.status_label.setText(str(message or "正在生成 ChatGPT 分析包..."))
        dialog = getattr(self, "_package_progress_dialog", None)
        if dialog is not None:
            dialog.update_progress(message, percent)
            self._position_package_progress_dialog()

    def _finish_chatgpt_package_progress(self, success=True, message=""):
        self._set_chatgpt_package_button_enabled(True)
        dialog = getattr(self, "_package_progress_dialog", None)
        if dialog is not None:
            dialog.mark_finished(success, message)
            self._position_package_progress_dialog()
            QTimer.singleShot(450, dialog.hide)

    def _show_chatgpt_package_success(self, package_dir):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("生成完成")
        box.setText("ChatGPT 分析包已生成。")
        box.setInformativeText(str(package_dir))
        open_btn = box.addButton("📂 打开包目录", QMessageBox.AcceptRole)
        copy_btn = box.addButton("📋 复制路径", QMessageBox.ActionRole)
        box.addButton("知道了", QMessageBox.RejectRole)
        box.exec_()

        clicked = box.clickedButton()
        if clicked == open_btn:
            self._open_path_safely(package_dir)
        elif clicked == copy_btn:
            QApplication.clipboard().setText(str(package_dir))
            if hasattr(self, "status_label"):
                self.status_label.setText("ChatGPT 分析包路径已复制到剪贴板")

    def _show_chatgpt_package_failure(self, message):
        detail = str(message or "未知错误")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("生成失败")
        box.setText("ChatGPT 分析包生成失败。")
        box.setInformativeText(detail)
        copy_btn = box.addButton("📋 复制错误信息", QMessageBox.ActionRole)
        box.addButton("知道了", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() == copy_btn:
            QApplication.clipboard().setText(detail)
            if hasattr(self, "status_label"):
                self.status_label.setText("ChatGPT 包错误信息已复制到剪贴板")

    def _generate_chatgpt_package(self, key):
        if self.package_worker and self.package_worker.isRunning():
            QMessageBox.information(self, "正在生成", "已有一个 ChatGPT 分析包正在生成，请稍后再试。")
            return

        entry = self.history.get(key)
        if not entry:
            QMessageBox.warning(self, "无法生成", "找不到该项目的历史记录。")
            return

        out_dir = entry.get("output_dir") or ""
        srt_path = entry.get("srt_path") or ""
        if not out_dir or not Path(out_dir).exists():
            QMessageBox.warning(self, "无法生成", "找不到输出目录。")
            return
        if not srt_path or not Path(srt_path).exists():
            QMessageBox.warning(self, "无法生成", "找不到 SRT 字幕文件。")
            return

        source_video = self._find_output_video(key, out_dir)
        if not source_video:
            QMessageBox.warning(self, "无法生成", "找不到可用于压缩的原视频文件。")
            return

        title = entry.get("title") or Path(source_video).name
        self.package_worker = ChatGPTPackageWorker(source_video, srt_path, out_dir, title, self)
        self._begin_chatgpt_package_progress(source_video, out_dir)
        self.package_worker.progress.connect(lambda message: self._on_chatgpt_package_progress(message, None))
        if hasattr(self.package_worker, "progress_detail"):
            self.package_worker.progress_detail.connect(self._on_chatgpt_package_progress)
        self.package_worker.completed.connect(self._on_chatgpt_package_done)
        self.package_worker.failed.connect(self._on_chatgpt_package_failed)
        self.status_label.setText("正在生成 ChatGPT 分析包...")
        self.package_worker.start()

    def _on_chatgpt_package_done(self, package_dir):
        self.status_label.setText(f"ChatGPT 分析包已生成: {package_dir}")
        self._finish_chatgpt_package_progress(True, "ChatGPT 包生成完成")
        self._show_chatgpt_package_success(package_dir)
        self.package_worker = None

    def _on_chatgpt_package_failed(self, message):
        self.status_label.setText("ChatGPT 分析包生成失败")
        self._finish_chatgpt_package_progress(False, message)
        self._show_chatgpt_package_failure(message)
        self.package_worker = None

    def _export_single(self, key, fmt):
        if key not in self.video_items:
            return
        widget = self.video_items[key]["widget"]
        if not widget.subtitles:
            return
        filter_map = {
            "srt": "SRT 字幕文件 (*.srt)",
            "vtt": "VTT 字幕文件 (*.vtt)",
            "txt": "文本文件 (*.txt)",
        }
        if widget.is_url:
            default_name = "subtitles"
        else:
            default_name = Path(key).stem
        path, _ = QFileDialog.getSaveFileName(
            self, f"导出 {fmt.upper()}",
            str(Path.home() / f"{default_name}.{fmt}"),
            filter_map.get(fmt, f"*.{fmt}"),
        )
        if not path:
            return
        client = WhisperApiClient()
        save_fn = getattr(client, f"save_{fmt}")
        save_fn(widget.subtitles, path)
        QMessageBox.information(self, "导出成功", f"字幕已导出至:\n{path}")

    def _drag_enter_event(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _drag_move_event(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _drop_event(self, event):
        paths = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                paths.append(url.toLocalFile())
        if paths:
            self._add_videos(paths)

    def _on_selection_changed(self, row):
        try:
            if row < 0:
                self.subtitle_viewer.clear()
                return

            item = self.file_list.item(row)
            if not item:
                self.subtitle_viewer.clear()
                return

            widget = self.file_list.itemWidget(item)
            if not widget or not widget.subtitles:
                self.subtitle_viewer.clear()
                return

            self.subtitle_viewer.show_subtitles(str(widget.file_path), widget.subtitles, widget.is_url)
        except Exception:
            self.subtitle_viewer.clear()

    def _update_counts(self):
        self.count_label.setText(f"{len(self.video_items)} 个文件")

    def _update_progress(self):
        total = len(self.video_items)
        if total == 0:
            self.progress_bar_total.setValue(0)
            return

        completed = sum(1 for d in self.video_items.values() if d["widget"].status == "completed")
        progress = int((completed / total) * 100)
        self.progress_bar_total.setValue(progress)

    def _show_history(self):
        entries = self.history.all_entries()
        if not entries:
            QMessageBox.information(self, "历史记录", "暂无历史记录")
            return
        # 延迟到事件队列空闲再创建，避免点击动画导致的闪烁
        self.history_btn.setEnabled(False)
        QTimer.singleShot(0, lambda: self._show_history_dialog(entries))

    def _show_history_dialog(self, entries):
        dialog = QDialog(self)
        dialog.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setWindowTitle("历史记录")
        dialog.setMinimumSize(640, 480)
        dialog.resize(720, 540)
        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {THEME["bg_dark"]}; }}
        """)
        # 构建期间禁用更新，避免逐次触发父窗口重绘
        dialog.setUpdatesEnabled(False)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        title = QLabel(f"历史记录（共 {len(entries)} 条）")
        title.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {THEME['text_primary']}; padding-bottom: 4px;")
        layout.addWidget(title)
        list_widget = QListWidget()
        list_widget.setUpdatesEnabled(False)
        list_widget.setUniformItemSizes(True)
        list_widget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        list_widget.setSpacing(6)
        list_widget.setStyleSheet(f"""
            QListWidget {{ background: transparent; border: none; }}
            QListWidget::item {{
                background-color: {THEME["bg_card"]};
                border: none;
                border-radius: 8px;
                padding: 10px 14px;
                margin: 1px 0px;
            }}
            QListWidget::item:selected {{
                background-color: {THEME["accent_dark"]};
            }}
            QListWidget::item:hover {{
                background-color: {THEME["bg_hover"]};
            }}
        """)
        for key, entry in sorted(entries.items(), key=lambda x: x[1].get("timestamp", 0), reverse=True):
            out_dir = entry.get("output_dir") or ""
            lang = entry.get("language", "?")
            count = entry.get("subtitle_count", 0)
            is_url = entry.get("is_url", False)
            name = entry.get("title") or (Path(key).name if not is_url else key)
            detail = f"{lang} · {count} 条 · {time.strftime('%m-%d %H:%M', time.localtime(entry.get('timestamp', 0)))}"
            item = QListWidgetItem(f"{name[:120]}\n{detail}")
            item.setToolTip(key)
            item.setData(Qt.UserRole, out_dir if out_dir and Path(out_dir).exists() else "")
            item.setSizeHint(QSize(0, 58))
            list_widget.addItem(item)
        layout.addWidget(list_widget)

        def open_selected_output(item=None):
            item = item or list_widget.currentItem()
            if not item:
                return
            out_dir = item.data(Qt.UserRole)
            if out_dir:
                os.startfile(out_dir)

        list_widget.itemDoubleClicked.connect(open_selected_output)

        open_btn = QPushButton("打开输出目录")
        open_btn.setObjectName("btn_secondary")
        open_btn.setFixedHeight(34)
        open_btn.clicked.connect(open_selected_output)
        layout.addWidget(open_btn)

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("btn_secondary")
        close_btn.setFixedHeight(34)
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        # 居中于主窗口
        if self.window():
            qr = dialog.frameGeometry()
            cp = self.window().frameGeometry().center()
            qr.moveCenter(cp)
            dialog.move(qr.topLeft())
        list_widget.setUpdatesEnabled(True)
        dialog.setUpdatesEnabled(True)
        try:
            dialog.exec_()
        finally:
            self.history_btn.setEnabled(True)

    def _show_settings(self):
        dialog = SettingsDialog(self, self.output_dir)
        if dialog.exec_():
            if dialog.output_dir:
                self.output_dir = dialog.output_dir
                self.output_dir.mkdir(parents=True, exist_ok=True)
                self.output_dir_btn.setToolTip(str(self.output_dir))

    def _show_localization_dialog(self):
        dialog = LocalizationDialog(self)
        if dialog.exec_():
            self._localization_config = dialog.get_settings()
            if self._localization_config.get("is_dub_mode"):
                tgt = self._localization_config["target_language"]
                self.status_label.setText(f"配音模式: → {tgt}")
            elif self._localization_config.get("is_translate_mode"):
                src = self._localization_config["source_language"]
                tgt = self._localization_config["target_language"]
                self.status_label.setText(f"翻译模式: {src} → {tgt}")
            else:
                self.status_label.setText("字幕模式（不翻译）")

    def _refresh_localization_config_from_settings(self):
        try:
            self._localization_config = localization_runtime_config(get_effective_settings())
        except Exception as exc:
            print(f"load localization config failed: {exc}")
            self._localization_config = None

    def _start_localization(self, file_path, srt_path):
        is_url = self.video_items.get(file_path, {}).get("is_url", False)
        if is_url:
            srt_path_obj = Path(srt_path)
            video_file = None
            for ext in ['.mp4', '.mkv', '.webm']:
                candidate = srt_path_obj.with_suffix(ext)
                if candidate.exists():
                    video_file = candidate
                    break
            if not video_file:
                self.video_items[file_path]["widget"].update_status(
                    "completed", 100, "翻译暂不支持 URL 视频（找不到视频文件）"
                )
                self._update_progress()
                return
            worker = LocalizationWorker(
                file_path, srt_path, str(video_file),
                self._localization_config, self.output_dir,
            )
            worker.progress_updated.connect(self._on_localization_progress)
            worker.task_completed.connect(self._on_localization_completed)
            worker.task_error.connect(self._on_localization_error)
            self._localization_worker = worker
            worker.start()
            return
        if not Path(file_path).exists():
            self.video_items[file_path]["widget"].update_status("error", 0, "源视频文件不存在")
            return

        worker = LocalizationWorker(
            file_path, srt_path, file_path,
            self._localization_config, self.output_dir,
        )
        worker.progress_updated.connect(self._on_localization_progress)
        worker.task_completed.connect(self._on_localization_completed)
        worker.task_error.connect(self._on_localization_error)
        self._localization_worker = worker
        worker.start()

    def _on_localization_progress(self, file_path, progress, message, status):
        if self._stopped or file_path not in self.video_items:
            return
        self.video_items[file_path]["widget"].update_status(status, progress, message)

    def _on_localization_completed(self, file_path, translated_srt):
        if file_path not in self.video_items:
            return
        from subtitle_utils import parse_srt_file
        widget = self.video_items[file_path]["widget"]
        tgt = self._localization_config.get("target_language", "zh") if self._localization_config else "zh"
        if translated_srt and Path(translated_srt).exists():
            subtitles = parse_srt_file(translated_srt)
            widget.subtitles = subtitles
            widget.update_status("completed", 100, f"翻译完成 ({tgt})")
            idx = self.file_list.row(self.video_items[file_path]["item"])
            if idx == self.file_list.currentRow():
                self.subtitle_viewer.show_subtitles(
                    file_path, subtitles, self.video_items[file_path].get("is_url", False)
                )
        else:
            widget.update_status("completed", 100, f"ASR 完成（翻译输出未找到）")
        self._update_progress()

    def _on_localization_error(self, file_path, error_msg):
        if file_path in self.video_items:
            self.video_items[file_path]["widget"].update_status("error", 0, error_msg[:80])
            self._update_progress()


class SettingsDialog(QDialog):
    def __init__(self, parent=None, current_output_dir=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumSize(520, 480)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {THEME["bg_dark"]};
                border-radius: 16px;
            }}
        """)
        self.output_dir = current_output_dir
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(20)

        title = QLabel("⚙ 设置")
        title.setStyleSheet(f"font-size: 22px; font-weight: 700; color: {THEME['text_primary']};")
        layout.addWidget(title)

        group = QGroupBox("服务器连接")
        form = QFormLayout(group)
        form.setSpacing(12)

        self.server_url = QLineEdit("http://127.0.0.1:8765")
        self.server_url.setStyleSheet(f"""
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
        form.addRow("服务器地址:", self.server_url)

        test_btn = QPushButton("测试连接")
        test_btn.setObjectName("btn_secondary")
        test_btn.setFixedHeight(34)
        test_btn.clicked.connect(self._test_connection)
        form.addRow("", test_btn)

        self.conn_status = QLabel("")
        form.addRow("", self.conn_status)

        layout.addWidget(group)

        group2 = QGroupBox("处理选项")
        form2 = QFormLayout(group2)
        form2.setSpacing(12)

        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["auto (自动检测)", "zh (中文)", "en (英文)", "ja (日文)", "ko (韩文)", "fr (法文)"])
        form2.addRow("语言:", self.lang_combo)

        self.service_combo = QComboBox()
        self.service_combo.addItems(["local (本地 Whisper)", "groq (Groq API)", "openai (OpenAI API)"])
        form2.addRow("识别服务:", self.service_combo)

        layout.addWidget(group2)

        group3 = QGroupBox("输出设置")
        form3 = QFormLayout(group3)
        form3.setSpacing(12)

        dir_layout = QHBoxLayout()
        dir_layout.setSpacing(8)
        self.output_dir_input = QLineEdit(str(self.output_dir) if self.output_dir else "未设置")
        self.output_dir_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {THEME["bg_light"]};
                border: 1px solid {THEME["border"]};
                border-radius: 8px;
                padding: 10px 14px;
                font-size: 13px;
            }}
        """)
        dir_layout.addWidget(self.output_dir_input, 1)

        browse_btn = QPushButton("浏览...")
        browse_btn.setObjectName("btn_secondary")
        browse_btn.setFixedHeight(34)
        browse_btn.clicked.connect(self._browse_output)
        dir_layout.addWidget(browse_btn)

        form3.addRow("输出目录:", dir_layout)
        hint = QLabel("字幕生成完成后，视频和 SRT 字幕将自动输出到此目录")
        hint.setStyleSheet(f"font-size: 11px; color: {THEME['text_muted']};")
        form3.addRow("", hint)

        layout.addWidget(group3)
        layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.setStyleSheet(f"""
            QPushButton {{
                padding: 8px 24px;
                border-radius: 8px;
                font-weight: 600;
            }}
        """)
        layout.addWidget(buttons)

    def _browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_dir_input.text())
        if folder:
            self.output_dir_input.setText(folder)

    def accept(self):
        dir_str = self.output_dir_input.text().strip()
        if dir_str:
            p = Path(dir_str)
            try:
                p.mkdir(parents=True, exist_ok=True)
                self.output_dir = p
            except Exception as e:
                QMessageBox.warning(self, "目录无效", f"无法创建输出目录:\n{e}")
                return
        super().accept()

    def _test_connection(self):
        url = self.server_url.text().strip()
        client = WhisperApiClient(url)
        if client.health_check():
            info = client.get_server_info()
            model_info = "✅ 已连接"
            if info and info.get("local_whisper"):
                model_info += " (本地 Whisper 可用)"
            self.conn_status.setText(model_info)
            self.conn_status.setStyleSheet(f"color: {THEME['success']}; font-weight: 600;")
        else:
            self.conn_status.setText("❌ 连接失败，请检查服务器是否启动")
            self.conn_status.setStyleSheet(f"color: {THEME['error']}; font-weight: 600;")
