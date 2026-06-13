"""Localization settings dialog for translation and rendering configuration."""
from __future__ import annotations

import os
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
    QVBoxLayout,
)

from client_settings import get_effective_settings, save_settings
from job_models import SubtitleStyle, TranslationConfig

try:
    import edge_tts
    _EDGE_TTS_AVAILABLE = True
except ImportError:
    _EDGE_TTS_AVAILABLE = False

_THEME = {
    "bg_dark": "#1a1b2e",
    "bg_medium": "#232540",
    "bg_light": "#2d2f54",
    "bg_card": "#363870",
    "accent": "#7c6ff0",
    "text_primary": "#e8eaff",
    "text_secondary": "#9a9cc0",
    "text_muted": "#6b6d92",
    "border": "#3d3f6b",
}

_STYLE_SHEET = f"""
QDialog {{ background-color: {_THEME["bg_dark"]}; }}
QGroupBox {{
    border: 1px solid {_THEME["border"]};
    border-radius: 10px;
    margin-top: 14px;
    padding: 16px 12px 10px 12px;
    font-weight: 600;
    color: {_THEME["text_primary"]};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 8px;
    color: {_THEME["accent"]};
}}
QLabel {{ color: {_THEME["text_secondary"]}; font-size: 12px; }}
QLineEdit, QSpinBox, QComboBox {{
    background-color: {_THEME["bg_light"]};
    border: 1px solid {_THEME["border"]};
    border-radius: 6px;
    padding: 6px 10px;
    color: {_THEME["text_primary"]};
    font-size: 12px;
}}
QCheckBox {{ color: {_THEME["text_primary"]}; spacing: 6px; }}
"""


