"""Error log visibility patch for the main window.

This module keeps the existing processing API untouched and only improves how
generation errors are displayed and copied in the desktop UI.
"""
from __future__ import annotations

import os
import time
from pathlib import Path


def _service_log_path() -> str:
    return os.environ.get("V2S_WHISPER_SERVICE_LOG", "").strip()


def _tail_text(path: str, max_lines: int = 80) -> str:
    if not path:
        return ""
    try:
        log_path = Path(path)
        if not log_path.exists() or not log_path.is_file():
            return ""
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception as exc:
        return f"读取服务日志失败: {exc}"


def _format_error_log(source: str, error_msg: str) -> str:
    log_path = _service_log_path()
    parts = [
        "Video2Subtitles 生成错误日志",
        "=" * 32,
        f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"任务: {source or '全局任务'}",
        "",
        "错误信息:",
        str(error_msg or "未知错误"),
    ]

    if log_path:
        parts.extend(["", f"本地服务日志文件: {log_path}"])
        tail = _tail_text(log_path)
        if tail:
            parts.extend(["", "服务日志末尾（最近 80 行）:", "-" * 32, tail])

    return "\n".join(parts)


def install() -> None:
    import main_window as mw
    from PyQt5.QtWidgets import (
        QApplication,
        QDialog,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QTextEdit,
        QVBoxLayout,
    )

    if getattr(mw, "_error_log_patch_installed", False):
        return

    theme = mw.THEME

    def copy_text(text: str, owner=None) -> None:
        QApplication.clipboard().setText(text or "")
        if owner is not None and hasattr(owner, "status_label"):
            owner.status_label.setText("错误日志已复制到剪贴板")

    class ErrorLogDialog(QDialog):
        def __init__(self, source: str, detail: str, parent=None):
            super().__init__(parent)
            self._detail = detail or ""
            self.setWindowTitle("错误日志")
            self.setMinimumSize(760, 520)
            self.setStyleSheet(f"""
                QDialog {{
                    background-color: {theme["bg_dark"]};
                    color: {theme["text_primary"]};
                }}
                QTextEdit {{
                    background-color: #241f33;
                    border: 2px solid {theme["error"]};
                    border-radius: 10px;
                    padding: 12px;
                    font-family: Consolas, 'Cascadia Mono', monospace;
                    font-size: 12px;
                    line-height: 1.45;
                }}
                QLabel#error_title {{
                    color: {theme["error"]};
                    font-size: 18px;
                    font-weight: 800;
                }}
            """)

            layout = QVBoxLayout(self)
            layout.setContentsMargins(18, 18, 18, 18)
            layout.setSpacing(12)

            title = QLabel("❌ 生成过程出错")
            title.setObjectName("error_title")
            layout.addWidget(title)

            source_label = QLabel(str(source or "全局任务"))
            source_label.setWordWrap(True)
            source_label.setStyleSheet(f"color: {theme['text_secondary']}; font-size: 12px;")
            layout.addWidget(source_label)

            self.text_edit = QTextEdit()
            self.text_edit.setReadOnly(True)
            self.text_edit.setPlainText(self._detail)
            layout.addWidget(self.text_edit, 1)

            buttons = QHBoxLayout()
            buttons.addStretch()

            copy_btn = QPushButton("📋 复制错误日志")
            copy_btn.clicked.connect(lambda: copy_text(self._detail, parent))
            buttons.addWidget(copy_btn)

            close_btn = QPushButton("关闭")
            close_btn.setObjectName("btn_secondary")
            close_btn.clicked.connect(self.accept)
            buttons.addWidget(close_btn)
            layout.addLayout(buttons)

    def ensure_error_button(window) -> None:
        if hasattr(window, "copy_error_btn"):
            return
        try:
            header = window.subtitle_viewer.layout().itemAt(0).layout()
            btn = QPushButton("📋 复制错误日志")
            btn.setObjectName("btn_secondary")
            btn.setFixedHeight(34)
            btn.setVisible(False)
            btn.clicked.connect(lambda: copy_text(getattr(window, "_current_error_log", ""), window))
            header.addWidget(btn)
            window.copy_error_btn = btn
        except Exception:
            window.copy_error_btn = None

    def set_normal_preview_style(viewer) -> None:
        viewer.text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: {theme["bg_medium"]};
                border: 1px solid {theme["border"]};
                border-radius: 12px;
                padding: 16px;
                font-size: 14px;
                line-height: 1.7;
            }}
        """)

    def show_error_preview(window, source: str, error_msg: str, detail: str) -> None:
        ensure_error_button(window)
        window._current_error_log = detail or ""
        viewer = window.subtitle_viewer

        for attr in ("open_dir_btn", "export_btn", "export_vtt_btn", "export_txt_btn"):
            btn = getattr(viewer, attr, None)
            if btn is not None:
                btn.setVisible(False)
        if getattr(window, "copy_error_btn", None) is not None:
            window.copy_error_btn.setVisible(True)

        viewer._subtitles = None
        viewer._file_path = source
        viewer.text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: #2b1f2f;
                border: 2px solid {theme["error"]};
                border-radius: 12px;
                padding: 16px;
                font-family: Consolas, 'Cascadia Mono', monospace;
                font-size: 12px;
                line-height: 1.55;
                selection-background-color: {theme["error"]}66;
            }}
        """)
        viewer.text_edit.setPlainText(detail)
        viewer.text_edit.moveCursor(mw.QTextCursor.Start)

    original_main_init = mw.MainWindow.__init__

    def patched_main_init(self, *args, **kwargs):
        original_main_init(self, *args, **kwargs)
        self._error_logs = {}
        self._current_error_log = ""
        self._shown_error_dialogs = set()
        ensure_error_button(self)

    mw.MainWindow.__init__ = patched_main_init

    original_item_update = mw.VideoItemWidget.update_status

    def patched_item_update(self, status, progress=0, message=""):
        original_item_update(self, status, progress, message)
        if status == "error":
            text = message or self.message or "处理失败"
            self.status_label.setText(f"❌ {text}")
            self.status_label.setToolTip(text)
            self.setToolTip(text)
            self.status_label.setStyleSheet(
                f"font-size: 11px; color: {theme['error']}; font-weight: 800; "
                f"background-color: #4a1f2d; padding: 2px 6px; border-radius: 6px;"
            )

    mw.VideoItemWidget.update_status = patched_item_update

    original_show_subtitles = mw.SubtitleViewer.show_subtitles

    def patched_show_subtitles(self, *args, **kwargs):
        parent = self.window() if self.window() else None
        if parent is not None and getattr(parent, "copy_error_btn", None) is not None:
            parent.copy_error_btn.setVisible(False)
            parent._current_error_log = ""
        set_normal_preview_style(self)
        return original_show_subtitles(self, *args, **kwargs)

    mw.SubtitleViewer.show_subtitles = patched_show_subtitles

    original_clear = mw.SubtitleViewer.clear

    def patched_clear(self):
        parent = self.window() if self.window() else None
        if parent is not None and getattr(parent, "copy_error_btn", None) is not None:
            parent.copy_error_btn.setVisible(False)
            parent._current_error_log = ""
        set_normal_preview_style(self)
        return original_clear(self)

    mw.SubtitleViewer.clear = patched_clear

    original_on_error = mw.MainWindow._on_error

    def patched_on_error(self, file_path, error_msg):
        msg = str(error_msg or "未知错误")
        key = str(file_path or "")
        detail = _format_error_log(key, msg)

        if not hasattr(self, "_error_logs"):
            self._error_logs = {}
        if key:
            self._error_logs[key] = detail

        original_on_error(self, file_path, msg)

        try:
            if key and key in self.video_items:
                widget = self.video_items[key]["widget"]
                widget.message = msg
                widget.setToolTip(detail)
                row = self.file_list.row(self.video_items[key]["item"])
                if row == self.file_list.currentRow():
                    show_error_preview(self, key, msg, detail)

                shown = getattr(self, "_shown_error_dialogs", set())
                if key not in shown:
                    shown.add(key)
                    self._shown_error_dialogs = shown
                    dialog = ErrorLogDialog(key, detail, self)
                    dialog.exec_()
            elif not key:
                self._current_error_log = detail
        except Exception as exc:
            print(f"error_log_patch _on_error failed: {exc}")

    mw.MainWindow._on_error = patched_on_error

    original_on_selection_changed = getattr(mw.MainWindow, "_on_selection_changed", None)

    if original_on_selection_changed is not None:
        def patched_on_selection_changed(self, row):
            original_on_selection_changed(self, row)
            try:
                item = self.file_list.item(row)
                if not item:
                    return
                widget = self.file_list.itemWidget(item)
                if not widget or widget.status != "error":
                    return
                key = str(widget.file_path)
                detail = getattr(self, "_error_logs", {}).get(key)
                if not detail:
                    detail = _format_error_log(key, widget.message or "处理失败")
                    self._error_logs[key] = detail
                show_error_preview(self, key, widget.message or "处理失败", detail)
            except Exception as exc:
                print(f"error_log_patch selection failed: {exc}")

        mw.MainWindow._on_selection_changed = patched_on_selection_changed

    original_start_processing = mw.MainWindow._start_processing

    def patched_start_processing(self, specific_files=None):
        try:
            keys = []
            if isinstance(specific_files, (list, tuple)):
                keys = [str(p) for p in specific_files]
            else:
                keys = [
                    str(p) for p, data in self.video_items.items()
                    if data["widget"].status in ("pending", "error")
                ]
            for key in keys:
                getattr(self, "_error_logs", {}).pop(key, None)
            if getattr(self, "copy_error_btn", None) is not None:
                self.copy_error_btn.setVisible(False)
                self._current_error_log = ""
        except Exception:
            pass
        return original_start_processing(self, specific_files)

    mw.MainWindow._start_processing = patched_start_processing

    mw._error_log_patch_installed = True
