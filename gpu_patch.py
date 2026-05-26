"""GPU settings patch for the existing settings dialog."""
from __future__ import annotations

from PyQt5.QtWidgets import QComboBox, QFormLayout, QGroupBox, QLabel

import main_window as mw
from client_settings import apply_settings_to_env, save_settings
from gpu_config import (
    SUPPORTED_COMPUTE_TYPES,
    SUPPORTED_DEVICES,
    device_status,
    resolve_device_and_compute,
)


THEME = mw.THEME

DEVICE_LABELS = {
    "auto": "自动：优先 NVIDIA CUDA，失败则 CPU",
    "cuda": "NVIDIA CUDA / GPU",
    "cpu": "CPU",
}
COMPUTE_LABELS = {
    "auto": "自动：GPU 用 float16，CPU 用 int8",
    "float16": "float16（推荐 NVIDIA GPU）",
    "int8_float16": "int8_float16（省显存 GPU）",
    "int8": "int8（推荐 CPU）",
    "float32": "float32（兼容但较慢）",
}


def _select_data(combo, value):
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return


def install():
    OriginalSettingsDialog = mw.SettingsDialog

    class GPUSettingsDialog(OriginalSettingsDialog):
        def __init__(self, parent=None, current_output_dir=None):
            super().__init__(parent, current_output_dir)
            try:
                self.setFixedSize(max(self.width(), 720), max(self.height(), 900))
            except Exception:
                pass
            self._append_gpu_group()

        def _append_gpu_group(self):
            group = QGroupBox("GPU / 推理设备")
            form = QFormLayout(group)
            form.setSpacing(12)

            self.device_combo = QComboBox()
            for value in SUPPORTED_DEVICES:
                self.device_combo.addItem(DEVICE_LABELS.get(value, value), value)
            _select_data(self.device_combo, self.model_settings.get("device", "auto"))
            form.addRow("推理设备:", self.device_combo)

            self.compute_combo = QComboBox()
            for value in SUPPORTED_COMPUTE_TYPES:
                self.compute_combo.addItem(COMPUTE_LABELS.get(value, value), value)
            _select_data(self.compute_combo, self.model_settings.get("compute_type", "auto"))
            form.addRow("计算类型:", self.compute_combo)

            status = device_status()
            auto_device, auto_compute = resolve_device_and_compute("auto", "auto")
            gpu_name = status.get("gpu_name") or "未检测到 NVIDIA GPU"
            status_text = f"检测结果：{gpu_name}；自动模式将使用 {auto_device}/{auto_compute}。"
            self.gpu_status_label = QLabel(status_text)
            self.gpu_status_label.setWordWrap(True)
            self.gpu_status_label.setStyleSheet(f"font-size: 11px; color: {THEME['text_muted']};")
            form.addRow("", self.gpu_status_label)

            layout = self.layout()
            insert_at = max(0, layout.count() - 2)
            layout.insertWidget(insert_at, group)

        def _collect_model_settings(self, create_model_dir=True):
            settings = super()._collect_model_settings(create_model_dir=create_model_dir)
            if not settings:
                return None
            settings["device"] = self.device_combo.currentData() or "auto"
            settings["compute_type"] = self.compute_combo.currentData() or "auto"
            return settings

        def _save_model_settings_only(self):
            settings = self._collect_model_settings(create_model_dir=True)
            if not settings:
                return None
            saved = save_settings(settings)
            apply_settings_to_env(saved, overwrite=True)
            self.model_settings = saved
            runtime_device, runtime_compute = resolve_device_and_compute(
                saved.get("device", "auto"), saved.get("compute_type", "auto")
            )
            self._set_model_status(f"设置已保存。当前将使用 {runtime_device}/{runtime_compute}。", "success")
            return saved

    mw.SettingsDialog = GPUSettingsDialog
