"""Provider preset management dialogs."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSpinBox, QDoubleSpinBox, QTabWidget, QVBoxLayout,
    QWidget,
)

from provider_presets import (
    ProviderPreset,
    delete_provider_preset,
    duplicate_provider_preset,
    export_presets,
    get_default_provider_preset,
    import_presets,
    load_provider_presets,
    set_default_provider_preset,
    tts_config_from_settings,
    translation_config_from_settings,
    upsert_provider_preset,
)
from client_settings import get_effective_settings


_THEME = {
    "bg_dark": "#1a1b2e",
    "bg_light": "#2d2f54",
    "bg_card": "#363870",
    "accent": "#7c6ff0",
    "text_primary": "#e8eaff",
    "text_secondary": "#9a9cc0",
    "text_muted": "#6b6d92",
    "border": "#3d3f6b",
    "success": "#4ade80",
    "error": "#f87171",
}


_STYLE = f"""
QDialog {{ background-color: {_THEME["bg_dark"]}; }}
QLabel {{ color: {_THEME["text_secondary"]}; font-size: 12px; }}
QListWidget {{
    background-color: {_THEME["bg_light"]};
    border: 1px solid {_THEME["border"]};
    border-radius: 8px;
    color: {_THEME["text_primary"]};
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {_THEME["bg_light"]};
    border: 1px solid {_THEME["border"]};
    border-radius: 6px;
    padding: 6px 10px;
    color: {_THEME["text_primary"]};
}}
QGroupBox {{
    border: 1px solid {_THEME["border"]};
    border-radius: 8px;
    margin-top: 12px;
    padding: 14px 10px 10px 10px;
    color: {_THEME["text_primary"]};
}}
QGroupBox::title {{ color: {_THEME["accent"]}; padding: 0 6px; }}
QCheckBox {{ color: {_THEME["text_primary"]}; }}
"""


def _text(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


class ProviderPresetEditDialog(QDialog):
    def __init__(self, preset_type: str, preset: Optional[ProviderPreset] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑服务配置" if preset else "新增服务配置")
        self.setMinimumWidth(560)
        self.setStyleSheet(_STYLE)
        self.preset = preset
        self.preset_type = preset_type
        self._setup_ui()
        if preset:
            self._load_preset(preset)
        else:
            self._load_defaults()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.name = QLineEdit()
        form.addRow("配置名称:", self.name)

        self.provider = QLineEdit("openai_compatible" if self.preset_type == "translation" else "edge-tts")
        form.addRow("服务商:", self.provider)

        self.enabled = QCheckBox("启用")
        self.enabled.setChecked(True)
        form.addRow("状态:", self.enabled)

        self.is_default = QCheckBox("设为默认配置")
        form.addRow("默认:", self.is_default)
        layout.addLayout(form)

        if self.preset_type == "translation":
            self._build_translation_fields(layout)
        else:
            self._build_tts_fields(layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_translation_fields(self, layout: QVBoxLayout):
        group = QGroupBox("翻译参数")
        form = QFormLayout(group)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.base_url = QLineEdit()
        self.model = QLineEdit()
        self.source_language = QLineEdit("auto")
        self.target_language = QLineEdit("zh-CN")
        self.api_type = QComboBox()
        self.api_type.addItems(["auto", "responses", "chat_completions", "anthropic_messages"])
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(0.3)
        self.timeout = QSpinBox()
        self.timeout.setRange(10, 300)
        self.timeout.setValue(60)
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 16)
        self.concurrency.setValue(2)
        self.max_batch_items = QSpinBox()
        self.max_batch_items.setRange(1, 100)
        self.max_batch_items.setValue(10)

        form.addRow("API Key:", self.api_key)
        form.addRow("API 地址:", self.base_url)
        form.addRow("模型:", self.model)
        form.addRow("源语言:", self.source_language)
        form.addRow("目标语言:", self.target_language)
        form.addRow("API 协议:", self.api_type)
        form.addRow("Temperature:", self.temperature)
        form.addRow("超时(秒):", self.timeout)
        form.addRow("并发:", self.concurrency)
        form.addRow("每批条数:", self.max_batch_items)
        layout.addWidget(group)

    def _build_tts_fields(self, layout: QVBoxLayout):
        group = QGroupBox("TTS 参数")
        form = QFormLayout(group)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.base_url = QLineEdit()
        self.model = QLineEdit()
        self.voice = QLineEdit()
        self.speed = QDoubleSpinBox()
        self.speed.setRange(-100.0, 4.0)
        self.speed.setSingleStep(0.1)
        self.speed.setValue(1.0)
        self.volume = QSpinBox()
        self.volume.setRange(-100, 100)
        self.volume.setValue(0)
        self.format = QComboBox()
        self.format.addItems(["mp3", "wav"])
        self.sample_rate = QSpinBox()
        self.sample_rate.setRange(8000, 48000)
        self.sample_rate.setSingleStep(1000)
        self.sample_rate.setValue(24000)
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 8)
        self.concurrency.setValue(1)
        self.min_gap = QSpinBox()
        self.min_gap.setRange(0, 5000)
        self.min_gap.setValue(120)
        self.max_stretch = QDoubleSpinBox()
        self.max_stretch.setRange(1.0, 3.0)
        self.max_stretch.setSingleStep(0.05)
        self.max_stretch.setValue(1.15)
        self.duration_align = QCheckBox("启用时长对齐")
        self.duration_align.setChecked(True)
        self.consistency_mode = QComboBox()
        self.consistency_mode.addItems(["stable", "fast", "strict"])

        form.addRow("API Key:", self.api_key)
        form.addRow("API 地址:", self.base_url)
        form.addRow("模型:", self.model)
        form.addRow("音色:", self.voice)
        form.addRow("速度:", self.speed)
        form.addRow("音量:", self.volume)
        form.addRow("格式:", self.format)
        form.addRow("采样率:", self.sample_rate)
        form.addRow("并发:", self.concurrency)
        form.addRow("句间隔(ms):", self.min_gap)
        form.addRow("最大拉伸比:", self.max_stretch)
        form.addRow("时长对齐:", self.duration_align)
        form.addRow("一致性模式:", self.consistency_mode)
        layout.addWidget(group)

    def _load_defaults(self):
        settings = get_effective_settings()
        if self.preset_type == "translation":
            cfg = translation_config_from_settings(settings)
            self.name.setText("新的翻译配置")
            self.provider.setText(_text(settings.get("translation_provider"), "openai_compatible") or "openai_compatible")
        else:
            cfg = tts_config_from_settings(settings)
            self.name.setText("新的 TTS 配置")
            self.provider.setText(_text(settings.get("tts_provider"), "edge-tts") or "edge-tts")
        self._load_config(cfg)

    def _load_preset(self, preset: ProviderPreset):
        self.name.setText(preset.name)
        self.provider.setText(preset.provider)
        self.enabled.setChecked(preset.enabled)
        self.is_default.setChecked(preset.isDefault)
        self._load_config(preset.config)

    def _load_config(self, cfg: Dict[str, Any]):
        self.api_key.setText(_text(cfg.get("apiKey") or cfg.get("api_key")))
        self.base_url.setText(_text(cfg.get("baseUrl") or cfg.get("base_url")))
        self.model.setText(_text(cfg.get("model")))
        if self.preset_type == "translation":
            self.source_language.setText(_text(cfg.get("sourceLanguage") or cfg.get("source_language"), "auto"))
            self.target_language.setText(_text(cfg.get("targetLanguage") or cfg.get("target_language"), "zh-CN"))
            idx = self.api_type.findText(_text(cfg.get("apiType") or cfg.get("api_type"), "auto"))
            self.api_type.setCurrentIndex(idx if idx >= 0 else 0)
            self.temperature.setValue(float(cfg.get("temperature", 0.3) or 0.3))
            self.timeout.setValue(int(cfg.get("timeout", 60) or 60))
            self.concurrency.setValue(int(cfg.get("concurrency", 2) or 2))
            self.max_batch_items.setValue(int(cfg.get("maxBatchItems") or cfg.get("max_batch_items") or 10))
        else:
            self.voice.setText(_text(cfg.get("voice")))
            self.speed.setValue(float(cfg.get("speed", 1.0) or 1.0))
            self.volume.setValue(int(cfg.get("volume", 0) or 0))
            idx = self.format.findText(_text(cfg.get("format"), "mp3"))
            self.format.setCurrentIndex(idx if idx >= 0 else 0)
            self.sample_rate.setValue(int(cfg.get("sampleRate") or cfg.get("sample_rate") or 24000))
            self.concurrency.setValue(int(cfg.get("concurrency", 1) or 1))
            self.min_gap.setValue(int(cfg.get("minSentenceGapMs") or cfg.get("min_sentence_gap_ms") or 120))
            self.max_stretch.setValue(float(cfg.get("maxAudioStretchRatio") or cfg.get("max_audio_stretch_ratio") or 1.15))
            self.duration_align.setChecked(bool(cfg.get("enableDurationAlign", True)))
            idx = self.consistency_mode.findText(_text(cfg.get("consistencyMode") or cfg.get("consistency_mode"), "stable"))
            self.consistency_mode.setCurrentIndex(idx if idx >= 0 else 0)

    def to_preset(self) -> ProviderPreset:
        from provider_presets import _now, _new_id  # local import keeps public surface small

        now = _now()
        if self.preset_type == "translation":
            cfg = {
                "apiKey": self.api_key.text().strip(),
                "baseUrl": self.base_url.text().strip(),
                "model": self.model.text().strip(),
                "sourceLanguage": self.source_language.text().strip() or "auto",
                "targetLanguage": self.target_language.text().strip() or "zh-CN",
                "apiType": self.api_type.currentText(),
                "temperature": self.temperature.value(),
                "timeout": self.timeout.value(),
                "concurrency": self.concurrency.value(),
                "maxBatchItems": self.max_batch_items.value(),
            }
        else:
            cfg = {
                "apiKey": self.api_key.text().strip(),
                "baseUrl": self.base_url.text().strip(),
                "model": self.model.text().strip(),
                "voice": self.voice.text().strip(),
                "speed": self.speed.value(),
                "volume": self.volume.value(),
                "format": self.format.currentText(),
                "sampleRate": self.sample_rate.value(),
                "concurrency": self.concurrency.value(),
                "minSentenceGapMs": self.min_gap.value(),
                "maxAudioStretchRatio": self.max_stretch.value(),
                "enableDurationAlign": self.duration_align.isChecked(),
                "consistencyMode": self.consistency_mode.currentText(),
            }
        return ProviderPreset(
            id=self.preset.id if self.preset else _new_id(),
            type=self.preset_type,  # type: ignore[arg-type]
            name=self.name.text().strip() or "未命名配置",
            provider=self.provider.text().strip() or ("openai_compatible" if self.preset_type == "translation" else "edge-tts"),
            enabled=self.enabled.isChecked(),
            isDefault=self.is_default.isChecked(),
            createdAt=self.preset.createdAt if self.preset else now,
            updatedAt=now,
            config=cfg,
            lastTestStatus=self.preset.lastTestStatus if self.preset else "unknown",
            lastTestAt=self.preset.lastTestAt if self.preset else "",
            lastTestMessage=self.preset.lastTestMessage if self.preset else "",
        )


class ProviderPresetsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("服务配置方案")
        self.setMinimumSize(780, 560)
        self.resize(860, 620)
        self.setStyleSheet(_STYLE)
        self._setup_ui()
        self._reload()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        title = QLabel("服务配置方案")
        title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {_THEME['text_primary']};")
        layout.addWidget(title)

        self.tabs = QTabWidget()
        self.translation_list = QListWidget()
        self.tts_list = QListWidget()
        self.tabs.addTab(self.translation_list, "翻译服务")
        self.tabs.addTab(self.tts_list, "TTS 配音服务")
        self.translation_list.itemDoubleClicked.connect(lambda _: self._edit_current())
        self.tts_list.itemDoubleClicked.connect(lambda _: self._edit_current())
        layout.addWidget(self.tabs, 1)

        toolbar = QHBoxLayout()
        for text, callback in [
            ("新增", self._add_current_type),
            ("编辑", self._edit_current),
            ("复制", self._duplicate_current),
            ("启用/禁用", self._toggle_current),
            ("设为默认", self._set_default_current),
            ("测试连接", self._mark_test_placeholder),
            ("删除", self._delete_current),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(callback)
            toolbar.addWidget(btn)
        layout.addLayout(toolbar)

        import_export = QHBoxLayout()
        import_export.addStretch()
        export_btn = QPushButton("导出配置")
        export_btn.clicked.connect(self._export)
        import_export.addWidget(export_btn)
        import_btn = QPushButton("导入配置")
        import_btn.clicked.connect(self._import)
        import_export.addWidget(import_btn)
        layout.addLayout(import_export)

        close_btn = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn.rejected.connect(self.reject)
        layout.addWidget(close_btn)

    def _current_type(self) -> str:
        return "translation" if self.tabs.currentIndex() == 0 else "tts"

    def _current_list(self) -> QListWidget:
        return self.translation_list if self._current_type() == "translation" else self.tts_list

    def _current_preset_id(self) -> str:
        item = self._current_list().currentItem()
        return str(item.data(Qt.UserRole) or "") if item else ""

    def _reload(self):
        presets = load_provider_presets()
        self.translation_list.clear()
        self.tts_list.clear()
        for preset_type, widget in (("translation", self.translation_list), ("tts", self.tts_list)):
            typed = [p for p in presets if p.type == preset_type]
            if not typed:
                item = QListWidgetItem("暂无配置，请点击「新增」")
                item.setFlags(Qt.NoItemFlags)
                widget.addItem(item)
                continue
            for preset in typed:
                status = "启用" if preset.enabled else "禁用"
                default = " · 默认" if preset.isDefault else ""
                test = {"success": "成功", "failed": "失败", "unknown": "未测试"}.get(preset.lastTestStatus, "未测试")
                model = _text(preset.config.get("model"), "未设置")
                item = QListWidgetItem(
                    f"{preset.name}{default}\n{preset.provider} · {model} · {status} · 测试: {test}"
                )
                item.setData(Qt.UserRole, preset.id)
                widget.addItem(item)

    def _find_current(self) -> Optional[ProviderPreset]:
        preset_id = self._current_preset_id()
        return next((p for p in load_provider_presets() if p.id == preset_id), None)

    def _add_current_type(self):
        dlg = ProviderPresetEditDialog(self._current_type(), parent=self)
        if dlg.exec_():
            upsert_provider_preset(dlg.to_preset())
            self._reload()

    def _edit_current(self):
        preset = self._find_current()
        if not preset:
            return
        dlg = ProviderPresetEditDialog(preset.type, preset, self)
        if dlg.exec_():
            upsert_provider_preset(dlg.to_preset())
            self._reload()

    def _duplicate_current(self):
        preset_id = self._current_preset_id()
        if preset_id:
            duplicate_provider_preset(preset_id)
            self._reload()

    def _toggle_current(self):
        preset = self._find_current()
        if not preset:
            return
        preset.enabled = not preset.enabled
        if not preset.enabled:
            preset.isDefault = False
        upsert_provider_preset(preset)
        self._reload()

    def _set_default_current(self):
        preset_id = self._current_preset_id()
        if preset_id:
            set_default_provider_preset(preset_id)
            self._reload()

    def _mark_test_placeholder(self):
        preset = self._find_current()
        if not preset:
            return
        preset.lastTestStatus = "unknown"
        preset.lastTestMessage = "测试连接能力后续增强"
        upsert_provider_preset(preset)
        QMessageBox.information(self, "测试连接", "已保留测试状态字段；真实连接测试将在后续版本增强。")
        self._reload()

    def _delete_current(self):
        preset = self._find_current()
        if not preset:
            return
        if QMessageBox.question(self, "删除配置", f"确定删除「{preset.name}」吗？") == QMessageBox.Yes:
            delete_provider_preset(preset.id)
            self._reload()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出服务配置", "provider-presets.json", "JSON Files (*.json)")
        if path:
            export_presets(Path(path))
            QMessageBox.information(self, "导出完成", "配置已导出，API Key 已脱敏。")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入服务配置", "", "JSON Files (*.json);;All Files (*)")
        if not path:
            return
        try:
            count = import_presets(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        QMessageBox.information(self, "导入完成", f"已导入 {count} 个配置。")
        self._reload()


def ensure_default_preset_label(preset_type: str) -> str:
    preset = get_default_provider_preset(preset_type)  # type: ignore[arg-type]
    return preset.name if preset else ""
