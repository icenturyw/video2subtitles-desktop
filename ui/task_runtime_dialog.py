"""Task detail view for stages, resources, models, events, and safe guidance."""
from __future__ import annotations

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from localization_client import LocalizationClient


class TaskRuntimeDialog(QDialog):
    SAFE_ACTIONS = {
        "retry-stage", "open-settings", "open-output", "view-log",
        "install-ffmpeg", "free-disk", "choose-cpu", "edit-subtitles",
    }

    def __init__(self, job_id: str, parent=None, client: LocalizationClient | None = None) -> None:
        super().__init__(parent)
        self.job_id = job_id
        self.client = client or LocalizationClient()
        self.setWindowTitle(f"任务运行详情 — {job_id[:12]}")
        self.resize(900, 680)
        root = QVBoxLayout(self)

        summary = QGroupBox("当前状态")
        summary_layout = QVBoxLayout(summary)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        summary_layout.addWidget(self.progress)
        self.stage = QLabel("阶段：--")
        self.resources = QLabel("CPU --  内存 --  GPU --  显存 --")
        self.models = QLabel("加载模型：--")
        self.error = QLabel("")
        self.error.setWordWrap(True)
        summary_layout.addWidget(self.stage)
        summary_layout.addWidget(self.resources)
        summary_layout.addWidget(self.models)
        summary_layout.addWidget(self.error)
        root.addWidget(summary)

        self.stages = QTableWidget(0, 5)
        self.stages.setHorizontalHeaderLabels(["阶段", "尝试", "状态", "耗时", "错误码"])
        self.stages.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.stages, 1)
        self.events = QTableWidget(0, 4)
        self.events.setHorizontalHeaderLabels(["时间", "类型", "消息", "阶段/进度"])
        self.events.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.events, 1)

        self.guidance_layout = QHBoxLayout()
        root.addLayout(self.guidance_layout)
        footer = QHBoxLayout()
        self.status = QLabel()
        footer.addWidget(self.status, 1)
        logs = QPushButton("完整日志")
        logs.clicked.connect(self._show_logs)
        footer.addWidget(logs)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        root.addLayout(footer)

        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        QTimer.singleShot(0, self.refresh)

    def refresh(self) -> None:
        detail = self.client.get_job_detail(self.job_id)
        if detail.get("error"):
            self.status.setText(f"详情查询失败：{detail['error']}")
            return
        task = detail.get("task") or {}
        self.progress.setValue(int(task.get("progress", 0) or 0))
        current_stage = task.get("stage") or task.get("current_stage") or "--"
        runs = detail.get("stage_runs") or []
        current_run = next((run for run in reversed(runs) if run.get("stage") == current_stage), None)
        elapsed = float((current_run or {}).get("elapsed_seconds", 0) or 0)
        self.stage.setText(f"阶段：{current_stage}  阶段耗时：{elapsed:.1f}s  状态：{task.get('status', '--')}")
        runtime = detail.get("runtime") or {}
        gpus = runtime.get("gpus") or []
        gpu = gpus[0] if gpus else {}
        self.resources.setText(
            f"CPU {float(runtime.get('cpu_percent', 0) or 0):.0f}%  "
            f"内存 {float(runtime.get('memory_percent', 0) or 0):.0f}%  "
            f"GPU {float(gpu.get('utilization_percent', 0) or 0):.0f}%  "
            f"显存 {int(gpu.get('memory_used_mb', 0) or 0)} / {int(gpu.get('memory_total_mb', 0) or 0)} MB"
        )
        loaded = detail.get("loaded_models") or []
        self.models.setText("加载模型：" + (", ".join(item.get("model_id", "") for item in loaded) or "无"))
        code = task.get("error_code") or ""
        message = task.get("error_detail") or task.get("message") or ""
        self.error.setText(f"错误码：{code or '无'}\n{message}")
        self._fill_stages(runs)
        self._fill_events(detail.get("events") or [])
        self._set_guidance(detail.get("guidance") or [])
        self.status.setText("任务详情已更新")

    def _fill_stages(self, runs: list[dict]) -> None:
        self.stages.setRowCount(len(runs))
        for row, run in enumerate(runs):
            values = (
                run.get("stage", ""), run.get("attempt", ""), run.get("status", ""),
                f"{float(run.get('elapsed_seconds', 0) or 0):.1f}s", run.get("error_code", "") or "",
            )
            for column, value in enumerate(values):
                self.stages.setItem(row, column, QTableWidgetItem(str(value)))
        self.stages.resizeColumnsToContents()

    def _fill_events(self, events: list[dict]) -> None:
        important = events[-200:]
        self.events.setRowCount(len(important))
        for row, event in enumerate(important):
            payload = event.get("payload") or {}
            stage_progress = " / ".join(
                str(value) for value in (payload.get("stage"), payload.get("progress"))
                if value not in (None, "")
            )
            values = (
                event.get("created_at", ""), event.get("event_type", ""),
                event.get("message", ""), stage_progress,
            )
            for column, value in enumerate(values):
                self.events.setItem(row, column, QTableWidgetItem(str(value)))
        self.events.resizeColumnsToContents()

    def _set_guidance(self, actions: list[dict]) -> None:
        while self.guidance_layout.count():
            item = self.guidance_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for action in actions:
            action_id = str(action.get("action_id") or "")
            if action_id not in self.SAFE_ACTIONS:
                continue
            button = QPushButton(str(action.get("label") or action_id))
            button.clicked.connect(lambda _checked=False, key=action_id: self._run_action(key))
            self.guidance_layout.addWidget(button)
        self.guidance_layout.addStretch()

    def _run_action(self, action_id: str) -> None:
        handlers = {
            "retry-stage": lambda: self.client.retry_failed_stage(self.job_id),
            "view-log": self._show_logs,
        }
        handler = handlers.get(action_id)
        if handler:
            result = handler()
            if isinstance(result, dict) and result.get("error"):
                QMessageBox.warning(self, "任务建议", result["error"])
            self.refresh()
        else:
            QMessageBox.information(self, "任务建议", "请从主窗口执行该操作。")

    def _show_logs(self):
        result = self.client.get_logs(self.job_id, tail=500)
        if result.get("error"):
            QMessageBox.warning(self, "任务日志", result["error"])
            return result
        QMessageBox.information(self, "任务日志", "\n".join(result.get("lines") or ["暂无日志"])[:12000])
        return result

    def closeEvent(self, event) -> None:
        self.timer.stop()
        super().closeEvent(event)