class LocalizationDialog(QDialog):
    """Settings dialog for translation and subtitle rendering options."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("本地化设置")
        self.setMinimumSize(520, 600)
        self.setStyleSheet(_STYLE_SHEET)
        self._settings = get_effective_settings()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        title = QLabel("本地化处理设置")
        title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {_THEME['text_primary']};")
        layout.addWidget(title)

        # Mode selection
        mode_group = QGroupBox("处理模式")
        mode_layout = QHBoxLayout(mode_group)

        self.mode_subtitle = QCheckBox("快速字幕（现有流程）")
        self.mode_subtitle.setChecked(True)

        self.mode_translate = QCheckBox("翻译字幕成片")
        self.mode_translate.setChecked(False)

        self.mode_dub = QCheckBox("指定语言配音")
        self.mode_dub.setChecked(False)

        mode_layout.addWidget(self.mode_subtitle)
        mode_layout.addWidget(self.mode_translate)
        mode_layout.addWidget(self.mode_dub)
        layout.addWidget(mode_group)

        self.mode_translate.toggled.connect(self._on_mode_changed)
        self.mode_dub.toggled.connect(self._on_mode_changed)

        # Translation settings
        self.trans_group = QGroupBox("翻译设置")
        trans_form = QFormLayout(self.trans_group)
        trans_form.setSpacing(8)
        self.trans_group.setEnabled(False)

        self.source_lang = QComboBox()
        self.source_lang.addItems(["auto (自动检测)", "en (英文)", "zh (中文)",
                                    "ja (日文)", "ko (韩文)", "fr (法文)", "de (德文)"])
        trans_form.addRow("源语言:", self.source_lang)

        self.target_lang = QComboBox()
        self.target_lang.addItems(["zh-CN (简体中文)", "en (英文)", "ja (日文)",
                                    "ko (韩文)", "fr (法文)", "de (德文)", "es (西班牙文)"])
        trans_form.addRow("目标语言:", self.target_lang)

        self.trans_provider = QComboBox()
        self.trans_provider.addItems(["openai_compatible", "custom"])
        trans_form.addRow("翻译服务:", self.trans_provider)

        self.trans_base_url = QLineEdit(
            self._settings.get("translation_base_url", "https://api.openai.com/v1")
        )
        trans_form.addRow("API 地址:", self.trans_base_url)

        self.trans_model = QLineEdit(
            self._settings.get("translation_model", "gpt-4o-mini")
        )
        trans_form.addRow("模型:", self.trans_model)

        # API key is loaded from env, show masked
        api_key = os.environ.get("V2S_TRANSLATION_API_KEY", "")
        self.trans_api_key = QLineEdit(api_key)
        self.trans_api_key.setEchoMode(QLineEdit.Password)
        self.trans_api_key.setPlaceholderText("设置 V2S_TRANSLATION_API_KEY 环境变量")
        trans_form.addRow("API Key:", self.trans_api_key)

        self.trans_timeout = QSpinBox()
        self.trans_timeout.setRange(10, 300)
        self.trans_timeout.setValue(int(self._settings.get("translation_timeout", 60)))
        trans_form.addRow("超时(秒):", self.trans_timeout)

        layout.addWidget(self.trans_group)

        # TTS / Dubbing settings
        self.tts_group = QGroupBox("配音设置")
        tts_form = QFormLayout(self.tts_group)
        tts_form.setSpacing(8)
        self.tts_group.setEnabled(False)

        self.tts_provider = QComboBox()
        self.tts_provider.addItems(["edge-tts"])
        if not _EDGE_TTS_AVAILABLE:
            self.tts_provider.setItemData(0, "需要安装: pip install edge-tts")
        tts_form.addRow("TTS 服务:", self.tts_provider)

        self.tts_voice_label = QLabel("zh-CN-XiaoxiaoNeural")
        self.tts_voice_label.setStyleSheet(f"color: {_THEME['text_primary']}; font-size: 12px;")
        tts_form.addRow("音色:", self.tts_voice_label)

        self.orig_volume = QSpinBox()
        self.orig_volume.setRange(0, 100)
        self.orig_volume.setValue(30)
        self.orig_volume.setSuffix(" %")
        tts_form.addRow("原声保留:", self.orig_volume)

        layout.addWidget(self.tts_group)

        # Subtitle output settings
        sub_group = QGroupBox("字幕输出")
        sub_form = QFormLayout(sub_group)
        sub_form.setSpacing(8)

        self.subtitle_mode = QComboBox()
        self.subtitle_mode.addItems(["bilingual (双语)", "translated (仅译文)", "source (仅原文)"])
        sub_form.addRow("字幕模式:", self.subtitle_mode)

        self.export_srt = QCheckBox("导出 SRT")
        self.export_srt.setChecked(True)
        sub_form.addRow("", self.export_srt)

        self.export_ass = QCheckBox("导出 ASS（支持样式）")
        self.export_ass.setChecked(True)
        sub_form.addRow("", self.export_ass)

        layout.addWidget(sub_group)

        # Rendering settings
        render_group = QGroupBox("视频渲染")
        render_form = QFormLayout(render_group)
        render_form.setSpacing(8)

        self.burn_subtitles = QCheckBox("烧录硬字幕到视频")
        self.burn_subtitles.setChecked(True)
        render_form.addRow("", self.burn_subtitles)

        self.embed_soft = QCheckBox("同时封装软字幕")
        self.embed_soft.setChecked(False)
        render_form.addRow("", self.embed_soft)

        layout.addWidget(render_group)

        layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_mode_changed(self, checked: bool):
        trans = self.mode_translate.isChecked()
        dub = self.mode_dub.isChecked()
        self.trans_group.setEnabled(trans or dub)
        self.tts_group.setEnabled(dub)
        if trans or dub:
            self.mode_subtitle.setChecked(False)
        else:
            self.mode_subtitle.setChecked(True)

    def _on_accept(self):
        # Save settings
        self._settings["translation_base_url"] = self.trans_base_url.text().strip()
        self._settings["translation_model"] = self.trans_model.text().strip()
        self._settings["translation_timeout"] = str(self.trans_timeout.value())
        save_settings(self._settings)

        # Set env var for API key
        key = self.trans_api_key.text().strip()
        if key:
            os.environ["V2S_TRANSLATION_API_KEY"] = key

        self.accept()

    @property
    def is_translate_mode(self) -> bool:
        return self.mode_translate.isChecked()

    @property
    def is_dub_mode(self) -> bool:
        return self.mode_dub.isChecked()

    @property
    def source_language(self) -> str:
        text = self.source_lang.currentText()
        if text.startswith("auto"):
            return "auto"
        return text.split("(")[1].rstrip(")").strip() if "(" in text else "en"

    @property
    def target_language(self) -> str:
        text = self.target_lang.currentText()
        return text.split("(")[1].rstrip(")").strip() if "(" in text else "zh-CN"

    @property
    def subtitle_mode_value(self) -> str:
        text = self.subtitle_mode.currentText()
        if "bilingual" in text:
            return "bilingual"
        if "translated" in text:
            return "translated"
        return "source"

    def get_translation_config(self) -> TranslationConfig:
        return TranslationConfig(
            provider=self.trans_provider.currentText(),
            base_url=self.trans_base_url.text().strip(),
            model=self.trans_model.text().strip(),
            api_key_env="V2S_TRANSLATION_API_KEY",
            timeout=self.trans_timeout.value(),
        )

    def get_settings(self) -> dict:
        tc = self.get_translation_config()
        return {
            "is_translate_mode": self.is_translate_mode or self.is_dub_mode,
            "is_dub_mode": self.is_dub_mode,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "subtitle_mode": self.subtitle_mode_value,
            "export_srt": self.export_srt.isChecked(),
            "export_ass": self.export_ass.isChecked(),
            "burn_subtitles": self.burn_subtitles.isChecked(),
            "embed_soft_subtitles": self.embed_soft.isChecked(),
            "dubbing_enabled": self.is_dub_mode,
            "tts_provider": self.tts_provider.currentText(),
            "tts_voice": self.tts_voice_label.text(),
            "original_volume": self.orig_volume.value() / 100.0,
            "translation_config": {
                "provider": tc.provider,
                "base_url": tc.base_url,
                "model": tc.model,
                "api_key_env": tc.api_key_env,
                "timeout": tc.timeout,
            },
        }
