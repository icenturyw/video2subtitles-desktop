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


_TTS_PROVIDER_OPTIONS = [
    "edge-tts",
    "qwen3-tts",
    "openai-compatible",
    "volcengine-doubao",
    "sapi",
]

_QWEN3_TTS_VOICE_OPTIONS = [
    "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric",
    "Ryan", "Aiden", "Ono_Anna", "Sohee",
]

# Native language for each Qwen3-TTS preset voice.  Helps the user avoid
# voice-language mismatches that cause off-language speech and clipping.
_QWEN3_VOICE_LANG_LABELS = {
    "Vivian": "zh",
    "Uncle_Fu": "zh",
    "Serena": "en",
    "Dylan": "en",
    "Eric": "en",
    "Ryan": "en",
    "Aiden": "en",
    "Ono_Anna": "ja",
    "Sohee": "ko",
}
_QWEN3_VOICE_LANG_DISPLAY = {
    "zh": "中文",
    "en": "English",
    "ja": "日本語",
    "ko": "한국어",
}

_VOLCENGINE_DOUBAO_VOICE_OPTIONS = [
    "zh_female_tianmei_moon_bigtts",
    "zh_male_yunzhou_emo_v2_mars_bigtts",
    "zh_female_shuangkuaisisi_moon_bigtts",
    "zh_male_guozhoudege_moon_bigtts",
]

_EDGE_TTS_VOICE_OPTIONS = [
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-XiaoyiNeural",
    "en-US-JennyNeural",
    "en-US-GuyNeural",
]

_SAPI_VOICE_OPTIONS = ["default"]

_OPENAI_TTS_VOICE_OPTIONS = [
    "alloy", "ash", "ballad", "coral", "echo",
    "fable", "nova", "onyx", "sage", "shimmer",
]


def _provider_value(widget: Any) -> str:
    if hasattr(widget, "currentText"):
        return str(widget.currentText() or "").strip()
    if hasattr(widget, "text"):
        return str(widget.text() or "").strip()
    return ""


def _set_provider_value(widget: Any, value: str) -> None:
    value = str(value or "").strip()
    if hasattr(widget, "findText") and hasattr(widget, "setCurrentIndex"):
        index = widget.findText(value)
        if index >= 0:
            widget.setCurrentIndex(index)
        elif hasattr(widget, "setEditText"):
            widget.setEditText(value)
        return
    if hasattr(widget, "setText"):
        widget.setText(value)


