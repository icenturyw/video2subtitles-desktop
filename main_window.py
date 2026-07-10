import os
import json
import re
import time
import threading
import webbrowser
from pathlib import Path

from PyQt5.QtCore import (
    Qt, QObject, QThread, pyqtSignal, QTimer, QSize, QRectF,
    QPropertyAnimation, QEasingCurve, pyqtProperty,
)
from PyQt5.QtGui import (
    QFont, QColor, QPalette, QIcon, QPixmap, QPainter, QPen,
    QLinearGradient, QBrush, QCursor, QTextCursor, QFontDatabase,
    QKeySequence,
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
    QStyleOptionViewItem, QShortcut, QTableWidget, QTableWidgetItem,
    QHeaderView,
)

from api_client import WhisperApiClient
from local_whisper import LocalWhisperTranscriber, WHISPER_SERVER
from history import HistoryManager
from subtitle_utils import (
    align_keyframe_points_to_scene_changes,
    choose_subtitle_keyframe_points,
    format_subtitle_time,
    parse_srt_file,
    sanitize_filename,
)
from localization_client import LocalizationClient
from client_settings import get_effective_settings, save_settings
from process_utils import hidden_subprocess_kwargs
from output_layout import resolve_existing_layout
from ui.localization_dialog import LocalizationDialog, localization_runtime_config
from ui.runtime_dashboard import RuntimeDashboardDialog
from ui.subtitle_timeline_dialog import SubtitleTimelineDialog
from ui.tts_preview_dialog import TTSPreviewDialog
from ui.task_runtime_dialog import TaskRuntimeDialog
from core.task_state import (
    build_task_display,
    normalize_task_stage,
    normalize_task_status,
    stage_to_ui_text,
    status_to_ui_text,
)


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
        QPushButton#btn_caption_source {{
            background-color: {THEME["bg_light"]};
            color: {THEME["text_primary"]};
            border: 1px solid {THEME["border"]};
            text-align: left;
            padding: 8px 14px;
            font-weight: 700;
        }}
        QPushButton#btn_caption_source:hover {{
            background-color: {THEME["bg_hover"]};
            border-color: {THEME["accent"]};
        }}
        QPushButton#btn_caption_source:checked {{
            background-color: {THEME["accent_dark"]};
            color: white;
            border-color: {THEME["accent_hover"]};
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
        self.stage = ""
        self.progress = 0
        self.message = ""
        self.error_code = ""
        self.error_detail = ""
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

        self.message_label = QLabel("")
        self.message_label.setWordWrap(False)
        self.message_label.setStyleSheet(f"font-size: 11px; color: {THEME['text_secondary']};")
        self.message_label.setVisible(False)
        info_layout.addWidget(self.message_label)

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
        display = build_task_display(
            self.status,
            self.stage,
            self.progress,
            self.message,
            self.error_code,
            self.error_detail,
            is_url=self.is_url,
        )
        self.icon_label.setText(str(display["icon"]))

    def update_status(
        self,
        status,
        progress=0,
        message="",
        stage="",
        error_code="",
        error_detail="",
    ):
        self.status = normalize_task_status(status)
        self.stage = normalize_task_stage(stage, status=status)
        try:
            self.progress = max(0, min(100, int(float(progress))))
        except (TypeError, ValueError):
            self.progress = 0
        if message:
            self.message = str(message)
        if error_code:
            self.error_code = str(error_code)
        elif self.status != "failed":
            self.error_code = ""
        if error_detail:
            self.error_detail = str(error_detail)
        elif self.status != "failed":
            self.error_detail = ""

        display = build_task_display(
            self.status,
            self.stage,
            self.progress,
            self.message,
            self.error_code,
            self.error_detail,
            is_url=self.is_url,
        )
        color = THEME.get(str(display["color_key"]), THEME["text_muted"])
        self.status_label.setText(str(display["title"]))
        self.status_label.setStyleSheet(f"font-size: 11px; color: {color}; font-weight: 600;")

        subtitle = str(display.get("subtitle") or "")
        if subtitle:
            self.message_label.setText(subtitle)
            self.message_label.setToolTip(self.error_detail or self.message or subtitle)
            self.message_label.setVisible(True)
        else:
            self.message_label.clear()
            self.message_label.setVisible(False)

        self.progress_bar.setValue(int(display["progress"]))
        self.pct_label.setText(str(display["progress_text"]))

        self.update_icon()

        if self.status == "completed":
            self.progress_bar.setStyleSheet(f"""
                QProgressBar::chunk {{
                    background-color: {THEME["success"]};
                    border-radius: 4px;
                }}
            """)
        elif self.status == "failed":
            self.progress_bar.setStyleSheet(f"""
                QProgressBar::chunk {{
                    background-color: {THEME["error"]};
                    border-radius: 4px;
                }}
            """)
        elif self.status == "interrupted":
            self.progress_bar.setStyleSheet(f"""
                QProgressBar::chunk {{
                    background-color: {THEME["warning"]};
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
        self._subtitles = None
        self._file_path = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("字幕预览")
        title.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {THEME['text_primary']};")
        header.addWidget(title)
        header.addStretch()

        # Search bar
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索字幕... (Ctrl+F)")
        self.search_input.setFixedWidth(200)
        self.search_input.setVisible(False)
        self.search_input.textChanged.connect(self._filter_table)
        header.addWidget(self.search_input)

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

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["#", "时间", "原文", "译文"])
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 170)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked)
        self.table.cellChanged.connect(self._on_cell_changed)
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {THEME["bg_medium"]};
                border: 1px solid {THEME["border"]};
                border-radius: 12px;
                font-size: 13px;
            }}
            QTableWidget::item {{
                padding: 6px 10px;
                color: {THEME["text_primary"]};
            }}
            QTableWidget::item:alternate {{
                background-color: {THEME["bg_light"]};
            }}
            QHeaderView::section {{
                background-color: {THEME["bg_dark"]};
                color: {THEME["text_secondary"]};
                padding: 8px;
                border: none;
                font-weight: 600;
                font-size: 12px;
            }}
        """)
        self.table.verticalHeader().setVisible(False)
        self.table.setVisible(False)
        layout.addWidget(self.table)

        self.empty_label = QLabel("选择已完成的任务查看字幕...\n双击原文或译文单元格可编辑")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(f"color: {THEME['text_muted']}; font-size: 14px;")
        layout.addWidget(self.empty_label)

    def show_subtitles(self, key, subtitles, is_url=False):
        try:
            self._file_path = key
            self._subtitles = list(subtitles)
            self.open_dir_btn.setVisible(True)
            self.export_btn.setVisible(True)
            self.export_vtt_btn.setVisible(True)
            self.export_txt_btn.setVisible(True)
            self.table.setVisible(True)
            self.empty_label.setVisible(False)
            self.search_input.setVisible(True)

            self._populate_table(self._subtitles)
        except Exception as e:
            self.table.setVisible(False)
            self.empty_label.setText(f"显示字幕时出错: {str(e)[:100]}")
            self.empty_label.setVisible(True)

    def _populate_table(self, subtitles):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.setRowCount(len(subtitles))
        for i, sub in enumerate(subtitles):
            idx_item = QTableWidgetItem(str(i + 1))
            idx_item.setFlags(idx_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 0, idx_item)

            start = sub.get("start", 0)
            end = sub.get("end", 0)
            time_item = QTableWidgetItem(self._format_time(start) + " → " + self._format_time(end))
            time_item.setFlags(time_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 1, time_item)

            text = sub.get("text", "")
            self.table.setItem(i, 2, QTableWidgetItem(text))

            translation = sub.get("translation", "")
            self.table.setItem(i, 3, QTableWidgetItem(translation))
        self.table.blockSignals(False)

    def _filter_table(self):
        text = self.search_input.text().lower()
        for row in range(self.table.rowCount()):
            match = False
            for col in range(1, self.table.columnCount()):
                item = self.table.item(row, col)
                if item and text in item.text().lower():
                    match = True
                    break
            self.table.setRowHidden(row, not match)

    def _on_cell_changed(self, row, col):
        if not self._subtitles or col not in (2, 3):
            return
        item = self.table.item(row, col)
        if item is None:
            return
        new_text = item.text()
        key = "text" if col == 2 else "translation"
        if row < len(self._subtitles):
            self._subtitles[row][key] = new_text

    def clear(self):
        self._subtitles = None
        self._file_path = None
        self.open_dir_btn.setVisible(False)
        self.export_btn.setVisible(False)
        self.export_vtt_btn.setVisible(False)
        self.export_txt_btn.setVisible(False)
        self.table.setVisible(False)
        self.search_input.setVisible(False)
        self.search_input.clear()
        self.empty_label.setText("选择已完成的任务查看字幕...\n双击原文或译文单元格可编辑")
        self.empty_label.setVisible(True)

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
        import subprocess

        cmd = ["yt-dlp", "--print", "title", "--no-playlist", self.url]

        def _run():
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=90,
                    **hidden_subprocess_kwargs(),
                )
                if proc.returncode == 0:
                    raw = (proc.stdout or "").strip()
                    if raw:
                        self.title_ready.emit(self.url, raw[:100])
            except Exception:
                pass

        self._proc = None
        threading.Thread(target=_run, daemon=True).start()

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
        self._skip_event = threading.Event()
        self.client = WhisperApiClient()
        self.local = LocalWhisperTranscriber()
        self._use_api = self.client.health_check()
        self._active_task_ids = set()
        self._task_lock = threading.Lock()

    def _remember_task(self, task_id):
        if not task_id:
            return
        with self._task_lock:
            self._active_task_ids.add(str(task_id))

    def _forget_task(self, task_id):
        if not task_id:
            return
        with self._task_lock:
            self._active_task_ids.discard(str(task_id))

    def _cancel_active_tasks(self):
        with self._task_lock:
            task_ids = list(self._active_task_ids)
        for task_id in task_ids:
            try:
                self.client.cancel_task(task_id)
            except Exception:
                pass

    def run(self):
        try:
            if not self._use_api:
                print("Whisper server not available, using local transcriber")

            if not self.items:
                print("No items to process")
                self.all_done.emit()
                return

            for path_or_url, is_url in self.items:
                if not self._running:
                    break
                if self._cancel_event.is_set() and not self._skip_event.is_set():
                    break
                self._skip_event.clear()
                self._cancel_event.clear()

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
            self._remember_task(task_id)
            if self._cancel_event.is_set():
                self.client.cancel_task(task_id)
                self._forget_task(task_id)
                return

            def on_progress(p, m, s):
                try:
                    status = s if s != "transcribing" else "processing"
                    self.progress_updated.emit(url, int(p), str(m or ""), str(status))
                except Exception:
                    pass

            try:
                task_result = self.client.wait_for_result(
                    task_id,
                    progress_callback=on_progress,
                    cancel_checker=lambda: self._cancel_event.is_set(),
                )
            finally:
                self._forget_task(task_id)

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
                self._remember_task(task_id)
                if self._cancel_event.is_set():
                    self.client.cancel_task(task_id)
                    self._forget_task(task_id)
                    return
                try:
                    task_result = self.client.wait_for_result(
                        task_id,
                        progress_callback=lambda p, m, s: self.progress_updated.emit(
                            file_path, p, m, "processing" if s != "completed" else "completed"
                        ),
                        cancel_checker=lambda: self._cancel_event.is_set(),
                    )
                finally:
                    self._forget_task(task_id)
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

        if self._cancel_event.is_set():
            return
        subtitles, lang = self.local.transcribe(file_path, self.language, on_progress)
        if lang == "cancelled" or self._cancel_event.is_set():
            return
        if lang == "error":
            self.task_error.emit(file_path, subtitles[0] if isinstance(subtitles, list) and subtitles else "转写失败")
        else:
            self.task_completed.emit(file_path, subtitles, lang)

    def stop(self):
        self._running = False
        self._cancel_event.set()
        try:
            self.local.cancel()
        except Exception:
            pass
        threading.Thread(target=self._cancel_active_tasks, daemon=True).start()

    def skip_current(self):
        self._skip_event.set()
        self._cancel_event.set()
        try:
            self.local.cancel()
        except Exception:
            pass
        threading.Thread(target=self._cancel_active_tasks, daemon=True).start()


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

    def _extract_single_frame(self, subprocess, ffmpeg, timestamp, output_path, run_kwargs):
        subprocess.run(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{max(0.0, float(timestamp)):.3f}",
                "-i", str(self.source_video),
                "-frames:v", "1",
                "-vf", "scale=-2:720,format=yuvj420p",
                "-q:v", "4", str(output_path),
            ],
            **run_kwargs,
        )
        return output_path.exists() and output_path.stat().st_size > 0

    def _detect_scene_change_points(self, subprocess, ffmpeg, run_kwargs):
        detect_kwargs = dict(run_kwargs)
        detect_kwargs.update({
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        })
        try:
            completed = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "info",
                    "-i", str(self.source_video),
                    "-an",
                    "-vf", "scale=320:-2,select='gt(scene,0.32)',showinfo",
                    "-f", "null", "-",
                ],
                **detect_kwargs,
            )
        except subprocess.CalledProcessError:
            return []

        output = "\n".join([
            str(completed.stdout or ""),
            str(completed.stderr or ""),
        ])
        points = []
        for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", output):
            timestamp = round(float(match.group(1)), 3)
            if points and timestamp - points[-1] < 0.4:
                continue
            points.append(timestamp)
            if len(points) >= 400:
                break
        return points

    def _extract_subtitle_aligned_frames(self, subprocess, ffmpeg, frames_dir, run_kwargs):
        points = choose_subtitle_keyframe_points(
            parse_srt_file(self.srt_path),
            target_interval=30,
            min_gap=8,
            max_frames=80,
        )
        if not points:
            return [], [], 0

        scene_points = self._detect_scene_change_points(subprocess, ffmpeg, run_kwargs)
        if scene_points:
            points = align_keyframe_points_to_scene_changes(
                points,
                scene_points,
                max_offset=2.0,
                post_scene_offset=0.4,
            )

        frame_records = []
        for index, point in enumerate(points, 1):
            frame_path = frames_dir / f"frame_{index:04d}.jpg"
            try:
                ok = self._extract_single_frame(
                    subprocess,
                    ffmpeg,
                    point.get("timestamp", 0),
                    frame_path,
                    run_kwargs,
                )
            except subprocess.CalledProcessError:
                ok = False
            if not ok:
                continue

            text = str(point.get("subtitle_text", "") or "").replace("\n", " ").strip()
            if len(text) > 240:
                text = text[:237] + "..."
            frame_records.append({
                "file": frame_path.name,
                "timestamp": point.get("timestamp", 0),
                "subtitle_index": point.get("subtitle_index"),
                "subtitle_start": point.get("subtitle_start"),
                "subtitle_end": point.get("subtitle_end"),
                "subtitle_text": text,
                "selection_score": point.get("score"),
            })
            for key in ("original_timestamp", "scene_timestamp", "visual_anchor"):
                if key in point:
                    frame_records[-1][key] = point.get(key)
        return sorted(frames_dir.glob("frame_*.jpg")), frame_records, len(scene_points)

    def _extract_fixed_interval_frames(self, subprocess, ffmpeg, frames_dir, run_kwargs):
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
        records = [
            {
                "file": frame.name,
                "timestamp": round((index - 1) * 30.0, 3),
                "subtitle_index": None,
            }
            for index, frame in enumerate(frames, 1)
        ]
        return frames, records

    def run(self):
        import shutil
        import subprocess

        try:
            run_kwargs = {"check": True}
            run_kwargs = hidden_subprocess_kwargs(run_kwargs)

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
            layout = resolve_existing_layout(self.output_dir, create_dirs=True)
            package_dir = layout.chatgpt_package_dir
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
            frames, frame_records, scene_change_count = self._extract_subtitle_aligned_frames(
                subprocess,
                ffmpeg,
                frames_dir,
                run_kwargs,
            )
            frame_strategy = "subtitle_scene_aligned" if scene_change_count else "subtitle_aligned"
            if not frames:
                frame_strategy = "fixed_interval_fallback"
                scene_change_count = 0
                frames, frame_records = self._extract_fixed_interval_frames(
                    subprocess,
                    ffmpeg,
                    frames_dir,
                    run_kwargs,
                )
            if not frames:
                frame_strategy = "first_frame_fallback"
                scene_change_count = 0
                frame_path = frames_dir / "frame_0001.jpg"
                self._extract_single_frame(subprocess, ffmpeg, 0, frame_path, run_kwargs)
                frames = sorted(frames_dir.glob("frame_*.jpg"))
                frame_records = [
                    {
                        "file": frame.name,
                        "timestamp": 0.0,
                        "subtitle_index": None,
                    }
                    for frame in frames
                ]

            frames_manifest = {
                "strategy": frame_strategy,
                "target_interval_seconds": 30,
                "min_gap_seconds": 8,
                "max_frames": 80,
                "scene_change_count": scene_change_count,
                "scene_threshold": 0.32,
                "scene_alignment_window_seconds": 2.0,
                "frames": frame_records,
            }
            frames_manifest_path = package_dir / "frames_manifest.json"
            frames_manifest_path.write_text(
                json.dumps(frames_manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._emit_progress("正在写入包清单 manifest.json...", 68)
            manifest = {
                "title": self.title,
                "source_video": str(self.source_video),
                "subtitle_file": package_srt.name,
                "proxy_video": proxy_video.name,
                "light_upload_zip": "chatgpt_upload_light.zip",
                "full_upload_zip": "chatgpt_upload_full.zip",
                "frame_strategy": frame_strategy,
                "frame_target_interval_seconds": 30,
                "frame_scene_change_count": scene_change_count,
                "frame_scene_threshold": 0.32,
                "frame_scene_alignment_window_seconds": 2.0,
                "frame_interval_seconds": 30,
                "frames_dir": "frames",
                "frames_manifest": "frames_manifest.json",
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
                zf.write(frames_manifest_path, "frames_manifest.json")
                for frame in frames:
                    zf.write(frame, f"frames/{frame.name}")

            self._emit_progress("正在生成完整上传包...", 92)
            with zipfile.ZipFile(full_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(package_srt, package_srt.name)
                zf.write(proxy_video, proxy_video.name)
                zf.write(package_dir / "manifest.json", "manifest.json")
                zf.write(frames_manifest_path, "frames_manifest.json")
                for frame in frames:
                    zf.write(frame, f"frames/{frame.name}")

            self._emit_progress("ChatGPT 包生成完成", 100)
            self.completed.emit(str(package_dir))
        except subprocess.CalledProcessError as e:
            self.failed.emit(f"ffmpeg 处理失败，退出码: {e.returncode}")
        except Exception as e:
            self.failed.emit(str(e)[:300])


class LocalizationWorker(QThread):
    progress_updated = pyqtSignal(str, int, str, str, str)  # file_path, progress, message, status, stage
    job_started = pyqtSignal(str, str)
    task_completed = pyqtSignal(str, str)
    task_error = pyqtSignal(str, str, str, str, str)  # file_path, error_code, message, error_detail, stage
    preflight_confirmation = pyqtSignal(object)

    def __init__(self, file_path, srt_path, source_video, config, output_dir,
                 retry_job_id="", retry_from_stage=""):
        super().__init__()
        self.file_path = file_path
        self.srt_path = Path(srt_path)
        self.source_video = Path(source_video)
        self.config = config
        self.output_dir = Path(output_dir)
        self._cancelled = False
        self._job_id = str(retry_job_id or "") or None
        self._retry_from_stage = str(retry_from_stage or "")
        self._client = LocalizationClient()

    def stop(self):
        self._cancelled = True
        if self._job_id:
            job_id = self._job_id
            threading.Thread(
                target=lambda: self._client.cancel_job(job_id),
                daemon=True,
            ).start()

    def _emit_error(self, code: str = "", message: str = "", detail: str = "", stage: str = "error"):
        self.task_error.emit(self.file_path, str(code or ""), str(message or ""), str(detail or ""), str(stage or "error"))

    def _resolve_output_srt(self, workspace: Path, final: dict) -> Path | None:
        target_lang = str(self.config.get("target_language", "zh-CN") or "zh-CN")
        artifacts = final.get("artifacts") or []

        def artifact_path(kind: str) -> Path | None:
            for artifact in artifacts:
                if artifact.get("kind") != kind:
                    continue
                path = artifact.get("path") or ""
                candidate = workspace / path
                if candidate.exists():
                    return candidate
            return None

        # The final "_translated.srt" should contain target-language subtitles.
        for kind in ("translated_srt", "bilingual_srt"):
            candidate = artifact_path(kind)
            if candidate:
                return candidate

        subs_dir = workspace / "subtitles"
        fallback_names = [
            f"{target_lang}.srt",
            f"bilingual_{target_lang}.srt",
            f"{target_lang.lower()}.srt",
            f"bilingual_{target_lang.lower()}.srt",
        ]
        for name in fallback_names:
            candidate = subs_dir / name
            if candidate.exists():
                return candidate

        legacy_dir = workspace / "translation"
        legacy_names = [
            f"{self.srt_path.stem}_translated.srt",
            self.srt_path.name,
        ]
        for name in legacy_names:
            candidate = legacy_dir / name
            if candidate.exists():
                return candidate

        for directory in (subs_dir, legacy_dir):
            if directory.exists():
                srt_files = sorted(directory.glob("*.srt"))
                if srt_files:
                    return srt_files[0]

        return None

    def _cleanup_workspace(self, workspace: Path, layout, success: bool):
        """Copy useful logs out of .work and apply the configured cleanup policy."""
        import shutil

        try:
            logs_dir = workspace / "logs"
            if logs_dir.exists():
                layout.logs_dir.mkdir(parents=True, exist_ok=True)
                for item in logs_dir.iterdir():
                    if item.is_file():
                        try:
                            shutil.copy2(str(item), str(layout.logs_dir / item.name))
                        except Exception:
                            pass
        except Exception:
            pass

        if not success:
            return
        try:
            policy = str(get_effective_settings().get("output_cleanup_policy", "tidy") or "tidy").strip().lower()
        except Exception:
            policy = "tidy"
        if policy == "keep_all":
            return
        if policy == "minimal":
            try:
                shutil.rmtree(str(workspace), ignore_errors=True)
            except Exception:
                pass
            return
        # tidy: keep checkpoints/logs/translation/audio roots, remove noisy temp chunks.
        for relative in ("temp", "audio_chunks", "audio/chunks"):
            try:
                shutil.rmtree(str(workspace / relative), ignore_errors=True)
            except Exception:
                pass
        for pattern in ("*.partial", "*.tmp", "*.temp"):
            try:
                for item in workspace.rglob(pattern):
                    if item.is_file():
                        item.unlink(missing_ok=True)
            except Exception:
                pass

    def run(self):
        try:
            self.progress_updated.emit(self.file_path, 0, "准备本地化工作空间...", "running", "prepare")
            if self._cancelled:
                return

            layout = resolve_existing_layout(self.srt_path, create_dirs=True)
            workspace = layout.work_dir
            for d in ["source", "subtitles", "translation", "rendered", "audio", "audio/tts", "checkpoints", "logs", "temp"]:
                (workspace / d).mkdir(parents=True, exist_ok=True)

            if self._job_id and self._retry_from_stage:
                self.progress_updated.emit(self.file_path, 5, "连接本地化引擎...", "running", "prepare")
                if not self._client.health_check():
                    self._emit_error(message="本地化引擎未启动，请先启动服务")
                    return
                if self._cancelled:
                    self._client.cancel_job(self._job_id)
                    return

                self.progress_updated.emit(self.file_path, 10, "提交阶段重试任务...", "running", self._retry_from_stage or "prepare")
                result = self._client.retry_job(self._job_id, self._retry_from_stage)
                if "error" in result:
                    if not self._cancelled:
                        self._emit_error(message=result["error"])
                    return
                self.job_started.emit(self.file_path, self._job_id)
                final = self._client.wait_for_result(
                    self._job_id,
                    progress_callback=lambda p, m, s, st="": self.progress_updated.emit(
                        self.file_path, int(p), str(m or ""), str(s or "running"), str(st or self._retry_from_stage or "")
                    ),
                    poll_interval=1.0,
                    cancel_checker=lambda: self._cancelled,
                )
                self._finish_from_result(workspace, final)
                return

            import shutil
            raw_video = workspace / "source" / self.source_video.name
            try:
                shutil.copy2(str(self.source_video), str(raw_video))
            except Exception as e:
                self._emit_error(message=f"复制视频文件失败: {e}")
                return
            if self._cancelled:
                return

            source_srt_name = self.srt_path.name
            source_sub = workspace / "subtitles" / source_srt_name
            try:
                shutil.copy2(str(self.srt_path), str(source_sub))
            except Exception as e:
                self._emit_error(message=f"复制字幕文件失败: {e}")
                return
            if self._cancelled:
                return

            self.progress_updated.emit(self.file_path, 5, "连接本地化引擎...", "running", "prepare")
            if not self._client.health_check():
                self._emit_error(message="本地化引擎未启动，请先启动服务")
                return
            if self._cancelled:
                return

            self.progress_updated.emit(self.file_path, 8, "释放 Whisper 显存，准备翻译/配音...", "running", "prepare")
            try:
                WhisperApiClient().unload_model()
            except Exception:
                pass

            self.progress_updated.emit(self.file_path, 10, "提交翻译任务...", "running", "translate")
            cfg = self.config
            create_kwargs = dict(
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
                tts_concurrency=cfg.get("tts_concurrency", 1),
                tts_options=cfg.get("tts_options", {}),
                original_volume=cfg.get("original_volume", 0.0),
                low_vram_mode=cfg.get("low_vram_mode", True),
                translation=cfg.get("translation_config"),
                translation_preset_id=cfg.get("translation_preset_id", ""),
                translation_preset_name=cfg.get("translation_preset_name", ""),
                tts_preset_id=cfg.get("tts_preset_id", ""),
                tts_preset_name=cfg.get("tts_preset_name", ""),
            )
            result = self._client.create_job(**create_kwargs)

            if result.get("error_code") == "PREFLIGHT_CONFIRMATION_REQUIRED":
                confirmation = {
                    "event": threading.Event(),
                    "accepted": False,
                    "preflight": result.get("preflight") or {},
                }
                self.preflight_confirmation.emit(confirmation)
                confirmation["event"].wait(300)
                if self._cancelled or not confirmation.get("accepted"):
                    self._emit_error(
                        code="PREFLIGHT_CONFIRMATION_REQUIRED",
                        message="用户未确认 Preflight 警告，任务未启动",
                        stage="prepare",
                    )
                    return
                result = self._client.create_job(
                    **create_kwargs,
                    confirm_preflight_warnings=True,
                )

            if "error" in result:
                if not self._cancelled:
                    self._emit_error(message=result["error"])
                return

            self._job_id = result.get("job_id")
            if not self._job_id:
                if not self._cancelled:
                    self._emit_error(message="引擎未返回任务 ID")
                return
            self.job_started.emit(self.file_path, self._job_id)
            if self._cancelled:
                self._client.cancel_job(self._job_id)
                return

            def on_progress(p, m, s, stage=""):
                self.progress_updated.emit(self.file_path, int(p), str(m or ""), str(s or "running"), str(stage or ""))

            final = self._client.wait_for_result(
                self._job_id,
                progress_callback=on_progress,
                poll_interval=1.0,
                cancel_checker=lambda: self._cancelled,
            )

            self._finish_from_result(workspace, final)

        except Exception as e:
            import traceback
            traceback.print_exc()
            if not self._cancelled:
                self._emit_error(message=str(e)[:200])

    def _finish_from_result(self, workspace: Path, final: dict):
        import shutil

        status = final.get("status")
        if status == "completed":
            output_srt = self._resolve_output_srt(workspace, final)
            if output_srt and output_srt.exists():
                layout = resolve_existing_layout(self.srt_path, create_dirs=True)
                target_lang = str(self.config.get("target_language", "zh-CN") or "zh-CN")
                safe_lang = re.sub(r"[^A-Za-z0-9_.-]", "_", target_lang) or "translated"
                dst = layout.subtitles_dir / f"{self.srt_path.stem}_{safe_lang}_translated.srt"
                shutil.copy2(str(output_srt), str(dst))
                # Promote rendered media artifacts to the user-facing media/ folder.
                for rendered in sorted((workspace / "rendered").glob("*")):
                    if rendered.is_file() and rendered.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}:
                        try:
                            shutil.copy2(str(rendered), str(layout.media_dir / rendered.name))
                        except Exception:
                            pass
                self._cleanup_workspace(workspace, layout, True)
                self.task_completed.emit(self.file_path, str(dst))
            else:
                layout = resolve_existing_layout(self.srt_path, create_dirs=True)
                self._cleanup_workspace(workspace, layout, True)
                self.task_completed.emit(self.file_path, "")
        elif status == "cancelled":
            pass
        else:
            layout = resolve_existing_layout(self.srt_path, create_dirs=True)
            self._cleanup_workspace(workspace, layout, False)
            self.task_error.emit(
                self.file_path,
                final.get("error_code", ""),
                final.get("message", "处理失败"),
                final.get("error_detail", ""),
                final.get("stage", "error"),
            )


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
        self._localization_workers = {}
        self.output_dir = Path(WHISPER_SERVER) / "output" if WHISPER_SERVER.exists() else Path.cwd() / "output"
        self.history = HistoryManager(self.output_dir / "history.json")
        self.history.interrupt_running_tasks()
        self._setup_ui()
        self._refresh_localization_config_from_settings()
        self._check_server()

    def closeEvent(self, event):
        package_running = bool(self.package_worker and self.package_worker.isRunning())
        if package_running:
            QMessageBox.warning(
                self,
                "任务正在运行",
                "ChatGPT 分析包仍在生成中，请等待完成或失败后再关闭窗口。",
            )
            event.ignore()
            return

        active_workers = []
        if self.worker and self.worker.isRunning():
            active_workers.append(self.worker)
        active_workers.extend(
            worker for worker in self._localization_workers.values()
            if worker and worker.isRunning()
        )

        if active_workers:
            reply = QMessageBox.question(
                self,
                "确认退出",
                "当前仍有任务在运行，退出会取消这些任务。确定要关闭窗口吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return

            self._stopped = True
            if self.worker and self.worker.isRunning():
                self.worker.stop()
                self.worker.wait(5000)
            for path, data in self.video_items.items():
                widget = data.get("widget")
                if widget and widget.status in ("queued", "running", "pending"):
                    self.history.cancel_task(path, "应用关闭，任务已取消", progress=widget.progress)
            for worker in list(self._localization_workers.values()):
                if worker and worker.isRunning():
                    worker.stop()
                    worker.wait(5000)
            self._localization_workers.clear()
            self._localization_worker = None

        super().closeEvent(event)

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
        self._create_content(main_layout)
        self._create_statusbar()

        # -- Keyboard shortcuts -------------------------------------------
        QShortcut(QKeySequence("Ctrl+O"), self, self._add_files)
        QShortcut(QKeySequence("Ctrl+Shift+O"), self, self._add_folder)
        QShortcut(QKeySequence("Delete"), self, self._remove_selected)
        QShortcut(QKeySequence("Ctrl+Return"), self, self._start_processing)
        QShortcut(QKeySequence("Escape"), self, self._stop_processing)
        QShortcut(QKeySequence("Ctrl+E"), self, self._export_selected_srt)
        QShortcut(QKeySequence("Ctrl+H"), self, self._show_history)
        QShortcut(QKeySequence("Ctrl+L"), self, self._open_localization_dialog)
        QShortcut(QKeySequence("Ctrl+F"), self, self._focus_subtitle_search)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, self._skip_current)

    def _create_toolbar(self, parent_layout):
        toolbar = QWidget()
        toolbar.setStyleSheet(f"""
            QWidget {{
                background-color: {THEME["bg_medium"]};
                border-bottom: 1px solid {THEME["border"]};
            }}
        """)
        toolbar.setFixedHeight(68)
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(12)

        logo = QLabel("🎬")
        logo.setStyleSheet("font-size: 26px;")
        layout.addWidget(logo)

        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        title = QLabel("Video2Subtitles")
        title.setStyleSheet(f"font-size: 18px; font-weight: 800; color: {THEME['text_primary']}; letter-spacing: 0.5px;")
        title_block.addWidget(title)
        subtitle = QLabel("把视频变成字幕、翻译和可交付文件")
        subtitle.setStyleSheet(f"font-size: 12px; color: {THEME['text_muted']};")
        title_block.addWidget(subtitle)
        layout.addLayout(title_block)

        layout.addStretch()

        self.server_status = QLabel()
        self.server_status.setStyleSheet(f"font-size: 12px; color: {THEME['text_muted']}; padding: 4px 12px;")
        self.server_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.server_status)

        self.output_dir_btn = QPushButton("📁 输出目录")
        self.output_dir_btn.setObjectName("btn_secondary")
        self.output_dir_btn.setFixedHeight(36)
        self.output_dir_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.output_dir_btn.clicked.connect(self._change_output_dir)
        self.output_dir_btn.setToolTip(str(self.output_dir))
        layout.addWidget(self.output_dir_btn)

        self.localize_btn = QPushButton("🌐 翻译 / 配音")
        self.localize_btn.setObjectName("btn_secondary")
        self.localize_btn.setFixedHeight(36)
        self.localize_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.localize_btn.clicked.connect(self._show_localization_dialog)
        layout.addWidget(self.localize_btn)

        runtime_btn = QPushButton("资源")
        runtime_btn.setObjectName("btn_secondary")
        runtime_btn.setFixedHeight(36)
        runtime_btn.setCursor(QCursor(Qt.PointingHandCursor))
        runtime_btn.clicked.connect(self._show_runtime_dashboard)
        layout.addWidget(runtime_btn)

        preview_btn = QPushButton("语音试听")
        preview_btn.setObjectName("btn_secondary")
        preview_btn.setFixedHeight(36)
        preview_btn.setCursor(QCursor(Qt.PointingHandCursor))
        preview_btn.clicked.connect(self._show_tts_preview)
        layout.addWidget(preview_btn)

        settings_btn = QPushButton("⚙")
        settings_btn.setObjectName("btn_icon")
        settings_btn.setFixedSize(36, 36)
        settings_btn.setCursor(QCursor(Qt.PointingHandCursor))
        settings_btn.setToolTip("设置")
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
        left_layout.setContentsMargins(24, 18, 12, 18)
        left_layout.setSpacing(14)

        hero_card = CardFrame()
        hero_layout = hero_card.layout()
        hero_layout.setContentsMargins(18, 16, 18, 16)
        hero_layout.setSpacing(8)
        hero_title = QLabel("今天要处理哪个视频？")
        hero_title.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {THEME['text_primary']};")
        hero_layout.addWidget(hero_title)
        hero_hint = QLabel("先添加本地文件、目录或在线视频链接，再点击下方的大按钮开始生成字幕。")
        hero_hint.setWordWrap(True)
        hero_hint.setStyleSheet(f"font-size: 12px; color: {THEME['text_secondary']}; line-height: 1.5;")
        hero_layout.addWidget(hero_hint)
        left_layout.addWidget(hero_card)

        input_card = CardFrame()
        input_layout = input_card.layout()
        input_layout.setContentsMargins(18, 16, 18, 16)
        input_layout.setSpacing(12)
        step1 = QLabel("① 添加视频")
        step1.setStyleSheet(f"font-size: 14px; font-weight: 800; color: {THEME['accent']};")
        input_layout.addWidget(step1)

        file_buttons = QHBoxLayout()
        file_buttons.setSpacing(10)
        self.add_files_btn = QPushButton("📁 选择视频文件")
        self.add_files_btn.setObjectName("btn_secondary")
        self.add_files_btn.setFixedHeight(40)
        self.add_files_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.add_files_btn.clicked.connect(self._add_files)
        file_buttons.addWidget(self.add_files_btn)

        self.add_folder_btn = QPushButton("📂 批量添加目录")
        self.add_folder_btn.setObjectName("btn_secondary")
        self.add_folder_btn.setFixedHeight(40)
        self.add_folder_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.add_folder_btn.clicked.connect(self._add_folder)
        file_buttons.addWidget(self.add_folder_btn)
        input_layout.addLayout(file_buttons)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("粘贴 YouTube / Bilibili / 抖音等视频链接，回车添加")
        self.url_input.setMinimumHeight(40)
        self.url_input.returnPressed.connect(self._add_url)
        url_row.addWidget(self.url_input, 1)

        self.add_url_btn = QPushButton("添加链接")
        self.add_url_btn.setFixedHeight(40)
        self.add_url_btn.setFixedWidth(100)
        self.add_url_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.add_url_btn.clicked.connect(self._add_url)
        url_row.addWidget(self.add_url_btn)
        input_layout.addLayout(url_row)
        left_layout.addWidget(input_card)

        action_card = CardFrame()
        action_layout = action_card.layout()
        action_layout.setContentsMargins(18, 16, 18, 16)
        action_layout.setSpacing(12)
        step2 = QLabel("② 开始处理")
        step2.setStyleSheet(f"font-size: 14px; font-weight: 800; color: {THEME['accent']};")
        action_layout.addWidget(step2)

        self.source_subtitles_btn = QPushButton()
        self.source_subtitles_btn.setObjectName("btn_caption_source")
        self.source_subtitles_btn.setCheckable(True)
        self.source_subtitles_btn.setFixedHeight(38)
        self.source_subtitles_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.source_subtitles_btn.toggled.connect(self._on_source_subtitles_toggled)
        self._sync_source_subtitles_button_from_settings()
        action_layout.addWidget(self.source_subtitles_btn)

        self.start_btn = QPushButton("▶ 开始生成字幕")
        self.start_btn.setFixedHeight(46)
        self.start_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.start_btn.clicked.connect(lambda: self._start_processing())
        self.start_btn.setEnabled(False)
        action_layout.addWidget(self.start_btn)

        secondary_actions = QHBoxLayout()
        secondary_actions.setSpacing(8)
        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setObjectName("btn_danger")
        self.stop_btn.setFixedHeight(36)
        self.stop_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.stop_btn.clicked.connect(self._stop_processing)
        self.stop_btn.setEnabled(False)
        secondary_actions.addWidget(self.stop_btn)

        self.skip_btn = QPushButton("⏭ 跳过")
        self.skip_btn.setObjectName("btn_secondary")
        self.skip_btn.setFixedHeight(36)
        self.skip_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.skip_btn.clicked.connect(self._skip_current)
        self.skip_btn.setEnabled(False)
        secondary_actions.addWidget(self.skip_btn)

        self.retry_all_btn = QPushButton("🔄 重试失败")
        self.retry_all_btn.setObjectName("btn_secondary")
        self.retry_all_btn.setFixedHeight(36)
        self.retry_all_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.retry_all_btn.clicked.connect(self._retry_failed)
        self.retry_all_btn.setEnabled(False)
        secondary_actions.addWidget(self.retry_all_btn)

        self.clear_btn = QPushButton("🧹 清空列表")
        self.clear_btn.setObjectName("btn_secondary")
        self.clear_btn.setFixedHeight(36)
        self.clear_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.clear_btn.clicked.connect(self._clear_all)
        self.clear_btn.setEnabled(False)
        secondary_actions.addWidget(self.clear_btn)
        action_layout.addLayout(secondary_actions)
        left_layout.addWidget(action_card)

        list_header = QHBoxLayout()
        list_title = QLabel("③ 任务列表")
        list_title.setStyleSheet(f"font-size: 16px; font-weight: 800; color: {THEME['text_primary']};")
        list_header.addWidget(list_title)

        self.count_label = QLabel("0 个视频")
        self.count_label.setStyleSheet(f"font-size: 12px; color: {THEME['text_muted']}; padding-top: 4px;")
        list_header.addWidget(self.count_label)
        list_header.addStretch()

        self.history_btn = QPushButton("历史记录")
        self.history_btn.setObjectName("btn_secondary")
        self.history_btn.setFixedHeight(32)
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
        self.file_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.file_list.dragEnterEvent = self._drag_enter_event
        self.file_list.dragMoveEvent = self._drag_move_event
        self.file_list.dropEvent = self._drop_event
        self.file_list.setMinimumHeight(220)
        left_layout.addWidget(self.file_list, 1)

        splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 18, 24, 18)
        right_layout.setSpacing(0)

        self.subtitle_viewer = SubtitleViewer()
        right_layout.addWidget(self.subtitle_viewer)

        splitter.addWidget(right_panel)
        splitter.setSizes([520, 720])

        parent_layout.addWidget(splitter, 1)

    def _create_statusbar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("就绪 - 添加视频后点击「开始生成字幕」")
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
                self.video_items[p]["localization_job_id"] = entry.get("job_id", "")
                saved_status = normalize_task_status(entry.get("status", ""))
                saved_stage = normalize_task_stage(entry.get("stage", ""), status=entry.get("status", ""))
                saved_progress = entry.get("progress", 0)
                saved_message = entry.get("message", "")
                subs = self.history.get_subtitles(p)
                if subs:
                    widget.subtitles = subs
                out_dir = entry.get("output_dir") or ""
                if saved_status == "failed":
                    widget.update_status(
                        "failed", saved_progress or 0, saved_message or "上次处理出错",
                        saved_stage, entry.get("error_code", ""), entry.get("error_detail", "")
                    )
                elif saved_status == "cancelled":
                    widget.update_status("cancelled", saved_progress or 0, saved_message or "上次已取消", saved_stage)
                elif saved_status in ("pending", "queued", "running", "interrupted"):
                    widget.update_status("interrupted", saved_progress or 0, saved_message or "上次任务未正常结束，可重新处理", saved_stage)
                elif saved_status == "completed" and subs:
                    msg = f"已有字幕 ({entry.get('language', '?')})"
                    if out_dir:
                        msg += f" · {Path(out_dir).name}"
                    widget.update_status("completed", 100, msg, "completed")
                elif saved_status == "completed":
                    srt_path = entry.get("srt_path") if entry else ""
                    widget.update_status("completed", 100, f"历史记录 ({Path(srt_path).name})", "completed")
                else:
                    widget.update_status(saved_status, saved_progress or 0, saved_message, saved_stage)
            added += 1

        if added > 0:
            self._update_counts()
            self.start_btn.setEnabled(len(self.video_items) > 0)
            self.clear_btn.setEnabled(len(self.video_items) > 0)
            self.status_label.setText(f"已添加 {added} 个视频（含历史记录），共 {len(self.video_items)} 个")

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
        self.status_label.setText("就绪 - 添加视频后点击「开始生成字幕」")

    def _sync_source_subtitles_button_from_settings(self):
        """Refresh the visible source-subtitle toggle from persisted settings."""
        btn = getattr(self, "source_subtitles_btn", None)
        if not btn:
            return
        try:
            settings = get_effective_settings()
            policy = str(settings.get("youtube_caption_policy", "auto") or "auto").strip().lower()
        except Exception:
            policy = "auto"
        checked = policy != "whisper"
        old_block = btn.blockSignals(True)
        try:
            btn.setChecked(checked)
            self._update_source_subtitles_button_ui(checked, policy)
        finally:
            btn.blockSignals(old_block)

    def _update_source_subtitles_button_ui(self, checked, policy=None):
        btn = getattr(self, "source_subtitles_btn", None)
        if not btn:
            return
        policy = str(policy or ("auto" if checked else "whisper")).strip().lower()
        if checked:
            if policy == "youtube":
                btn.setText("🎞 源字幕：强制使用")
                btn.setToolTip("在线链接任务会强制使用源视频/YouTube 字幕；如果源字幕质量差，也不会自动回退。")
            else:
                btn.setText("🎞 源字幕：优先使用")
                btn.setToolTip("在线链接任务会优先尝试源视频/YouTube 字幕，并自动修复断词和重分段；质量差时回退本地 Whisper。")
        else:
            btn.setText("🎙 源字幕：关闭，本地识别")
            btn.setToolTip("在线链接任务会跳过源视频/YouTube 字幕，强制下载音视频后用本地 Whisper 重新识别。")

    def _on_source_subtitles_toggled(self, checked):
        policy = "auto" if checked else "whisper"
        try:
            save_settings({
                "youtube_caption_policy": policy,
                "youtube_caption_resegment": "true" if checked else get_effective_settings().get("youtube_caption_resegment", "true"),
            })
        except Exception as exc:
            print(f"save source subtitle policy failed: {exc}")
        self._update_source_subtitles_button_ui(checked, policy)
        if hasattr(self, "status_label"):
            if checked:
                self.status_label.setText("已开启源字幕优先：会自动修复分段，质量差则回退本地 Whisper")
            else:
                self.status_label.setText("已关闭源字幕：在线链接将强制使用本地 Whisper 重新识别")

    def _retry_failed(self):
        failed = []
        for path, data in self.video_items.items():
            widget = data["widget"]
            if widget.status in ("error", "failed"):
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
                           if d["widget"].status in ("pending", "error", "failed", "interrupted")]
                if not pending:
                    QMessageBox.information(self, "提示", "没有待处理的任务")
                    return
                items = pending
            elif not isinstance(specific_files, (list, tuple)):
                items = [(p, d.get("is_url", False)) for p, d in self.video_items.items()
                         if d["widget"].status in ("pending", "error", "failed", "interrupted")]
                if not items:
                    return
            else:
                items = [(p, self.video_items[p].get("is_url", False)) for p in specific_files if p in self.video_items]

            if not items:
                return

            language = "auto"
            if hasattr(self, "lang_combo"):
                language_text = self.lang_combo.currentText().strip()
                language = language_text.split("(", 1)[0].strip() or "auto"

            for p, is_url in items:
                if p in self.video_items:
                    widget = self.video_items[p]["widget"]
                    widget.update_status("queued", 0, "等待处理", "prepare")
                    title = self.video_items[p].get("title") or getattr(widget, "title", None) or (p if is_url else Path(p).name)
                    self.history.start_task(
                        p,
                        title=title,
                        source_type="url" if is_url else "local",
                        mode="subtitle",
                        source=p,
                        is_url=is_url,
                        language=language,
                        stage="prepare",
                        message="等待处理",
                    )

            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.skip_btn.setEnabled(True)
            self.add_files_btn.setEnabled(False)
            self.add_folder_btn.setEnabled(False)
            self.add_url_btn.setEnabled(False)
            self.url_input.setEnabled(False)
            if hasattr(self, "source_subtitles_btn"):
                self.source_subtitles_btn.setEnabled(False)

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
            self.add_url_btn.setEnabled(True)
            self.url_input.setEnabled(True)
            if hasattr(self, "source_subtitles_btn"):
                self.source_subtitles_btn.setEnabled(True)

    def _stop_processing(self):
        self._stopped = True
        if self.worker:
            self.worker.stop()
            self.worker.wait(2000)
        for worker in list(self._localization_workers.values()):
            worker.stop()
            worker.wait(2000)
        self._localization_workers.clear()
        self._localization_worker = None
        for path, data in self.video_items.items():
            widget = data["widget"]
            if widget.status in ("queued", "downloading", "processing", "running", "pending"):
                widget.update_status("cancelled", widget.progress, "已取消", "cancelled")
                self.history.cancel_task(path, "已取消", progress=widget.progress)
        self.stop_btn.setEnabled(False)
        self.skip_btn.setEnabled(False)
        self._on_all_done()

    def _skip_current(self):
        if self.worker and self.worker.isRunning():
            self.worker.skip_current()

    def _on_progress(self, file_path, progress, message, status, stage=""):
        try:
            if self._stopped:
                return
            if file_path in self.video_items:
                self.video_items[file_path]["widget"].update_status(status, progress, message, stage)
                self._save_progress_to_history(file_path, progress, status, message, stage)
        except Exception as e:
            print(f"_on_progress error: {e}")

    def _on_completed(self, file_path, subtitles, language):
        try:
            if self._stopped:
                return
            if file_path in self.video_items:
                widget = self.video_items[file_path]["widget"]
                msg = f"完成 ({language})"
                widget.update_status("completed", 100, msg, "completed")
                widget.subtitles = subtitles

                srt_path = self._save_output(file_path, subtitles, widget.is_url, language)
                if srt_path:
                    out_dir_name = srt_path.parent.name
                    msg = f"完成 ({language}) · {out_dir_name}/"
                    widget.update_status("completed", 100, msg, "completed")
                    self.history.complete_task(
                        file_path,
                        message=msg,
                        language=language,
                        subtitle_count=len(subtitles) if subtitles else 0,
                        srt_path=str(srt_path),
                        output_dir=str(srt_path.parent),
                    )
                else:
                    self._save_progress_to_history(file_path, 100, "completed", msg, "completed")

                idx = self.file_list.row(self.video_items[file_path]["item"])
                if idx == self.file_list.currentRow():
                    self.subtitle_viewer.show_subtitles(file_path, subtitles, widget.is_url)

                if self._localization_config and self._localization_config.get("is_translate_mode"):
                    widget.update_status("running", 50, "开始翻译字幕...", "translate")
                    self._save_progress_to_history(file_path, 50, "running", "开始翻译字幕...", "translate")
                    self._start_localization(file_path, str(srt_path))

            self._update_progress()
        except Exception as e:
            print(f"_on_completed error: {e}")

    def _on_error(self, file_path, error_msg):
        try:
            if self._stopped:
                return
            if file_path and file_path in self.video_items:
                self.video_items[file_path]["widget"].update_status("failed", 0, error_msg[:160], "error", "", str(error_msg))
                self.history.fail_task(file_path, error_detail=str(error_msg), message=str(error_msg)[:500], stage="error", progress=0)
            elif not file_path:
                QMessageBox.critical(self, "错误", error_msg)
            self._update_progress()
        except Exception as e:
            print(f"Error in _on_error: {e}")

    def _save_progress_to_history(self, file_path, progress, status, message, stage=""):
        try:
            if not self.history.exists(file_path):
                is_url = self.video_items.get(file_path, {}).get("is_url", False) if hasattr(self, "video_items") else False
                title = self.video_items.get(file_path, {}).get("title", "") if hasattr(self, "video_items") else ""
                if not title:
                    title = file_path if is_url else Path(file_path).name
                self.history.start_task(
                    file_path,
                    title=title,
                    source_type="url" if is_url else "local",
                    mode="subtitle",
                    source=file_path,
                    is_url=is_url,
                )
            normalized = normalize_task_status(status)
            normalized_stage = normalize_task_stage(stage, status=status)
            if normalized == "completed":
                self.history.complete_task(file_path, message=str(message or "任务已完成"), stage=normalized_stage or "completed", progress=progress)
            elif normalized == "failed":
                self.history.fail_task(file_path, error_detail=str(message or ""), message=str(message or "")[:500], stage=normalized_stage or "error", progress=progress)
            elif normalized == "cancelled":
                self.history.cancel_task(file_path, str(message or "任务已取消"), progress=progress, stage=normalized_stage or "cancelled")
            else:
                self.history.update_task_progress(
                    file_path,
                    normalized,
                    normalized_stage,
                    progress,
                    str(message or ""),
                )
        except Exception:
            pass

    def _on_all_done(self):
        try:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.add_files_btn.setEnabled(True)
            self.add_folder_btn.setEnabled(True)
            self.add_url_btn.setEnabled(True)
            self.url_input.setEnabled(True)
            if hasattr(self, "source_subtitles_btn"):
                self.source_subtitles_btn.setEnabled(True)

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
        match = re.search(r'(?:v=|\/videos\/|embed\/|youtu.be\/|\/v\/|\/e\/|watch\?v=|&v=)([^#&\n/?]*)', url)
        if match:
            return re.sub(r"[^A-Za-z0-9_.-]", "_", match.group(1))[:80]
        return hashlib.md5(url.encode("utf-8")).hexdigest()[:11]

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

            # Fetch title asynchronously without flashing a helper console window.
            fetcher = TitleFetcher(url, self)
            fetcher.title_ready.connect(self._on_title_fetched)
            if not hasattr(self, '_fetchers'):
                self._fetchers = {}
            self._fetchers[url] = fetcher
            fetcher.start()

            entry = self.history.get(url)
            if entry:
                self.video_items[url]["localization_job_id"] = entry.get("job_id", "")
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
        if widget.status in ("queued", "downloading", "processing", "running"):
            stop_action = QAction("停止任务", self)
            stop_action.triggered.connect(lambda: self._stop_task(key))
            menu.addAction(stop_action)
            menu.addSeparator()
        menu.addAction(remove_action)

        if widget.status in ("error", "failed", "cancelled", "interrupted"):
            label = "🔄 重新处理" if widget.status in ("cancelled", "interrupted") else "🔄 重试"
            retry_action = QAction(label, self)
            retry_action.triggered.connect(lambda: self._start_processing(specific_files=[key]))
            menu.addAction(retry_action)

        if widget.status in ("completed", "error", "failed", "cancelled", "interrupted") and self._get_localization_job_id(key):
            runtime_detail = QAction("查看任务运行详情", self)
            runtime_detail.triggered.connect(
                lambda: TaskRuntimeDialog(self._get_localization_job_id(key), self, LocalizationClient()).exec_()
            )
            menu.addAction(runtime_detail)
            edit_subtitles = QAction("编辑字幕时间轴", self)
            edit_subtitles.triggered.connect(lambda: self._open_subtitle_timeline(key))
            menu.addAction(edit_subtitles)
            stage_menu = QMenu("重新生成指定阶段", self)
            stages = [
                ("翻译及后续", "translate"),
                ("字幕导出及后续", "subtitle_export"),
                ("语音合成及后续", "tts"),
                ("音频混合及后续", "audio_mix"),
                ("视频渲染", "render"),
            ]
            for label, stage in stages:
                action = QAction(label, self)
                action.triggered.connect(lambda checked=False, st=stage: self._retry_localization_stage(key, st))
                stage_menu.addAction(action)
            stage_menu.addSeparator()
            full_action = QAction("全部重新处理", self)
            full_action.triggered.connect(lambda: self._start_processing(specific_files=[key]))
            stage_menu.addAction(full_action)
            menu.addMenu(stage_menu)

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

    def _get_localization_job_id(self, key):
        data = self.video_items.get(key) or {}
        job_id = data.get("localization_job_id") or self.history.get_job_id(key)
        return str(job_id or "").strip()

    def _show_runtime_dashboard(self):
        RuntimeDashboardDialog(self, LocalizationClient()).exec_()

    def _show_tts_preview(self):
        TTSPreviewDialog(self, LocalizationClient()).exec_()

    def _open_subtitle_timeline(self, key):
        job_id = self._get_localization_job_id(key)
        if not job_id:
            QMessageBox.warning(self, "字幕编辑器", "该项目没有可用的本地化任务 ID。")
            return
        source_video = key
        if self.video_items.get(key, {}).get("is_url"):
            output_dir = self.history.get_output_dir(key)
            candidate = self._find_output_video(key, output_dir) if output_dir else None
            source_video = str(candidate) if candidate else ""
        SubtitleTimelineDialog(
            job_id,
            source_video if Path(str(source_video)).is_file() else "",
            self,
            LocalizationClient(),
        ).exec_()

    def _get_history_srt_path(self, key):
        entry = self.history.get(key) or {}
        srt_path = entry.get("srt_path") or ""
        return srt_path if srt_path and Path(srt_path).exists() else ""

    def _retry_localization_stage(self, key, stage):
        if key not in self.video_items:
            return
        job_id = self._get_localization_job_id(key)
        srt_path = self._get_history_srt_path(key)
        if not job_id or not srt_path:
            QMessageBox.warning(self, "无法重新生成", "缺少本地化任务 ID 或字幕历史记录，请使用“全部重新处理”。")
            return
        if self._localization_workers.get(key):
            QMessageBox.information(self, "任务运行中", "该任务正在运行，请先停止后再重新生成。")
            return

        self._stopped = False
        self._refresh_localization_config_from_settings()
        source_video = key
        if self.video_items.get(key, {}).get("is_url"):
            output_dir = self.history.get_output_dir(key)
            source_video = str(self._find_output_video(key, output_dir)) if output_dir else ""
        worker = LocalizationWorker(
            key,
            srt_path,
            source_video or key,
            self._localization_config or {},
            self.output_dir,
            retry_job_id=job_id,
            retry_from_stage=stage,
        )
        self.video_items[key]["widget"].update_status("running", 0, f"重新生成: {stage_to_ui_text(stage) or stage}", stage)
        self.history.update_task_progress(key, "running", stage, 0, f"重新生成: {stage_to_ui_text(stage) or stage}", mode="localization")
        self.stop_btn.setEnabled(True)
        self.skip_btn.setEnabled(True)
        self._attach_localization_worker(key, worker)
        self.status_label.setText(f"正在从 {stage} 重新生成...")

    def _stop_task(self, key):
        if key not in self.video_items:
            return
        worker = self._localization_workers.get(key)
        if worker:
            worker.stop()
        else:
            job_id = self._get_localization_job_id(key)
            if job_id:
                threading.Thread(
                    target=lambda: LocalizationClient().cancel_job(job_id),
                    daemon=True,
                ).start()
            elif self.worker and self.worker.isRunning():
                self.worker.stop()
        widget = self.video_items[key]["widget"]
        widget.update_status("cancelled", widget.progress, "已请求停止", "cancelled")
        self.history.cancel_task(key, "已请求停止", progress=widget.progress)
        self._update_progress()
        self.status_label.setText("已请求停止任务")

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
            self.video_items[key]["widget"].update_status("pending", 0, "已删除历史记录", "prepare")
        self.status_label.setText("输出和记录已删除")

    def _remove_selected(self):
        item = self.file_list.currentItem()
        if item:
            key = str(item.data(Qt.UserRole) or "")
            if key in self.video_items:
                self._remove_file(key)

    def _export_selected_srt(self):
        item = self.file_list.currentItem()
        if item:
            key = str(item.data(Qt.UserRole) or "")
            if key in self.video_items:
                self._export_single(key, "srt")

    def _open_localization_dialog(self):
        self._show_localization_dialog()

    def _focus_subtitle_search(self):
        if self.subtitle_viewer.search_input.isVisible():
            self.subtitle_viewer.search_input.setFocus()
            self.subtitle_viewer.search_input.selectAll()

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
        self.count_label.setText(f"{len(self.video_items)} 个视频")

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
        dialog.setMinimumSize(860, 520)
        dialog.resize(980, 620)
        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {THEME["bg_dark"]}; }}
            QTableWidget {{
                background-color: {THEME["bg_medium"]};
                border: 1px solid {THEME["border"]};
                border-radius: 8px;
                gridline-color: {THEME["border"]};
                color: {THEME["text_primary"]};
                selection-background-color: {THEME["accent_dark"]};
            }}
            QHeaderView::section {{
                background-color: {THEME["bg_light"]};
                color: {THEME["text_primary"]};
                border: none;
                padding: 7px 8px;
                font-weight: 600;
            }}
        """)
        dialog.setUpdatesEnabled(False)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        normalized_entries = []
        counts = {"completed": 0, "failed": 0, "running": 0, "interrupted": 0, "cancelled": 0, "queued": 0, "pending": 0, "cached": 0}
        for key, entry in entries.items():
            status = normalize_task_status(entry.get("status", ""))
            if status in counts:
                counts[status] += 1
            normalized_entries.append((key, entry, status))
        running_total = counts["running"] + counts["queued"] + counts["pending"]
        summary = (
            f"历史记录（共 {len(entries)} 条）  "
            f"✅ 已完成 {counts['completed']}  "
            f"🔄 运行/排队 {running_total}  "
            f"❌ 失败 {counts['failed']}  "
            f"⚠️ 中断 {counts['interrupted']}"
        )
        title = QLabel(summary)
        title.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {THEME['text_primary']}; padding-bottom: 4px;")
        layout.addWidget(title)

        filter_layout = QHBoxLayout()
        history_search = QLineEdit()
        history_search.setPlaceholderText("搜索标题、来源、消息或错误码")
        history_search.setClearButtonEnabled(True)
        history_status = QComboBox()
        history_status.addItem("全部状态", "")
        for status_value, status_label in (
            ("completed", "已完成"), ("running", "运行中"),
            ("failed", "失败"), ("interrupted", "已中断"),
            ("cancelled", "已取消"), ("pending", "等待中"),
        ):
            history_status.addItem(status_label, status_value)
        history_page = QSpinBox()
        history_page.setMinimum(1)
        history_page.setMaximum(1)
        history_page.setPrefix("第 ")
        history_page.setSuffix(" 页")
        history_page_label = QLabel()
        history_page_label.setStyleSheet(f"color: {THEME['text_secondary']};")
        filter_layout.addWidget(history_search, 1)
        filter_layout.addWidget(history_status)
        filter_layout.addWidget(history_page)
        filter_layout.addWidget(history_page_label)
        layout.addLayout(filter_layout)

        table = QTableWidget()
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels(["状态", "标题/来源", "模式", "阶段", "进度", "更新时间", "输出/错误"])
        table.setRowCount(len(normalized_entries))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(False)
        table.setSortingEnabled(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)

        sorted_entries = sorted(
            normalized_entries,
            key=lambda x: x[1].get("updated_at") or x[1].get("timestamp", 0),
            reverse=True,
        )
        row_payloads = []
        for row, (key, entry, status) in enumerate(sorted_entries):
            stage = normalize_task_stage(entry.get("stage", ""), status=entry.get("status", ""))
            progress = int(entry.get("progress", 100 if status == "completed" else 0) or 0)
            display = build_task_display(
                status,
                stage,
                progress,
                entry.get("message", ""),
                entry.get("error_code", ""),
                entry.get("error_detail", ""),
                is_url=entry.get("is_url", False),
            )
            is_url = entry.get("is_url", False)
            name = entry.get("title") or (Path(key).name if not is_url else key)
            mode_raw = str(entry.get("mode", "subtitle") or "subtitle")
            mode_map = {
                "subtitle": "字幕",
                "localization": "翻译/配音",
                "translate": "翻译",
                "dub": "配音",
            }
            mode_text = mode_map.get(mode_raw, mode_raw)
            updated = entry.get("updated_at") or entry.get("timestamp", 0)
            updated_text = time.strftime("%m-%d %H:%M", time.localtime(updated)) if updated else "-"
            out_dir = entry.get("output_dir") or ""
            error_text = ""
            if status == "failed":
                code = str(entry.get("error_code", "") or "").strip()
                msg = str(entry.get("message", "") or entry.get("error_detail", "") or "处理失败")
                error_text = f"[{code}] {msg}" if code else msg
            else:
                error_text = Path(out_dir).name if out_dir else str(entry.get("srt_path", "") or "")

            values = [
                f"{display['icon']} {display['status_text']}",
                str(name)[:160],
                mode_text,
                str(display.get("stage_text") or "-"),
                str(display.get("progress_text") or f"{progress}%"),
                updated_text,
                str(error_text)[:220],
            ]
            payload = {
                "key": key,
                "entry": entry,
                "out_dir": out_dir if out_dir and Path(out_dir).exists() else "",
                "error_text": error_text,
            }
            row_payloads.append(payload)
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(str(error_text if col == 6 else value))
                if col == 0:
                    color = THEME.get(str(display["color_key"]), THEME["text_muted"])
                    cell.setForeground(QBrush(QColor(color)))
                cell.setData(Qt.UserRole, payload)
                table.setItem(row, col, cell)
        table.resizeRowsToContents()
        layout.addWidget(table, 1)

        history_page_size = 50

        def apply_history_filter(*_args):
            keyword = history_search.text().strip().casefold()
            selected_status = str(history_status.currentData() or "")
            matches = []
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                payload = item.data(Qt.UserRole) if item else {}
                entry = payload.get("entry") or {}
                status = normalize_task_status(entry.get("status", ""))
                haystack = " ".join((
                    str(payload.get("key", "")), str(entry.get("title", "")),
                    str(entry.get("source", "")), str(entry.get("message", "")),
                    str(entry.get("error_code", "")), str(entry.get("error_detail", "")),
                )).casefold()
                if selected_status and status != selected_status:
                    continue
                if keyword and keyword not in haystack:
                    continue
                matches.append(row)
            pages = max(1, (len(matches) + history_page_size - 1) // history_page_size)
            history_page.blockSignals(True)
            history_page.setMaximum(pages)
            if history_page.value() > pages:
                history_page.setValue(pages)
            history_page.blockSignals(False)
            page = history_page.value()
            visible = set(matches[(page - 1) * history_page_size:page * history_page_size])
            for row in range(table.rowCount()):
                table.setRowHidden(row, row not in visible)
            history_page_label.setText(f"{len(matches)} 条 / {pages} 页")

        history_search.textChanged.connect(lambda _text: (history_page.setValue(1), apply_history_filter()))
        history_status.currentIndexChanged.connect(lambda _index: (history_page.setValue(1), apply_history_filter()))
        history_page.valueChanged.connect(apply_history_filter)
        apply_history_filter()

        def current_payload():
            row = table.currentRow()
            if row < 0:
                return None
            item = table.item(row, 0)
            return item.data(Qt.UserRole) if item else None

        def open_selected_output():
            payload = current_payload()
            if not payload:
                return
            out_dir = payload.get("out_dir")
            if out_dir:
                os.startfile(out_dir)
            else:
                QMessageBox.information(dialog, "输出目录", "这条记录没有可打开的输出目录。")

        def copy_selected_error():
            payload = current_payload()
            if not payload:
                return
            text = payload.get("error_text") or ""
            if not text:
                QMessageBox.information(dialog, "错误信息", "这条记录没有错误信息。")
                return
            QApplication.clipboard().setText(str(text))
            self.status_label.setText("已复制历史错误信息")

        def show_selected_error():
            payload = current_payload()
            if not payload:
                return
            entry = payload.get("entry") or {}
            text = payload.get("error_text") or entry.get("message") or entry.get("error_detail") or "这条记录没有错误信息。"
            QMessageBox.information(dialog, "任务详情", str(text)[:5000])

        def on_double_clicked(_row, _col):
            open_selected_output()

        def show_context_menu(pos):
            row = table.rowAt(pos.y())
            if row >= 0:
                table.selectRow(row)
            payload = current_payload()
            if not payload:
                return
            menu = QMenu(dialog)
            open_action = QAction("📂 打开输出目录", dialog)
            open_action.triggered.connect(open_selected_output)
            menu.addAction(open_action)
            detail_action = QAction("查看详情/错误", dialog)
            detail_action.triggered.connect(show_selected_error)
            menu.addAction(detail_action)
            copy_action = QAction("复制错误/详情", dialog)
            copy_action.triggered.connect(copy_selected_error)
            menu.addAction(copy_action)
            menu.exec_(table.viewport().mapToGlobal(pos))

        table.cellDoubleClicked.connect(on_double_clicked)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(show_context_menu)

        btn_layout = QHBoxLayout()
        open_btn = QPushButton("打开输出目录")
        open_btn.setObjectName("btn_secondary")
        open_btn.setFixedHeight(34)
        open_btn.clicked.connect(open_selected_output)
        btn_layout.addWidget(open_btn)

        detail_btn = QPushButton("查看详情/错误")
        detail_btn.setObjectName("btn_secondary")
        detail_btn.setFixedHeight(34)
        detail_btn.clicked.connect(show_selected_error)
        btn_layout.addWidget(detail_btn)

        copy_btn = QPushButton("复制错误/详情")
        copy_btn.setObjectName("btn_secondary")
        copy_btn.setFixedHeight(34)
        copy_btn.clicked.connect(copy_selected_error)
        btn_layout.addWidget(copy_btn)
        btn_layout.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("btn_secondary")
        close_btn.setFixedHeight(34)
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        if table.rowCount() > 0:
            table.selectRow(0)
        if self.window():
            qr = dialog.frameGeometry()
            cp = self.window().frameGeometry().center()
            qr.moveCenter(cp)
            dialog.move(qr.topLeft())
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
            self._sync_source_subtitles_button_from_settings()

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

    def _attach_localization_worker(self, file_path, worker):
        worker.progress_updated.connect(self._on_localization_progress)
        worker.job_started.connect(self._on_localization_job_started)
        worker.task_completed.connect(self._on_localization_completed)
        worker.task_error.connect(self._on_localization_error)
        worker.preflight_confirmation.connect(self._confirm_preflight_warnings)
        worker.finished.connect(lambda fp=file_path, w=worker: self._on_localization_worker_finished(fp, w))
        self._localization_workers[file_path] = worker
        self._localization_worker = worker
        worker.start()

    def _confirm_preflight_warnings(self, confirmation):
        warnings = (confirmation.get("preflight") or {}).get("warnings") or []
        lines = [f"• [{item.get('code', '')}] {item.get('message', '')}" for item in warnings]
        message = "任务启动前发现以下警告：\n\n" + "\n".join(lines) + "\n\n是否仍要继续？"
        answer = QMessageBox.warning(
            self,
            "Preflight 警告",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        confirmation["accepted"] = answer == QMessageBox.Yes
        confirmation["event"].set()

    def _on_localization_worker_finished(self, file_path, worker):
        if self._localization_workers.get(file_path) is worker:
            self._localization_workers.pop(file_path, None)
        if self._localization_worker is worker:
            self._localization_worker = None

    def _on_localization_job_started(self, file_path, job_id):
        if file_path not in self.video_items:
            return
        self.video_items[file_path]["localization_job_id"] = job_id
        entry = self.history.get(file_path) or {}
        extra = {"job_id": job_id}
        if self._localization_config:
            extra.update({
                "source_language": self._localization_config.get("source_language", entry.get("source_language", "auto")),
                "target_language": self._localization_config.get("target_language", entry.get("target_language", "")),
                "translation_preset_id": self._localization_config.get("translation_preset_id", ""),
                "translation_preset_name": self._localization_config.get("translation_preset_name", ""),
                "tts_preset_id": self._localization_config.get("tts_preset_id", ""),
                "tts_preset_name": self._localization_config.get("tts_preset_name", ""),
            })
        self.history.update_task_progress(
            file_path, "running", "translate", 10, "本地化任务已提交", mode="localization", **extra
        )

    def _start_localization(self, file_path, srt_path):
        # Preflight: ensure Qwen3-TTS sidecar is running before launching a dub job
        cfg = self._localization_config or {}
        if cfg.get("is_dub_mode") and "qwen3-tts" in str(cfg.get("tts_provider", "") or ""):
            import json as _json
            import urllib.request as _urllib
            _healthy = False
            try:
                with _urllib.urlopen("http://127.0.0.1:8767/health", timeout=2) as _r:
                    _healthy = _json.loads(_r.read().decode("utf-8")).get("status") == "ok"
            except Exception:
                _healthy = False
            if not _healthy:
                # Attempt to auto-start the sidecar
                try:
                    from app import ensure_qwen3_tts_engine
                    if ensure_qwen3_tts_engine():
                        _healthy = True
                except Exception:
                    _healthy = False
                if not _healthy:
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self, "Qwen3-TTS 服务未运行",
                        "当前使用配音模式 + Qwen3-TTS，但本地服务未运行。\n\n"
                        "请先在 设置 → Qwen3-TTS 管理 中点击「启动服务」后重试。",
                    )
                    if file_path in self.video_items:
                        self.video_items[file_path]["widget"].update_status("failed", 0, "Qwen3-TTS 服务未运行", "tts")
                    self.history.fail_task(file_path, message="Qwen3-TTS 服务未运行", error_detail="当前使用配音模式 + Qwen3-TTS，但本地服务未运行。", stage="tts")
                    return

        is_url = self.video_items.get(file_path, {}).get("is_url", False)
        if is_url:
            srt_path_obj = Path(srt_path)
            video_file = None
            layout = resolve_existing_layout(srt_path_obj)
            for directory in (layout.source_dir, layout.media_dir, srt_path_obj.parent):
                if not directory.exists() or not directory.is_dir():
                    continue
                for candidate in sorted(directory.iterdir()):
                    if candidate.is_file() and candidate.suffix.lower() in ['.mp4', '.mkv', '.webm', '.mov', '.m4v'] and "_proxy_" not in candidate.stem:
                        video_file = candidate
                        break
                if video_file:
                    break
            if not video_file:
                self.video_items[file_path]["widget"].update_status(
                    "failed", 0, "翻译暂不支持 URL 视频（找不到视频文件）", "prepare"
                )
                self.history.fail_task(file_path, message="翻译暂不支持 URL 视频（找不到视频文件）", stage="prepare")
                self._update_progress()
                return
            worker = LocalizationWorker(
                file_path, srt_path, str(video_file),
                self._localization_config, self.output_dir,
            )
            self._attach_localization_worker(file_path, worker)
            return
        if not Path(file_path).exists():
            self.video_items[file_path]["widget"].update_status("failed", 0, "源视频文件不存在", "prepare")
            self.history.fail_task(file_path, message="源视频文件不存在", stage="prepare")
            return

        worker = LocalizationWorker(
            file_path, srt_path, file_path,
            self._localization_config, self.output_dir,
        )
        self._attach_localization_worker(file_path, worker)

    def _on_localization_progress(self, file_path, progress, message, status, stage=""):
        if self._stopped or file_path not in self.video_items:
            return
        self.video_items[file_path]["widget"].update_status(status, progress, message, stage)
        self.history.update_task_progress(file_path, status, stage, progress, message, mode="localization")

    def _on_localization_completed(self, file_path, translated_srt):
        if self._stopped:
            return
        if file_path not in self.video_items:
            return
        from subtitle_utils import parse_srt_file
        widget = self.video_items[file_path]["widget"]
        tgt = self._localization_config.get("target_language", "zh") if self._localization_config else "zh"
        if translated_srt and Path(translated_srt).exists():
            subtitles = parse_srt_file(translated_srt)
            widget.subtitles = subtitles
            msg = f"翻译完成 ({tgt})"
            widget.update_status("completed", 100, msg, "completed")
            self.history.complete_task(
                file_path,
                message=msg,
                stage="completed",
                progress=100,
                srt_path=str(translated_srt),
                subtitle_count=len(subtitles) if subtitles else 0,
                language=tgt,
                mode="localization",
            )
            idx = self.file_list.row(self.video_items[file_path]["item"])
            if idx == self.file_list.currentRow():
                self.subtitle_viewer.show_subtitles(
                    file_path, subtitles, self.video_items[file_path].get("is_url", False)
                )
        else:
            msg = "本地化完成（翻译输出未找到）"
            widget.update_status("completed", 100, msg, "completed")
            self.history.complete_task(file_path, message=msg, stage="completed", progress=100, mode="localization")
        self._update_progress()

    def _on_localization_error(self, file_path, error_code, error_msg, error_detail, stage="error"):
        if self._stopped:
            return
        if file_path in self.video_items:
            display = str(error_msg or "处理失败")[:160]
            self.video_items[file_path]["widget"].update_status("failed", 0, display, stage, error_code, error_detail)
            self.history.fail_task(
                file_path,
                error_code=error_code,
                error_detail=error_detail or error_msg,
                message=error_msg or "处理失败",
                stage=stage or "error",
                progress=0,
                mode="localization",
            )
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

        self.caption_policy_combo = QComboBox()
        self.caption_policy_combo.addItem("自动选择：修复 YouTube 字幕，质量差则本地识别", "auto")
        self.caption_policy_combo.addItem("强制使用 YouTube 字幕", "youtube")
        self.caption_policy_combo.addItem("强制本地 Whisper 识别", "whisper")
        current_policy = self.settings.get("youtube_caption_policy", "auto")
        for i in range(self.caption_policy_combo.count()):
            if self.caption_policy_combo.itemData(i) == current_policy:
                self.caption_policy_combo.setCurrentIndex(i)
                break
        form2.addRow("YouTube字幕:", self.caption_policy_combo)

        self.caption_resegment_check = QCheckBox("修复断词并按语义重新分段（推荐开启）")
        self.caption_resegment_check.setChecked(self.settings.get("youtube_caption_resegment", "true") == "true")
        form2.addRow("智能重分段:", self.caption_resegment_check)

        provider_btn = QPushButton("管理翻译 / TTS 服务配置")
        provider_btn.setObjectName("btn_secondary")
        provider_btn.setFixedHeight(34)
        provider_btn.clicked.connect(self._open_provider_presets)
        form2.addRow("服务配置:", provider_btn)

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
        save_settings({
            "youtube_caption_policy": self.caption_policy_combo.currentData() or "auto",
            "youtube_caption_resegment": "true" if self.caption_resegment_check.isChecked() else "false",
        })
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

    def _open_provider_presets(self):
        from ui.provider_presets_dialog import ProviderPresetsDialog

        dialog = ProviderPresetsDialog(self)
        dialog.exec_()
