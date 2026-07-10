"""First-generation PyQt subtitle timeline editor (single track, no waveform)."""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtCore import QPoint, QRectF, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QKeySequence, QPainter, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
    from PyQt5.QtMultimediaWidgets import QVideoWidget
except ImportError:  # pragma: no cover - optional Qt multimedia runtime
    QMediaContent = None
    QMediaPlayer = None
    QVideoWidget = None

ENGINE_ROOT = Path(__file__).resolve().parents[1] / "localization-engine"
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from subtitles import (  # noqa: E402
    DeleteCue,
    FindReplace,
    InsertCue,
    MergeCues,
    ShiftCues,
    SplitCue,
    SubtitleDocument,
    SubtitleEditor,
    SubtitleValidator,
    UpdateCue,
)
from localization_client import LocalizationClient  # noqa: E402


class SubtitleTimelineWidget(QWidget):
    cueSelected = pyqtSignal(str)
    cueTimingChanged = pyqtSignal(str, int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.cues = []
        self.duration_ms = 1
        self.position_ms = 0
        self.selected_id = ""
        self._drag = None
        self._pending_timing = None
        self.setMinimumHeight(120)
        self.setMouseTracking(True)

    def set_document(self, document: SubtitleDocument) -> None:
        self.cues = list(document.cues)
        self.duration_ms = max(1, max((cue.end_ms for cue in self.cues), default=1))
        self.update()

    def set_position(self, position_ms: int) -> None:
        self.position_ms = max(0, int(position_ms))
        self.update()

    def set_selected(self, cue_id: str) -> None:
        self.selected_id = cue_id
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#181c24"))
        width = max(1, self.width() - 20)
        y, height = 36, 52
        painter.setPen(QPen(QColor("#6b7280")))
        for division in range(11):
            x = 10 + width * division / 10
            painter.drawLine(round(x), 18, round(x), self.height() - 8)
            painter.drawText(round(x) + 2, 14, _format_ms(round(self.duration_ms * division / 10), compact=True))
        for cue in self.cues:
            left = 10 + width * cue.start_ms / self.duration_ms
            right = 10 + width * cue.end_ms / self.duration_ms
            rect = QRectF(left, y, max(4, right - left), height)
            color = QColor("#4f46e5" if cue.cue_id == self.selected_id else "#2563eb")
            painter.fillRect(rect, color)
            painter.setPen(QPen(QColor("#dbeafe")))
            painter.drawRect(rect)
            painter.drawText(rect.adjusted(5, 3, -5, -3), Qt.AlignLeft | Qt.AlignVCenter, cue.translated_text or cue.source_text)
        playhead = 10 + width * min(self.position_ms, self.duration_ms) / self.duration_ms
        painter.setPen(QPen(QColor("#f43f5e"), 2))
        painter.drawLine(round(playhead), 20, round(playhead), self.height() - 5)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        hit = self._hit(event.pos())
        if not hit:
            return
        cue, mode = hit
        self.selected_id = cue.cue_id
        self.cueSelected.emit(cue.cue_id)
        self._drag = (cue, mode, event.pos().x(), cue.start_ms, cue.end_ms)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if not self._drag:
            return
        cue, mode, origin_x, start_ms, end_ms = self._drag
        delta = round((event.pos().x() - origin_x) * self.duration_ms / max(1, self.width() - 20))
        if mode == "left":
            start_ms = min(end_ms - 50, max(0, start_ms + delta))
        elif mode == "right":
            end_ms = max(start_ms + 50, end_ms + delta)
        else:
            duration = end_ms - start_ms
            start_ms = max(0, start_ms + delta)
            end_ms = start_ms + duration
        self._pending_timing = (cue.cue_id, start_ms, end_ms)
        self.cues = [
            item.updated(start_ms=start_ms, end_ms=end_ms) if item.cue_id == cue.cue_id else item
            for item in self.cues
        ]
        self.update()

    def mouseReleaseEvent(self, _event) -> None:
        if self._pending_timing:
            self.cueTimingChanged.emit(*self._pending_timing)
        self._pending_timing = None
        self._drag = None

    def _hit(self, point: QPoint):
        if not 30 <= point.y() <= 95:
            return None
        width = max(1, self.width() - 20)
        for cue in reversed(self.cues):
            left = 10 + width * cue.start_ms / self.duration_ms
            right = 10 + width * cue.end_ms / self.duration_ms
            if left - 5 <= point.x() <= right + 5:
                if abs(point.x() - left) <= 7:
                    return cue, "left"
                if abs(point.x() - right) <= 7:
                    return cue, "right"
                return cue, "move"
        return None


class SubtitleTimelineDialog(QDialog):
    def __init__(
        self,
        job_id: str,
        source_video: str = "",
        parent=None,
        client: LocalizationClient | None = None,
    ) -> None:
        super().__init__(parent)
        self.job_id = job_id
        self.source_video = source_video
        self.client = client or LocalizationClient()
        self.document: SubtitleDocument | None = None
        self.editor: SubtitleEditor | None = None
        self.base_version = 0
        self.dirty = False
        self._refreshing = False
        self._active_cue = ""
        self.player = QMediaPlayer(self) if QMediaPlayer else None
        self.validator = SubtitleValidator()
        self.setWindowTitle(f"字幕时间轴编辑器 — {job_id[:12]}")
        self.resize(1180, 760)

        root = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.play_button = QPushButton("播放")
        self.play_button.clicked.connect(self._toggle_play)
        toolbar.addWidget(self.play_button)
        self.time_label = QLabel("00:00:00.000")
        toolbar.addWidget(self.time_label)
        for label, handler in (
            ("插入", self._insert), ("删除", self._delete), ("拆分", self._split),
            ("合并下一条", self._merge), ("批量平移", self._shift), ("查找替换", self._replace),
            ("撤销", self._undo), ("重做", self._redo),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        toolbar.addStretch()
        self.issue_button = QPushButton("校验问题 0")
        self.issue_button.clicked.connect(self._next_issue)
        toolbar.addWidget(self.issue_button)
        save = QPushButton("仅保存字幕")
        save.clicked.connect(lambda: self._save(False))
        toolbar.addWidget(save)
        regenerate = QPushButton("保存并重新生成")
        regenerate.clicked.connect(lambda: self._save(True))
        toolbar.addWidget(regenerate)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Vertical)
        upper = QWidget()
        upper_layout = QVBoxLayout(upper)
        if self.player and QVideoWidget:
            self.video = QVideoWidget()
            self.video.setMinimumHeight(240)
            self.player.setVideoOutput(self.video)
            self.player.positionChanged.connect(self._position_changed)
            self.player.durationChanged.connect(self._duration_changed)
            upper_layout.addWidget(self.video)
        else:
            upper_layout.addWidget(QLabel("当前 Qt 环境没有视频播放组件；字幕编辑仍可使用。"))
        self.timeline = SubtitleTimelineWidget()
        self.timeline.cueSelected.connect(self._select_cue)
        self.timeline.cueTimingChanged.connect(self._timeline_timing_changed)
        upper_layout.addWidget(self.timeline)
        splitter.addWidget(upper)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", "开始", "结束", "原文", "译文"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._table_changed)
        self.table.cellClicked.connect(self._row_clicked)
        splitter.addWidget(self.table)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.save_state = QLabel("正在加载字幕…")
        footer.addWidget(self.save_state, 1)
        history = QPushButton("修订历史")
        history.clicked.connect(self._show_history)
        footer.addWidget(history)
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        root.addLayout(footer)

        QShortcut(QKeySequence.Undo, self, self._undo)
        QShortcut(QKeySequence.Redo, self, self._redo)
        QShortcut(QKeySequence.Save, self, lambda: self._save(False))
        self.autosave = QTimer(self)
        self.autosave.setInterval(2500)
        self.autosave.timeout.connect(self._autosave)
        self.autosave.start()
        QTimer.singleShot(0, self._load)

    def _load(self) -> None:
        result = self.client.get_subtitle_document(self.job_id)
        if result.get("error"):
            QMessageBox.critical(self, "字幕编辑器", result["error"])
            self.reject()
            return
        formal = SubtitleDocument.from_dict(result["document"])
        draft = result.get("draft")
        self.document = SubtitleDocument.from_dict(draft) if draft else formal
        self.base_version = formal.version
        self.editor = SubtitleEditor(self.document)
        self.dirty = bool(draft)
        self._refresh()
        self.save_state.setText("已恢复自动保存草稿" if draft else f"正式版本 v{self.base_version}")
        if self.player and QMediaContent and self.source_video and Path(self.source_video).is_file():
            self.player.setMedia(QMediaContent(QUrl.fromLocalFile(str(Path(self.source_video).resolve()))))

    def _refresh(self, selected_id: str = "") -> None:
        if not self.editor:
            return
        self.document = self.editor.document
        selected_id = selected_id or self._active_cue
        self._refreshing = True
        self.table.setRowCount(len(self.document.cues))
        for row, cue in enumerate(self.document.cues):
            values = (str(row + 1), _format_ms(cue.start_ms), _format_ms(cue.end_ms), cue.source_text, cue.translated_text)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, cue.cue_id)
                if column == 0:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, column, item)
            if cue.cue_id == selected_id:
                self.table.selectRow(row)
        self.timeline.set_document(self.document)
        self.timeline.set_selected(selected_id)
        self._validate()
        self._refreshing = False

    def _apply(self, command, selected_id: str = "") -> None:
        if not self.editor:
            return
        try:
            self.editor.execute(command)
        except (KeyError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "字幕编辑", str(exc))
            return
        self.dirty = True
        self.save_state.setText("有未保存修改")
        self._refresh(selected_id)

    def _table_changed(self, item: QTableWidgetItem) -> None:
        if self._refreshing or not self.document or item.column() == 0:
            return
        cue_id = str(item.data(Qt.UserRole) or "")
        changes = {}
        try:
            if item.column() == 1:
                changes["start_ms"] = _parse_time(item.text())
            elif item.column() == 2:
                changes["end_ms"] = _parse_time(item.text())
            elif item.column() == 3:
                changes["source_text"] = item.text()
            elif item.column() == 4:
                changes["translated_text"] = item.text()
        except ValueError as exc:
            QMessageBox.warning(self, "时间格式", str(exc))
            self._refresh(cue_id)
            return
        self._apply(UpdateCue(cue_id, changes), cue_id)

    def _current_cue(self):
        if not self.document or self.table.currentRow() < 0:
            return None
        row = self.table.currentRow()
        return self.document.cues[row] if row < len(self.document.cues) else None

    def _row_clicked(self, row: int, _column: int) -> None:
        if not self.document or row >= len(self.document.cues):
            return
        cue = self.document.cues[row]
        self._active_cue = cue.cue_id
        self.timeline.set_selected(cue.cue_id)
        if self.player:
            self.player.setPosition(cue.start_ms)

    def _select_cue(self, cue_id: str) -> None:
        if not self.document:
            return
        for row, cue in enumerate(self.document.cues):
            if cue.cue_id == cue_id:
                self.table.selectRow(row)
                if self.player:
                    self.player.setPosition(cue.start_ms)
                break

    def _highlight_cue(self, cue_id: str) -> None:
        if not self.document:
            return
        for row, cue in enumerate(self.document.cues):
            if cue.cue_id == cue_id:
                self.table.selectRow(row)
                self.timeline.set_selected(cue_id)
                break

    def _timeline_timing_changed(self, cue_id: str, start_ms: int, end_ms: int) -> None:
        self._apply(UpdateCue(cue_id, {"start_ms": start_ms, "end_ms": end_ms}), cue_id)

    def _insert(self) -> None:
        cue = self._current_cue()
        start = cue.end_ms if cue else 0
        self._apply(InsertCue(start, start + 1500, "新字幕", "", cue.cue_id if cue else ""))

    def _delete(self) -> None:
        cue = self._current_cue()
        if cue:
            self._apply(DeleteCue(cue.cue_id))

    def _split(self) -> None:
        cue = self._current_cue()
        if cue and len(cue.source_text) > 1:
            self._apply(SplitCue(cue.cue_id, len(cue.source_text) // 2), cue.cue_id)

    def _merge(self) -> None:
        cue = self._current_cue()
        if not cue or not self.document:
            return
        index = self.document.cues.index(cue)
        if index + 1 < len(self.document.cues):
            self._apply(MergeCues(cue.cue_id, self.document.cues[index + 1].cue_id), cue.cue_id)

    def _shift(self) -> None:
        value, ok = QInputDialog.getInt(self, "批量平移", "偏移毫秒（可为负数）", 0, -3_600_000, 3_600_000)
        if ok:
            self._apply(ShiftCues(value))

    def _replace(self) -> None:
        find, ok = QInputDialog.getText(self, "查找替换", "查找")
        if not ok or not find:
            return
        replacement, ok = QInputDialog.getText(self, "查找替换", "替换为")
        if ok:
            self._apply(FindReplace(find, replacement))

    def _undo(self) -> None:
        if self.editor and self.editor.can_undo:
            self.editor.undo()
            self.dirty = True
            self._refresh()

    def _redo(self) -> None:
        if self.editor and self.editor.can_redo:
            self.editor.redo()
            self.dirty = True
            self._refresh()

    def _validate(self) -> None:
        self.issues = self.validator.validate(self.document) if self.document else []
        self.issue_button.setText(f"校验问题 {len(self.issues)}")
        issue_by_cue = {}
        for issue in self.issues:
            issue_by_cue.setdefault(issue.cue_id, []).append(issue)
        for row, cue in enumerate(self.document.cues if self.document else []):
            tooltip = "\n".join(
                f"[{issue.code}] {issue.message}" for issue in issue_by_cue.get(cue.cue_id, [])
            )
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                if item:
                    item.setToolTip(tooltip)

    def _next_issue(self) -> None:
        if not self.issues:
            QMessageBox.information(self, "字幕校验", "未发现字幕问题。")
            return
        current = self._current_cue()
        start = 0
        if current:
            ids = [issue.cue_id for issue in self.issues]
            if current.cue_id in ids:
                start = (ids.index(current.cue_id) + 1) % len(ids)
        issue = self.issues[start]
        self._select_cue(issue.cue_id)
        self.save_state.setText(f"[{issue.code}] {issue.message}；建议：{issue.suggestion}")

    def _autosave(self) -> None:
        if not self.dirty or not self.document:
            return
        result = self.client.save_subtitle_draft(self.job_id, self.document.to_dict(), self.base_version)
        if result.get("error"):
            self.save_state.setText(f"自动保存失败 [{result.get('error_code', '')}]：{result['error']}")
        else:
            self.save_state.setText("草稿已自动保存")

    def _save(self, regenerate: bool) -> None:
        if not self.document:
            return
        result = self.client.save_subtitle_revision(
            self.job_id, self.document.to_dict(), self.base_version, regenerate=regenerate
        )
        if result.get("error"):
            QMessageBox.warning(self, "保存字幕", f"[{result.get('error_code', '')}] {result['error']}")
            return
        self.document = SubtitleDocument.from_dict(result["document"])
        self.base_version = self.document.version
        self.editor = SubtitleEditor(self.document)
        self.dirty = False
        self._refresh()
        suffix = "，正在从 TTS 阶段重新生成" if result.get("regenerating") else ""
        self.save_state.setText(f"正式版本 v{self.base_version} 已保存{suffix}")

    def _show_history(self) -> None:
        result = self.client.list_subtitle_revisions(self.job_id)
        revisions = result.get("revisions") or []
        if not revisions:
            QMessageBox.information(self, "修订历史", "暂无正式修订。")
            return
        labels = [f"v{item['version']} — {item['created_at']}" for item in revisions]
        choice, ok = QInputDialog.getItem(self, "修订历史", "选择要恢复的版本", labels, 0, False)
        if not ok:
            return
        revision = revisions[labels.index(choice)]
        confirm = QMessageBox.question(
            self, "恢复修订", f"将 v{revision['version']} 恢复为新的正式版本？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        restored = self.client.restore_subtitle_revision(
            self.job_id, revision["id"], self.base_version
        )
        if restored.get("error"):
            QMessageBox.warning(self, "恢复修订", restored["error"])
            return
        self.document = SubtitleDocument.from_dict(restored["document"])
        self.base_version = self.document.version
        self.editor = SubtitleEditor(self.document)
        self.dirty = False
        self._refresh()

    def _toggle_play(self) -> None:
        if not self.player:
            return
        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_button.setText("播放")
        else:
            self.player.play()
            self.play_button.setText("暂停")

    def _position_changed(self, position_ms: int) -> None:
        self.time_label.setText(_format_ms(position_ms))
        self.timeline.set_position(position_ms)
        if not self.document:
            return
        active = next((cue for cue in self.document.cues if cue.start_ms <= position_ms < cue.end_ms), None)
        if active and active.cue_id != self._active_cue:
            self._active_cue = active.cue_id
            self._highlight_cue(active.cue_id)

    def _duration_changed(self, duration_ms: int) -> None:
        if duration_ms > 0:
            self.timeline.duration_ms = max(duration_ms, self.timeline.duration_ms)
            self.timeline.update()

    def closeEvent(self, event) -> None:
        if self.dirty:
            answer = QMessageBox.question(
                self,
                "未保存修改",
                "字幕仍有未保存修改。关闭前保存为正式修订吗？",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if answer == QMessageBox.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.Save:
                self._save(False)
                if self.dirty:
                    event.ignore()
                    return
        self.autosave.stop()
        if self.player:
            self.player.stop()
        super().closeEvent(event)


def _format_ms(value: int, compact: bool = False) -> str:
    value = max(0, int(value))
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    if compact and not hours:
        return f"{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _parse_time(value: str) -> int:
    parts = str(value).strip().replace(",", ".").split(":")
    if len(parts) != 3:
        raise ValueError("时间格式应为 HH:MM:SS.mmm")
    try:
        hours, minutes = int(parts[0]), int(parts[1])
        seconds = float(parts[2])
    except ValueError as exc:
        raise ValueError("时间格式应为 HH:MM:SS.mmm") from exc
    result = round((hours * 3600 + minutes * 60 + seconds) * 1000)
    if result < 0:
        raise ValueError("时间不能为负数")
    return result