def _voice_options_for_provider(provider: str) -> list[str]:
    provider = str(provider or "").strip().lower()
    if provider == "qwen3-tts":
        return list(_QWEN3_TTS_VOICE_OPTIONS)
    if provider == "volcengine-doubao":
        return list(_VOLCENGINE_DOUBAO_VOICE_OPTIONS)
    if provider in {"openai-compatible", "openai_compatible", "openai", "openai-tts", "openai_tts"}:
        return list(_OPENAI_TTS_VOICE_OPTIONS)
    if provider == "sapi":
        return list(_SAPI_VOICE_OPTIONS)
    return list(_EDGE_TTS_VOICE_OPTIONS)


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

        if self.preset_type == "translation":
            self.provider = QLineEdit("openai_compatible")
        else:
            self.provider = QComboBox()
            self.provider.setEditable(True)
            self.provider.addItems(_TTS_PROVIDER_OPTIONS)
            self.provider.currentTextChanged.connect(self._on_tts_provider_changed_in_editor)
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
        self.concurrency.setRange(1, 32)
        self.concurrency.setValue(4)
        self.max_batch_items = QSpinBox()
        self.max_batch_items.setRange(1, 100)
        self.max_batch_items.setValue(50)

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
        self.voice = QComboBox()
        self.voice.setEditable(True)
        self.voice.setMinimumWidth(320)
        self._sync_tts_voice_options()
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

    def _on_tts_provider_changed_in_editor(self, _provider: str):
        if self.preset_type == "tts" and hasattr(self, "voice"):
            self._sync_tts_voice_options()

    def _sync_tts_voice_options(self, selected_voice: str = ""):
        if self.preset_type != "tts" or not hasattr(self, "voice"):
            return
        provider = _provider_value(self.provider) or "edge-tts"
        current = selected_voice or (self.voice.currentData() or self.voice.currentText()).strip()
        options = _voice_options_for_provider(provider)
        if current and current not in options:
            options.insert(0, current)

        self.voice.blockSignals(True)
        self.voice.clear()
        for name in options:
            native = _QWEN3_VOICE_LANG_LABELS.get(name, "")
            if provider == "qwen3-tts" and native:
                display = _QWEN3_VOICE_LANG_DISPLAY.get(native, native)
                self.voice.addItem(f"{name} ({display})", name)
            else:
                self.voice.addItem(name, name)
        if current:
            index = self.voice.findData(current)
            if index < 0:
                index = self.voice.findText(current)
            self.voice.setCurrentIndex(index if index >= 0 else 0)
        elif options:
            self.voice.setCurrentIndex(0)
        self.voice.blockSignals(False)

    def _load_defaults(self):
        settings = get_effective_settings()
        if self.preset_type == "translation":
            cfg = translation_config_from_settings(settings)
            self.name.setText("新的翻译配置")
            _set_provider_value(self.provider, _text(settings.get("translation_provider"), "openai_compatible") or "openai_compatible")
        else:
            cfg = tts_config_from_settings(settings)
            self.name.setText("新的 TTS 配置")
            _set_provider_value(self.provider, _text(settings.get("tts_provider"), "edge-tts") or "edge-tts")
        self._load_config(cfg)

    def _load_preset(self, preset: ProviderPreset):
        self.name.setText(preset.name)
        _set_provider_value(self.provider, preset.provider)
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
            self.concurrency.setValue(int(cfg.get("concurrency", 4) or 4))
            self.max_batch_items.setValue(int(cfg.get("maxBatchItems") or cfg.get("max_batch_items") or 50))
        else:
            self._sync_tts_voice_options(_text(cfg.get("voice")))
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
                "voice": (self.voice.currentData() or self.voice.currentText()).strip(),
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
            provider=_provider_value(self.provider) or ("openai_compatible" if self.preset_type == "translation" else "edge-tts"),
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
            ("测试连接", self._test_current_connection),
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

    def _test_current_connection(self):
        preset = self._find_current()
        if not preset:
            return
        import time
        import logging
        logger = logging.getLogger("provider_presets_dialog")

        preset.lastTestStatus = "unknown"
        preset.lastTestMessage = "测试中..."
        upsert_provider_preset(preset)
        self._reload()

        start = time.time()
        success = False
        message = ""

        try:
            if preset.type == "translation":
                success, message = self._test_translation_provider(preset)
            else:
                success, message = self._test_tts_provider(preset)
        except Exception as e:
            elapsed = time.time() - start
            message = f"{e}"[:200]
            logger.warning("Connection test failed in %.1fs: %s", elapsed, message)

        preset.lastTestStatus = "success" if success else "failed"
        preset.lastTestMessage = message
        upsert_provider_preset(preset)
        self._reload()

        if success:
            QMessageBox.information(self, "测试连接", f"连接成功 ({message})")
        else:
            QMessageBox.warning(self, "测试连接", f"连接失败：{message}")

    @staticmethod
    def _test_translation_provider(preset: ProviderPreset):
        base_url = (preset.config.get("base_url", "") or "").strip().rstrip("/")
        api_key = preset.config.get("api_key", "") or ""
        if not base_url:
            base_url = preset.config.get("baseUrl", "") or ""
        if not base_url:
            return False, "未设置 API 地址"

        # Try common health/status endpoints
        import urllib.request
        import json as _json

        model = (preset.config.get("model", "") or "").strip()
        endpoints_to_try = ["/health", "/v1/models", "/models"]
        if model:
            endpoints_to_try.insert(0, "/v1/chat/completions")

        last_err = ""
        for ep in endpoints_to_try:
            url = base_url + ep
            try:
                req = urllib.request.Request(url, method="GET")
                req.add_header("User-Agent", "Video2Subtitles/1.0")
                if api_key:
                    req.add_header("Authorization", f"Bearer {api_key}")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status < 400:
                        return True, f"HTTP {resp.status}, {ep}"
                last_err = f"HTTP {resp.status}"
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    return True, "服务响应，API Key 验证通过" if api_key else "服务响应，未设置 API Key"
                if e.code == 404:
                    last_err = f"{e.code} {ep}"
                    continue
                last_err = f"HTTP {e.code}"
            except urllib.error.URLError as e:
                last_err = f"无法连接: {e.reason}"
                break
            except Exception as e:
                last_err = str(e)[:100]
        return False, last_err

    @staticmethod
    def _test_tts_provider(preset: ProviderPreset):
        provider_name = (preset.config.get("provider", "") or "").strip().lower()
        base_url = (preset.config.get("base_url", "") or "").strip().rstrip("/")
        api_key = preset.config.get("api_key", "") or ""

        if provider_name in ("edge-tts",):
            try:
                import edge_tts
                import asyncio
                voices = asyncio.run(edge_tts.list_voices())
                count = len(voices) if voices else 0
                return True, f"Edge-TTS 已安装，{count} 个可用音色"
            except ImportError:
                return False, "Edge-TTS 未安装 (pip install edge-tts)"
            except Exception as e:
                return False, str(e)[:200]
        elif provider_name in ("sapi", "windows-sapi", "windows_sapi"):
            import os
            if os.name == "nt":
                return True, "Windows SAPI 可用"
            return False, "SAPI 仅支持 Windows"
        elif provider_name in ("qwen3-tts", "qwen3_tts", "qwen3"):
            import urllib.request
            try:
                with urllib.request.urlopen("http://127.0.0.1:8767/health", timeout=3) as resp:
                    data = resp.read().decode("utf-8")
                    import json
                    info = json.loads(data)
                    model = info.get("model", info.get("loaded_model", "未知"))
                    device = info.get("device", "未知")
                    return True, f"Qwen3-TTS 就绪 ({model}, {device})"
            except Exception as e:
                return False, f"Qwen3-TTS 未运行: {e}"
        elif provider_name in ("openai-compatible", "openai_compatible", "openai", "openai-tts", "openai_tts"):
            if not base_url:
                return False, "未设置 API 地址"
            import urllib.request
            try:
                url = base_url.rstrip("/") + "/health"
                req = urllib.request.Request(url)
                if api_key:
                    req.add_header("Authorization", f"Bearer {api_key}")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return True, f"HTTP {resp.status}"
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return True, "服务可连接 (health 端点未实现)"
                return True, f"服务响应 HTTP {e.code}"
            except Exception as e:
                return False, str(e)[:200]
        elif provider_name in ("volcengine-doubao", "volcengine", "volcano", "doubao-tts", "doubao"):
            endpoint = (preset.config.get("endpoint", "") or "").strip()
            if not endpoint:
                return False, "未设置 API 端点"
            import urllib.request
            try:
                with urllib.request.urlopen(endpoint, timeout=5) as resp:
                    return True, f"HTTP {resp.status}"
            except urllib.error.HTTPError as e:
                return True, f"服务可连接 HTTP {e.code}"
            except Exception as e:
                return False, str(e)[:200]
        else:
            return False, f"不支持的提供者: {provider_name}"
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
