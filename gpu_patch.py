"""GPU settings patch for the existing settings dialog."""
from __future__ import annotations

from PyQt5.QtWidgets import QComboBox, QFormLayout, QGroupBox, QLabel, QLineEdit

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
            self._append_gpu_group()
            self._append_proxy_group()

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

        def _append_proxy_group(self):
            group = QGroupBox("网络代理")
            form = QFormLayout(group)
            form.setSpacing(12)

            self.proxy_input = QLineEdit()
            self.proxy_input.setPlaceholderText("例如 http://127.0.0.1:7890 或留空不使用代理")
            self.proxy_input.setText(self.model_settings.get("proxy_url", ""))
            form.addRow("代理地址:", self.proxy_input)

            hint = QLabel("留空则直连。支持 HTTP/HTTPS/SOCKS 代理，yt-dlp 下载视频时使用。")
            hint.setWordWrap(True)
            hint.setStyleSheet(f"font-size: 11px; color: {THEME['text_muted']};")
            form.addRow("", hint)

            layout = self.layout()
            insert_at = max(0, layout.count() - 2)
            layout.insertWidget(insert_at, group)

        def _collect_model_settings(self, create_model_dir=True):
            settings = super()._collect_model_settings(create_model_dir=create_model_dir)
            if not settings:
                return None
            settings["device"] = self.device_combo.currentData() or "auto"
            settings["compute_type"] = self.compute_combo.currentData() or "auto"
            settings["proxy_url"] = self.proxy_input.text().strip() or ""
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
            self._set_model_status(f"设置已保存。当前将使用 {runtime_device}/{runtime_compute}。正在重启服务...", "success")
            try:
                from app import restart_whisper_server
                restart_whisper_server()
            except Exception:
                pass
            self._set_model_status(f"设置已保存。当前将使用 {runtime_device}/{runtime_compute}。", "success")
            return saved

    mw.SettingsDialog = GPUSettingsDialog
