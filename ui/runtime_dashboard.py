"""Compact PyQt runtime/resource dashboard for localization tasks."""
from __future__ import annotations

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from localization_client import LocalizationClient


class RuntimeDashboardDialog(QDialog):
    def __init__(self, parent=None, client: LocalizationClient | None = None) -> None:
        super().__init__(parent)
        self.client = client or LocalizationClient()
        self.setWindowTitle("运行资源")
        self.resize(680, 520)

        layout = QVBoxLayout(self)
        metrics = QGroupBox("实时资源（活动任务约每 2 秒采样）")
        form = QFormLayout(metrics)
        self.cpu = _progress()
        self.memory = _progress()
        self.gpu = _progress()
        self.vram = _progress()
        self.disk = QLabel("--")
        form.addRow("CPU", self.cpu)
        form.addRow("系统内存", self.memory)
        form.addRow("GPU", self.gpu)
        form.addRow("显存", self.vram)
        form.addRow("工作区剩余磁盘", self.disk)
        layout.addWidget(metrics)

        model_group = QGroupBox("当前加载模型")
        model_layout = QVBoxLayout(model_group)
        self.models = QTableWidget(0, 5)
        self.models.setHorizontalHeaderLabels(["类型", "模型", "设备", "状态", "引用数"])
        self.models.horizontalHeader().setStretchLastSection(True)
        self.models.verticalHeader().setVisible(False)
        model_layout.addWidget(self.models)
        layout.addWidget(model_group, 1)

        footer = QHBoxLayout()
        self.status = QLabel("正在连接本地化引擎…")
        footer.addWidget(self.status, 1)
        refresh = QPushButton("立即刷新")
        refresh.clicked.connect(lambda: self.refresh(True))
        footer.addWidget(refresh)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)

        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        QTimer.singleShot(0, lambda: self.refresh(True))

    def refresh(self, force: bool = False) -> None:
        data = self.client.get_runtime_metrics(refresh=force)
        if data.get("error"):
            self.status.setText(f"资源查询失败：{data['error']}")
            return
        metrics = data.get("metrics") or {}
        _set_progress(self.cpu, metrics.get("cpu_percent", 0))
        _set_progress(self.memory, metrics.get("memory_percent", 0))
        gpus = metrics.get("gpus") or []
        if gpus:
            gpu = gpus[0]
            _set_progress(self.gpu, gpu.get("utilization_percent", 0))
            total = max(1, int(gpu.get("memory_total_mb", 0) or 0))
            used = int(gpu.get("memory_used_mb", 0) or 0)
            _set_progress(self.vram, used * 100 / total, f"{used} / {total} MB")
        else:
            _set_progress(self.gpu, 0, "无可用 GPU / 查询降级")
            _set_progress(self.vram, 0, "--")
        free = int(metrics.get("disk_free_bytes", 0) or 0)
        total = int(metrics.get("disk_total_bytes", 0) or 0)
        self.disk.setText(f"{_bytes(free)} 可用 / {_bytes(total)}")
        self._set_models(data.get("models") or metrics.get("loaded_models") or [])
        self.status.setText("资源状态已更新")

    def _set_models(self, models: list[dict]) -> None:
        self.models.setRowCount(len(models))
        for row, model in enumerate(models):
            values = (
                model.get("kind", ""), model.get("model_id", ""),
                model.get("device", ""), model.get("state", ""),
                model.get("ref_count", 0),
            )
            for column, value in enumerate(values):
                self.models.setItem(row, column, QTableWidgetItem(str(value)))
        self.models.resizeColumnsToContents()

    def closeEvent(self, event) -> None:
        self.timer.stop()
        super().closeEvent(event)


def _progress() -> QProgressBar:
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setFormat("%p%")
    return bar


def _set_progress(bar: QProgressBar, value, text: str = "") -> None:
    numeric = max(0, min(100, round(float(value or 0))))
    bar.setValue(numeric)
    bar.setFormat(text or f"{numeric}%")


def _bytes(value: int) -> str:
    amount = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"
