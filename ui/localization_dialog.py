"""Localization settings dialog for translation and rendering configuration."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QDoubleSpinBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QRadioButton,
    QVBoxLayout, QWidget,
)
from PyQt5.QtWidgets import QButtonGroup

from client_settings import apply_settings_to_env, get_effective_settings, save_settings
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
QCheckBox, QRadioButton {{ color: {_THEME["text_primary"]}; spacing: 6px; }}
"""

_EDGE_DEFAULT_VOICES = {
    "zh": "zh-CN-XiaoxiaoNeural",
    "zh-cn": "zh-CN-XiaoxiaoNeural",
    "zh-tw": "zh-TW-HsiaoChenNeural",
    "en": "en-US-JennyNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "es": "es-ES-ElviraNeural",
}

_QWEN_DEFAULT_VOICES = [
    "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric",
    "Ryan", "Aiden", "Ono_Anna", "Sohee",
]
_QWEN_DEFAULT_STABLE_SEED = 42

_PREVIEW_TEXTS = {
    "zh": "你好，这是当前配音音色的试听。",
    "en": "Hello, this is a preview of the selected dubbing voice.",
    "ja": "こんにちは。これは選択した音声のプレビューです。",
    "ko": "안녕하세요. 선택한 음색의 미리 듣기입니다.",
    "fr": "Bonjour, voici un aperçu de la voix sélectionnée.",
    "de": "Hallo, dies ist eine Vorschau der ausgewählten Stimme.",
    "es": "Hola, esta es una vista previa de la voz seleccionada.",
}


def _language_code(text: str, default: str) -> str:
    """Return the language code prefix from combo text like 'zh-CN (简体中文)'."""
    code = str(text or "").split("(", 1)[0].strip()
    return code or default


def _provider_key(text: str) -> str:
    value = str(text or "").lower()
    if "qwen3-tts" in value:
        return "qwen3-tts"
    if "sapi" in value or "windows" in value:
        return "sapi"
    return "edge-tts"


def _api_type_key(text: str) -> str:
    value = str(text or "").strip().lower()
    if value.startswith("openai responses") or value in {"responses", "response"}:
        return "responses"
    if value.startswith("openai chat") or value in {"chat", "chat_completions", "chat-completions"}:
        return "chat_completions"
    if value.startswith("anthropic") or value in {"anthropic", "anthropic_messages", "messages", "claude"}:
        return "anthropic_messages"
    return "auto"


def _api_type_label(value: str) -> str:
    key = _api_type_key(value)
    labels = {
        "auto": "Auto",
        "responses": "OpenAI Responses",
        "chat_completions": "OpenAI Chat Completions",
        "anthropic_messages": "Anthropic Messages",
    }
    return labels.get(key, "Auto")


def _bool_setting(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _percent_setting(value: object, default: int = 0) -> int:
    try:
        percent = int(str(value if value is not None else default).strip())
    except (TypeError, ValueError):
        percent = default
    return max(0, min(100, percent))


def _language_base(language: str) -> str:
    return str(language or "").strip().lower().split("-", 1)[0] or "zh"


def _default_tts_voice(provider: str, language: str) -> str:
    if provider == "qwen3-tts":
        return _QWEN_DEFAULT_VOICES[0]
    if provider == "sapi":
        return "default"
    lower = str(language or "").strip().lower()
    return _EDGE_DEFAULT_VOICES.get(
        lower,
        _EDGE_DEFAULT_VOICES.get(_language_base(lower), "zh-CN-XiaoxiaoNeural"),
    )


def _private_voice_ref_dir() -> Path:
    root = Path(__file__).resolve().parent.parent / ".cache" / "qwen3-tts" / "voice_refs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _copy_voice_ref(path_text: str) -> str:
    source = Path(str(path_text or "").strip())
    if not source.exists() or not source.is_file():
        return str(path_text or "").strip()
    dst = _private_voice_ref_dir() / source.name
    if dst.exists() and dst.resolve() != source.resolve():
        dst = _private_voice_ref_dir() / f"{source.stem}_{abs(hash(str(source.resolve()))) & 0xffff:x}{source.suffix}"
    if dst.resolve() != source.resolve():
        shutil.copy2(str(source), str(dst))
    return str(dst)


def _preview_text(language: str) -> str:
    return _PREVIEW_TEXTS.get(_language_base(language), _PREVIEW_TEXTS["zh"])


def _format_voice_label(voice: dict) -> str:
    name = str(voice.get("name") or "").strip()
    locale = str(voice.get("locale") or voice.get("language") or "").strip()
    gender = str(voice.get("gender") or "").strip()
    parts = [p for p in (locale, gender) if p]
    return f"{name} ({' 路 '.join(parts)})" if parts else name


def localization_runtime_config(settings: dict) -> dict | None:
    """Convert persisted localization settings into the config used by workers."""
    mode = str(settings.get("localization_mode", "subtitle") or "subtitle").strip()
    if mode == "subtitle":
        has_translation_config = bool(
            str(settings.get("translation_base_url", "") or "").strip()
            and (
                str(settings.get("translation_api_key", "") or "").strip()
                or os.environ.get("V2S_TRANSLATION_API_KEY", "").strip()
            )
        )
        if has_translation_config:
            mode = "translate"
    if mode not in {"translate", "dub"}:
        return None

    target_language = _language_code(
        settings.get("target_language_dialog", settings.get("default_target_language", "zh-CN")),
        settings.get("default_target_language", "zh-CN") or "zh-CN",
    )
    source_language = _language_code(
        settings.get("source_language_dialog", "auto"),
        "auto",
    )
    subtitle_mode = settings.get("subtitle_mode_dialog", "bilingual")
    if "translated" in subtitle_mode:
        subtitle_mode_value = "translated"
    elif "source" in subtitle_mode:
        subtitle_mode_value = "source"
    else:
        subtitle_mode_value = "bilingual"

    tts_provider = _provider_key(settings.get("tts_provider", "edge-tts"))
    consistency_mode = str(
        settings.get("tts_consistency_mode", "stable") or "stable"
    ).lower()
    if consistency_mode not in ("fast", "stable", "strict"):
        consistency_mode = "stable"

    tts_options = {
        "qwen_mode": settings.get("tts_qwen_mode", "auto") or "auto",
        "instruct": settings.get("tts_qwen_instruct", ""),
        "ref_audio": settings.get("tts_qwen_ref_audio", ""),
        "ref_text": settings.get("tts_qwen_ref_text", ""),
        "tts_segment_gap": float(settings.get("tts_segment_gap", "0.04") or 0.04),
        "tts_consistency_mode": consistency_mode,
    }
    for source_key, option_key, cast in [
        ("tts_qwen_seed", "seed", int),
        ("tts_qwen_temperature", "temperature", float),
        ("tts_qwen_top_p", "top_p", float),
        ("tts_qwen_max_new_tokens", "max_new_tokens", int),
    ]:
        raw = str(settings.get(source_key, "") or "").strip()
        if raw:
            try:
                tts_options[option_key] = cast(raw)
            except (TypeError, ValueError):
                pass

    mute_original_audio = _bool_setting(settings.get("mute_original_audio"), True)
    original_volume = (
        0.0
        if mute_original_audio
        else _percent_setting(settings.get("original_audio_volume_display"), 0) / 100.0
    )

    return {
        "is_translate_mode": True,
        "is_dub_mode": mode == "dub",
        "source_language": source_language,
        "target_language": target_language,
        "subtitle_mode": subtitle_mode_value,
        "export_srt": str(settings.get("export_srt", "true")).lower() == "true",
        "export_ass": str(settings.get("export_ass", "true")).lower() == "true",
        "burn_subtitles": str(settings.get("burn_subtitles", "true")).lower() == "true",
        "embed_soft_subtitles": str(settings.get("embed_soft_subtitles", "false")).lower() == "true",
        "dubbing_enabled": mode == "dub",
        "tts_provider": tts_provider,
        "tts_voice": settings.get("tts_voice", ""),
        "tts_concurrency": int(settings.get("tts_concurrency", "1") or 1),
        "tts_consistency_mode": consistency_mode,
        "tts_options": tts_options,
        "low_vram_mode": _bool_setting(settings.get("low_vram_mode"), True),
        "mute_original_audio": mute_original_audio,
        "original_volume": original_volume,
        "translation_config": {
            "provider": settings.get("translation_provider", "openai_compatible"),
            "base_url": settings.get("translation_base_url", ""),
            "model": settings.get("translation_model", ""),
            "api_type": _api_type_key(settings.get("translation_api_type", "auto")),
            "api_key_env": "V2S_TRANSLATION_API_KEY",
            "api_key": settings.get("translation_api_key", ""),
            "timeout": int(settings.get("translation_timeout", "60") or 60),
            "concurrency": int(settings.get("translation_concurrency", "2") or 2),
            "max_batch_items": int(settings.get("translation_max_batch_items", "10") or 10),
        },
    }


class LocalizationDialog(QDialog):
    """Settings dialog for translation and subtitle rendering options."""

    _voices_loaded = pyqtSignal(int, object, str)
    _preview_done = pyqtSignal(bool, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("本地化设置")
        self.setMinimumSize(760, 680)
        self.resize(820, 760)
        self.setStyleSheet(_STYLE_SHEET)
        self._settings = get_effective_settings()
        self._voice_request_id = 0
        self._preferred_tts_voice = self._settings.get("tts_voice", "")
        self._voices_loaded.connect(self._on_tts_voices_loaded)
        self._preview_done.connect(self._on_tts_preview_done)
        self._setup_ui()
        self._load_settings_to_ui()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 16, 20, 16)
        root_layout.setSpacing(12)

        title = QLabel("本地化处理设置")
        title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {_THEME['text_primary']};")
        root_layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: 0; background: transparent; }")
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)
        scroll.setWidget(content)
        root_layout.addWidget(scroll, 1)

        # Mode selection
        mode_group = QGroupBox("处理模式")
        mode_layout = QVBoxLayout(mode_group)
        mode_layout.setSpacing(8)

        self.mode_subtitle = QRadioButton("快速字幕（不翻译）")
        self.mode_subtitle.setChecked(True)

        self.mode_translate = QRadioButton("翻译字幕成片")
        self.mode_translate.setChecked(False)

        self.mode_dub = QRadioButton("指定语言配音")
        self.mode_dub.setChecked(False)

        mode_layout.addWidget(self.mode_subtitle)
        mode_layout.addWidget(self.mode_translate)
        mode_layout.addWidget(self.mode_dub)
        layout.addWidget(mode_group)

        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.mode_subtitle, 0)
        self._mode_group.addButton(self.mode_translate, 1)
        self._mode_group.addButton(self.mode_dub, 2)
        self._mode_group.setExclusive(True)
        self._mode_group.buttonClicked.connect(self._on_mode_changed)

        self._qwen3_status_timer = QTimer()
        self._qwen3_status_timer.timeout.connect(self._refresh_qwen3_status)
        self._qwen3_status_timer.start(5000)

        # Translation settings
        self.trans_group = QGroupBox("翻译设置")
        trans_form = QFormLayout(self.trans_group)
        trans_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        trans_form.setHorizontalSpacing(14)
        trans_form.setVerticalSpacing(10)
        trans_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.trans_group.setEnabled(True)

        self.source_lang = QComboBox()
        self.source_lang.setMinimumWidth(360)
        self.source_lang.addItems(["auto (自动检测)", "en (英文)", "zh (中文)",
                                    "ja (日文)", "ko (韩文)", "fr (法文)", "de (德文)"])
        trans_form.addRow("源语言:", self.source_lang)

        self.target_lang = QComboBox()
        self.target_lang.setMinimumWidth(360)
        self.target_lang.addItems(["zh-CN (简体中文)", "en (英文)", "ja (日文)",
                                    "ko (韩文)", "fr (法文)", "de (德文)", "es (西班牙文)"])
        self.target_lang.currentIndexChanged.connect(self._on_tts_language_changed)
        trans_form.addRow("目标语言:", self.target_lang)

        self.trans_provider = QComboBox()
        self.trans_provider.setMinimumWidth(360)
        self.trans_provider.addItems(["openai_compatible"])
        trans_form.addRow("翻译服务:", self.trans_provider)

        self.trans_base_url = QLineEdit(
            self._settings.get("translation_base_url", "https://api.openai.com/v1")
        )
        self.trans_base_url.setMinimumWidth(420)
        trans_form.addRow("API 地址:", self.trans_base_url)

        self.trans_model = QLineEdit(
            self._settings.get("translation_model", "gpt-4o-mini")
        )
        self.trans_model.setMinimumWidth(420)
        trans_form.addRow("模型:", self.trans_model)

        self.trans_api_type = QComboBox()
        self.trans_api_type.setMinimumWidth(360)
        self.trans_api_type.addItems([
            "Auto",
            "OpenAI Responses",
            "OpenAI Chat Completions",
            "Anthropic Messages",
        ])
        trans_form.addRow("API 协议:", self.trans_api_type)

        # API key: prefer saved setting, fall back to env var
        api_key = self._settings.get("translation_api_key", "") or os.environ.get("V2S_TRANSLATION_API_KEY", "")
        self.trans_api_key = QLineEdit(api_key)
        self.trans_api_key.setEchoMode(QLineEdit.Password)
        self.trans_api_key.setMinimumWidth(420)
        self.trans_api_key.setPlaceholderText("设置 V2S_TRANSLATION_API_KEY 环境变量")
        trans_form.addRow("API Key:", self.trans_api_key)

        self.trans_timeout = QSpinBox()
        self.trans_timeout.setRange(10, 300)
        self.trans_timeout.setValue(int(self._settings.get("translation_timeout", 60)))
        trans_form.addRow("超时(秒):", self.trans_timeout)

        self.trans_concurrency = QSpinBox()
        self.trans_concurrency.setRange(1, 8)
        self.trans_concurrency.setValue(int(self._settings.get("translation_concurrency", 2)))
        trans_form.addRow("并发线程数:", self.trans_concurrency)

        layout.addWidget(self.trans_group)

        # TTS / Dubbing settings
        self.tts_group = QGroupBox("配音设置")
        tts_form = QFormLayout(self.tts_group)
        tts_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        tts_form.setHorizontalSpacing(14)
        tts_form.setVerticalSpacing(10)
        tts_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.tts_group.setEnabled(False)

        self.tts_provider = QComboBox()
        self.tts_provider.setMinimumWidth(360)
        self.tts_provider.addItems([
            "edge-tts",
            "qwen3-tts（本地 Qwen3-TTS）",
            "sapi（Windows 本地 TTS）",
        ])
        if not _EDGE_TTS_AVAILABLE:
            self.tts_provider.setItemData(0, "需要安装: pip install edge-tts")
        self.tts_provider.currentIndexChanged.connect(self._on_tts_provider_changed)
        tts_form.addRow("TTS 服务:", self.tts_provider)

        self.tts_manage_btn = QPushButton("管理 Qwen3-TTS")
        self.tts_manage_btn.clicked.connect(self._open_qwen3_tts_setup)
        self.tts_manage_btn.setVisible(False)
        tts_form.addRow("", self.tts_manage_btn)

        self.tts_qwen3_status = QLabel("")
        self.tts_qwen3_status.setStyleSheet(
            f"color: {_THEME['text_muted']}; font-size: 11px;"
        )
        self.tts_qwen3_status.setVisible(False)
        tts_form.addRow("", self.tts_qwen3_status)

        voice_row = QWidget()
        voice_layout = QHBoxLayout(voice_row)
        voice_layout.setContentsMargins(0, 0, 0, 0)
        voice_layout.setSpacing(6)

        self.tts_voice = QComboBox()
        self.tts_voice.setMinimumWidth(360)
        voice_layout.addWidget(self.tts_voice, 1)

        self.tts_voice_refresh_btn = QPushButton("刷新")
        self.tts_voice_refresh_btn.clicked.connect(self._refresh_tts_voices)
        voice_layout.addWidget(self.tts_voice_refresh_btn)

        self.tts_preview_btn = QPushButton("试听")
        self.tts_preview_btn.clicked.connect(self._preview_tts_voice)
        voice_layout.addWidget(self.tts_preview_btn)
        tts_form.addRow("音色:", voice_row)

        self.tts_voice_status = QLabel("")
        self.tts_voice_status.setStyleSheet(
            f"color: {_THEME['text_muted']}; font-size: 11px;"
        )
        tts_form.addRow("", self.tts_voice_status)

        self.orig_volume = QSpinBox()
        self.orig_volume.setRange(0, 100)
        self.orig_volume.setValue(0)
        self.orig_volume.setSuffix(" %")
        self.mute_original_audio = QCheckBox("屏蔽原声（仅保留配音）")
        self.mute_original_audio.setChecked(True)
        self.mute_original_audio.toggled.connect(self._on_mute_original_audio_changed)
        tts_form.addRow("原声保留:", self.orig_volume)
        tts_form.addRow("", self.mute_original_audio)

        self.tts_concurrency = QSpinBox()
        self.tts_concurrency.setRange(1, 4)
        self.tts_concurrency.setValue(int(self._settings.get("tts_concurrency", 1)))
        tts_form.addRow("TTS 并发线程数:", self.tts_concurrency)

        self.tts_consistency_mode = QComboBox()
        self.tts_consistency_mode.setMinimumWidth(360)
        self.tts_consistency_mode.addItems([
            "stable (稳定 - 默认推荐)",
            "fast (快速 - 逐句生成)",
            "strict (严格 - 含校验重试)",
        ])
        self.tts_consistency_mode.setToolTip(
            "快速：逐句生成，速度更快，但可能出现轻微音色漂移\n"
            "稳定：合并多句生成，音色更一致，默认推荐\n"
            "严格：稳定生成基础上增加校验和失败重试"
        )
        tts_form.addRow("音色一致性模式:", self.tts_consistency_mode)

        self.qwen_mode = QComboBox()
        self.qwen_mode.setMinimumWidth(360)
        self.qwen_mode.addItems([
            "auto (自动)",
            "custom_voice (预设音色)",
            "voice_design (音色设计)",
            "voice_clone (声音克隆)",
        ])
        tts_form.addRow("Qwen3 模式:", self.qwen_mode)

        self.qwen_instruct = QLineEdit(self._settings.get("tts_qwen_instruct", ""))
        self.qwen_instruct.setMinimumWidth(420)
        self.qwen_instruct.setPlaceholderText("例如：温暖、清晰、自然的中文女声")
        tts_form.addRow("音色描述:", self.qwen_instruct)

        ref_row = QWidget()
        ref_layout = QHBoxLayout(ref_row)
        ref_layout.setContentsMargins(0, 0, 0, 0)
        ref_layout.setSpacing(6)
        self.qwen_ref_audio = QLineEdit(self._settings.get("tts_qwen_ref_audio", ""))
        self.qwen_ref_audio.setMinimumWidth(320)
        ref_layout.addWidget(self.qwen_ref_audio, 1)
        self.qwen_ref_browse = QPushButton("选择")
        self.qwen_ref_browse.clicked.connect(self._choose_qwen_ref_audio)
        ref_layout.addWidget(self.qwen_ref_browse)
        tts_form.addRow("参考音频:", ref_row)

        self.qwen_ref_text = QLineEdit(self._settings.get("tts_qwen_ref_text", ""))
        self.qwen_ref_text.setMinimumWidth(420)
        self.qwen_ref_text.setPlaceholderText("可选：参考音频对应文本")
        tts_form.addRow("参考文本:", self.qwen_ref_text)

        self.qwen_advanced = QGroupBox("Qwen3 高级参数")
        self.qwen_advanced.setCheckable(True)
        self.qwen_advanced.setChecked(False)
        adv_form = QFormLayout(self.qwen_advanced)
        self.qwen_seed = QSpinBox()
        self.qwen_seed.setRange(-1, 2147483647)
        seed_default = str(_QWEN_DEFAULT_STABLE_SEED)
        self.qwen_seed.setValue(
            int(self._settings.get("tts_qwen_seed", seed_default) or seed_default)
        )
        adv_form.addRow("Seed (-1 随机):", self.qwen_seed)

        self.qwen_temperature = QDoubleSpinBox()
        self.qwen_temperature.setRange(0.0, 2.0)
        self.qwen_temperature.setSingleStep(0.05)
        self.qwen_temperature.setSpecialValueText("默认")
        temp_value = self._settings.get("tts_qwen_temperature", "")
        self.qwen_temperature.setValue(float(temp_value) if temp_value else 0.0)
        adv_form.addRow("Temperature:", self.qwen_temperature)

        self.qwen_top_p = QDoubleSpinBox()
        self.qwen_top_p.setRange(0.0, 1.0)
        self.qwen_top_p.setSingleStep(0.05)
        self.qwen_top_p.setSpecialValueText("默认")
        top_p_value = self._settings.get("tts_qwen_top_p", "")
        self.qwen_top_p.setValue(float(top_p_value) if top_p_value else 0.0)
        adv_form.addRow("Top-p:", self.qwen_top_p)

        self.qwen_max_tokens = QSpinBox()
        self.qwen_max_tokens.setRange(0, 20000)
        self.qwen_max_tokens.setSpecialValueText("默认")
        self.qwen_max_tokens.setValue(int(self._settings.get("tts_qwen_max_new_tokens", "0") or 0))
        adv_form.addRow("Max new tokens:", self.qwen_max_tokens)

        self.tts_segment_gap = QDoubleSpinBox()
        self.tts_segment_gap.setRange(0.0, 0.25)
        self.tts_segment_gap.setSingleStep(0.01)
        self.tts_segment_gap.setValue(float(self._settings.get("tts_segment_gap", "0.04") or 0.04))
        adv_form.addRow("段间安全间隔:", self.tts_segment_gap)
        layout.addWidget(self.qwen_advanced)

        layout.addWidget(self.tts_group)

        # Subtitle output settings
        sub_group = QGroupBox("字幕输出")
        sub_form = QFormLayout(sub_group)
        sub_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        sub_form.setHorizontalSpacing(14)
        sub_form.setVerticalSpacing(10)
        sub_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.subtitle_mode = QComboBox()
        self.subtitle_mode.setMinimumWidth(360)
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
        render_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        render_form.setHorizontalSpacing(14)
        render_form.setVerticalSpacing(10)
        render_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

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
        root_layout.addWidget(buttons)

    def _on_mode_changed(self):
        btn = self._mode_group.checkedButton()
        if btn is None:
            btn = self.mode_subtitle
        dub = btn is self.mode_dub
        self.trans_group.setEnabled(True)
        self.tts_group.setEnabled(dub)
        if dub and self.tts_voice.count() == 0:
            self._refresh_tts_voices()

    def _on_mute_original_audio_changed(self, checked: bool):
        self.orig_volume.setEnabled(not checked)
        if checked:
            self.orig_volume.setValue(0)

    def _load_settings_to_ui(self):
        s = self._settings
        mode = s.get("localization_mode", "subtitle")

        mode_ids = {"subtitle": 0, "translate": 1, "dub": 2}
        btn = self._mode_group.button(mode_ids.get(mode, 0))
        if btn:
            btn.setChecked(True)
        self.trans_group.setEnabled(True)
        self.tts_group.setEnabled(mode == "dub")

        src = s.get("source_language_dialog", "auto (自动检测)")
        idx = self.source_lang.findText(src)
        if idx >= 0:
            self.source_lang.setCurrentIndex(idx)

        tgt = s.get("target_language_dialog", "zh-CN (简体中文)")
        idx = self.target_lang.findText(tgt)
        if idx >= 0:
            self.target_lang.setCurrentIndex(idx)

        trans_provider = s.get("translation_provider", "openai_compatible")
        idx = self.trans_provider.findText(trans_provider)
        if idx >= 0:
            self.trans_provider.setCurrentIndex(idx)

        self.trans_base_url.setText(
            s.get("translation_base_url", "https://api.openai.com/v1")
        )
        self.trans_model.setText(s.get("translation_model", "gpt-4o-mini"))
        idx = self.trans_api_type.findText(_api_type_label(s.get("translation_api_type", "auto")))
        if idx >= 0:
            self.trans_api_type.setCurrentIndex(idx)
        self.trans_timeout.setValue(int(s.get("translation_timeout", 60)))
        self.trans_concurrency.setValue(int(s.get("translation_concurrency", 2)))

        saved_key = s.get("translation_api_key", "")
        if saved_key:
            self.trans_api_key.setText(saved_key)

        sub_mode = s.get("subtitle_mode_dialog", "bilingual (双语)")
        idx = self.subtitle_mode.findText(sub_mode)
        if idx >= 0:
            self.subtitle_mode.setCurrentIndex(idx)

        self.export_srt.setChecked(s.get("export_srt", "true") == "true")
        self.export_ass.setChecked(s.get("export_ass", "true") == "true")
        self.burn_subtitles.setChecked(s.get("burn_subtitles", "true") == "true")
        self.embed_soft.setChecked(s.get("embed_soft_subtitles", "false") == "true")

        self.orig_volume.setValue(_percent_setting(s.get("original_audio_volume_display"), 0))
        mute_original_audio = _bool_setting(s.get("mute_original_audio"), True)
        self.mute_original_audio.setChecked(mute_original_audio)
        self._on_mute_original_audio_changed(mute_original_audio)
        self.tts_concurrency.setValue(int(s.get("tts_concurrency", 1)))
        consistency_mode = str(s.get("tts_consistency_mode", "stable") or "stable").lower()
        for i in range(self.tts_consistency_mode.count()):
            if self.tts_consistency_mode.itemText(i).startswith(consistency_mode):
                self.tts_consistency_mode.setCurrentIndex(i)
                break
        qwen_mode = s.get("tts_qwen_mode", "auto")
        for i in range(self.qwen_mode.count()):
            if self.qwen_mode.itemText(i).startswith(qwen_mode):
                self.qwen_mode.setCurrentIndex(i)
                break

        tts = _provider_key(s.get("tts_provider", "edge-tts"))
        for i in range(self.tts_provider.count()):
            if _provider_key(self.tts_provider.itemText(i)) == tts:
                self.tts_provider.setCurrentIndex(i)
                break
        self._refresh_tts_voices()

    def _choose_qwen_ref_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择声音克隆参考音频",
            "",
            "Audio Files (*.wav *.mp3 *.m4a *.flac *.ogg);;All Files (*)",
        )
        if path:
            self.qwen_ref_audio.setText(path)

    def _on_tts_provider_changed(self, index: int):
        is_qwen3 = self._current_tts_provider() == "qwen3-tts"
        self.tts_manage_btn.setVisible(is_qwen3)
        self.tts_qwen3_status.setVisible(is_qwen3)
        if is_qwen3:
            self._refresh_qwen3_status()
        self._refresh_tts_voices()

    def _on_tts_language_changed(self, index: int):
        if hasattr(self, "tts_voice"):
            self._refresh_tts_voices()

    def _current_tts_provider(self) -> str:
        return _provider_key(self.tts_provider.currentText())

    def _selected_tts_voice(self) -> str:
        data = self.tts_voice.currentData()
        text = data if data is not None else self.tts_voice.currentText()
        return str(text or "").strip()

    def _set_voice_loading(self, message: str):
        self.tts_voice.clear()
        self.tts_voice.addItem("加载中...", "")
        self.tts_voice.setEnabled(False)
        self.tts_preview_btn.setEnabled(False)
        self.tts_voice_status.setText(message)

    def _refresh_tts_voices(self):
        if not hasattr(self, "tts_voice"):
            return
        provider = self._current_tts_provider()
        language = self.target_language
        preferred = self._selected_tts_voice() or self._preferred_tts_voice
        if not preferred:
            preferred = _default_tts_voice(provider, language)

        self._voice_request_id += 1
        request_id = self._voice_request_id
        self._set_voice_loading("正在加载音色...")

        def _run():
            voices, error = self._load_tts_voices(provider, language)
            self._voices_loaded.emit(request_id, voices, error)

        threading.Thread(target=_run, daemon=True).start()
        self._preferred_tts_voice = preferred

    def _load_tts_voices(self, provider: str, language: str) -> tuple[list[dict], str]:
        if provider == "sapi":
            try:
                engine_dir = Path(__file__).resolve().parent.parent / "localization-engine"
                if str(engine_dir) not in sys.path:
                    sys.path.insert(0, str(engine_dir))
                from tts import get_provider
                voices = get_provider("sapi").list_voices(language)
                if voices:
                    return voices, ""
            except Exception:
                pass
            return [{"name": "default", "locale": language, "gender": ""}], ""

        if provider == "qwen3-tts":
            try:
                url = "http://127.0.0.1:8767/voices"
                with urllib.request.urlopen(url, timeout=3) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                voices = []
                for voice in data.get("voices", []):
                    name = str(voice.get("name") or "").strip()
                    if name:
                        voices.append({
                            "name": name,
                            "locale": language,
                            "gender": voice.get("gender", ""),
                        })
                if voices:
                    return voices, ""
            except Exception:
                pass
            return [
                {"name": name, "locale": language, "gender": ""}
                for name in _QWEN_DEFAULT_VOICES
            ], "Qwen3-TTS 服务未运行，已显示默认音色"

        if not _EDGE_TTS_AVAILABLE:
            voice = _default_tts_voice(provider, language)
            return [{"name": voice, "locale": language, "gender": ""}], "edge-tts 未安装，无法试听"

        try:
            voices = asyncio.run(edge_tts.list_voices())
            filtered = []
            lang = str(language or "").lower()
            for voice in voices:
                locale = str(voice.get("Locale") or "")
                if lang and not locale.lower().startswith(lang):
                    continue
                name = str(voice.get("ShortName") or "").strip()
                if name:
                    filtered.append({
                        "name": name,
                        "locale": locale,
                        "gender": voice.get("Gender", ""),
                    })
            if filtered:
                return filtered, ""
            fallback = _default_tts_voice(provider, language)
            return [{"name": fallback, "locale": language, "gender": ""}], "未找到匹配音色，已使用默认音色"
        except Exception as exc:
            fallback = _default_tts_voice(provider, language)
            return [{"name": fallback, "locale": language, "gender": ""}], f"音色加载失败: {exc}"

    def _on_tts_voices_loaded(self, request_id: int, voices: object, error: str):
        if request_id != self._voice_request_id:
            return
        voice_items = [v for v in voices if isinstance(v, dict) and v.get("name")]
        if not voice_items:
            provider = self._current_tts_provider()
            language = self.target_language
            voice_items = [{"name": _default_tts_voice(provider, language), "locale": language}]

        preferred = self._preferred_tts_voice or voice_items[0]["name"]
        self.tts_voice.blockSignals(True)
        self.tts_voice.clear()
        for voice in voice_items:
            self.tts_voice.addItem(_format_voice_label(voice), voice["name"])
        index = self.tts_voice.findData(preferred)
        if index < 0:
            index = self.tts_voice.findText(preferred)
        self.tts_voice.setCurrentIndex(index if index >= 0 else 0)
        self.tts_voice.blockSignals(False)
        self.tts_voice.setEnabled(True)
        self.tts_preview_btn.setEnabled(True)
        if error:
            self.tts_voice_status.setText(error)
            self.tts_voice_status.setStyleSheet(f"color: #fbbf24; font-size: 11px;")
        else:
            self.tts_voice_status.setText(f"已加载 {len(voice_items)} 个音色")
            self.tts_voice_status.setStyleSheet(f"color: {_THEME['text_muted']}; font-size: 11px;")

    def _preview_tts_voice(self):
        provider = self._current_tts_provider()
        language = self.target_language
        voice = self._selected_tts_voice() or _default_tts_voice(provider, language)
        if not voice:
            QMessageBox.warning(self, "无法试听", "请先选择一个音色。")
            return

        self.tts_preview_btn.setEnabled(False)
        self.tts_preview_btn.setText("生成中...")
        self.tts_voice_status.setText("正在生成试听音频...")

        def _run():
            try:
                path = self._generate_tts_preview(provider, language, voice)
                self._preview_done.emit(True, f"试听音频已生成: {path}", str(path))
            except Exception as exc:
                self._preview_done.emit(False, f"试听失败: {exc}", "")

        threading.Thread(target=_run, daemon=True).start()

    def _generate_tts_preview(self, provider: str, language: str, voice: str) -> Path:
        text = _preview_text(language)
        preview_dir = Path(tempfile.gettempdir()) / "video2subtitles_tts_preview"
        preview_dir.mkdir(parents=True, exist_ok=True)

        if provider == "sapi":
            engine_dir = Path(__file__).resolve().parent.parent / "localization-engine"
            if str(engine_dir) not in sys.path:
                sys.path.insert(0, str(engine_dir))
            from tts import get_provider
            output_path = preview_dir / "sapi_preview.wav"
            get_provider("sapi", cache_dir=preview_dir).synthesize(
                text, language, "" if voice == "default" else voice, output_path, {"timeout": 60},
            )
            return output_path

        if provider == "qwen3-tts":
            return self._generate_qwen3_preview(text, language, voice, preview_dir)

        if not _EDGE_TTS_AVAILABLE:
            raise RuntimeError("edge-tts 未安装，请执行: pip install edge-tts")

        output_path = preview_dir / f"edge_{voice}.mp3"
        communicate = edge_tts.Communicate(text, voice)
        asyncio.run(communicate.save(str(output_path)))
        return output_path

    def _generate_qwen3_preview(self, text: str, language: str, voice: str,
                                preview_dir: Path) -> Path:
        base_url = "http://127.0.0.1:8767"
        with urllib.request.urlopen(f"{base_url}/health", timeout=5) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        if health.get("status") != "ok" or not health.get("loaded_model"):
            raise RuntimeError("Qwen3-TTS 服务未运行或尚未加载模型")

        caps = health.get("capabilities") or {}
        mode = self.qwen_mode.currentText().split(" ", 1)[0]
        generation = {}
        if self.qwen_seed.value() >= 0:
            generation["seed"] = self.qwen_seed.value()
        if self.qwen_temperature.value() > 0:
            generation["temperature"] = self.qwen_temperature.value()
        if self.qwen_top_p.value() > 0:
            generation["top_p"] = self.qwen_top_p.value()
        if self.qwen_max_tokens.value() > 0:
            generation["max_new_tokens"] = self.qwen_max_tokens.value()

        if mode == "voice_clone":
            if not caps.get("voice_clone"):
                raise RuntimeError("当前 Qwen3-TTS 模型不支持声音克隆")
            endpoint = "/synthesize/voice-clone"
            ref_audio = self.qwen_ref_audio.text().strip()
            if ref_audio:
                ref_audio = _copy_voice_ref(ref_audio)
                self.qwen_ref_audio.setText(ref_audio)
            body = {
                "text": text,
                "language": language,
                "ref_audio": ref_audio or None,
                "ref_text": self.qwen_ref_text.text().strip() or None,
            }
        elif mode == "voice_design":
            if not caps.get("voice_design"):
                raise RuntimeError("当前 Qwen3-TTS 模型不支持音色设计")
            endpoint = "/synthesize/voice-design"
            body = {
                "text": text,
                "instruct": self.qwen_instruct.text().strip() or f"A natural voice speaking {_language_base(language)}",
                "language": language,
            }
        elif mode == "custom_voice":
            if not caps.get("custom_voice"):
                raise RuntimeError("当前 Qwen3-TTS 模型不支持预设音色")
            endpoint = "/synthesize/custom-voice"
            body = {
                "text": text,
                "speaker": voice,
                "language": language,
                "instruct": self.qwen_instruct.text().strip() or None,
            }
        elif caps.get("custom_voice"):
            endpoint = "/synthesize/custom-voice"
            body = {
                "text": text,
                "speaker": voice,
                "language": language,
                "instruct": self.qwen_instruct.text().strip() or None,
            }
        elif caps.get("voice_clone"):
            endpoint = "/synthesize/voice-clone"
            body = {
                "text": text,
                "language": language,
                "ref_audio": self.qwen_ref_audio.text().strip() or None,
                "ref_text": self.qwen_ref_text.text().strip() or None,
            }
        elif caps.get("voice_design"):
            endpoint = "/synthesize/voice-design"
            body = {
                "text": text,
                "instruct": self.qwen_instruct.text().strip() or f"A natural voice speaking {_language_base(language)}",
                "language": language,
            }
        else:
            raise RuntimeError("当前 Qwen3-TTS 模型不支持语音合成")
        body.update(generation)
        body = {k: v for k, v in body.items() if v is not None}

        req = urllib.request.Request(
            f"{base_url}{endpoint}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            output_path = preview_dir / f"qwen3_{voice}.wav"
            output_path.write_bytes(resp.read())
        return output_path

    def _on_tts_preview_done(self, ok: bool, message: str, path: str):
        self.tts_preview_btn.setText("试听")
        self.tts_preview_btn.setEnabled(True)
        if ok and path:
            self.tts_voice_status.setText("试听音频已打开")
            self.tts_voice_status.setStyleSheet(f"color: #4ade80; font-size: 11px;")
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            if not opened:
                QMessageBox.information(self, "试听音频", message)
        else:
            self.tts_voice_status.setText(message)
            self.tts_voice_status.setStyleSheet(f"color: #f87171; font-size: 11px;")
            QMessageBox.warning(self, "试听失败", message)

    def _refresh_qwen3_status(self):
        if "qwen3-tts" not in self.tts_provider.currentText():
            return
        try:
            import json
            import urllib.request
            with urllib.request.urlopen(
                "http://127.0.0.1:8767/health", timeout=2
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                model = data.get("loaded_model", "未加载")
                device = data.get("device", "?")
                self.tts_qwen3_status.setText(
                    f"● 运行中 | 设备: {device} | 模型: {model}"
                )
                self.tts_qwen3_status.setStyleSheet(
                    "color: #4ade80; font-size: 11px;"
                )
        except Exception:
            self.tts_qwen3_status.setText("● 未运行，请点击「管理 Qwen3-TTS」启动")
            self.tts_qwen3_status.setStyleSheet(
                f"color: #f87171; font-size: 11px;"
            )

    def _open_qwen3_tts_setup(self):
        from ui.qwen_tts_setup_dialog import QwenTTSInstallDialog
        dlg = QwenTTSInstallDialog(self)
        dlg.exec_()
        self._refresh_qwen3_status()
        self._refresh_tts_voices()

    def _on_accept(self):
        # Save all dialog state
        if self.mode_translate.isChecked():
            self._settings["localization_mode"] = "translate"
        elif self.mode_dub.isChecked():
            self._settings["localization_mode"] = "dub"
        else:
            self._settings["localization_mode"] = "subtitle"

        self._settings["source_language_dialog"] = self.source_lang.currentText()
        self._settings["target_language_dialog"] = self.target_lang.currentText()
        self._settings["translation_provider"] = self.trans_provider.currentText()
        self._settings["translation_base_url"] = self.trans_base_url.text().strip()
        self._settings["translation_model"] = self.trans_model.text().strip()
        self._settings["translation_api_type"] = _api_type_key(self.trans_api_type.currentText())
        self._settings["translation_timeout"] = str(self.trans_timeout.value())
        self._settings["translation_concurrency"] = str(self.trans_concurrency.value())
        self._settings["translation_api_key"] = self.trans_api_key.text().strip()
        self._settings["subtitle_mode_dialog"] = self.subtitle_mode.currentText()
        self._settings["export_srt"] = "true" if self.export_srt.isChecked() else "false"
        self._settings["export_ass"] = "true" if self.export_ass.isChecked() else "false"
        self._settings["burn_subtitles"] = "true" if self.burn_subtitles.isChecked() else "false"
        self._settings["embed_soft_subtitles"] = "true" if self.embed_soft.isChecked() else "false"
        self._settings["mute_original_audio"] = "true" if self.mute_original_audio.isChecked() else "false"
        self._settings["original_audio_volume_display"] = str(self.orig_volume.value())
        self._settings["tts_concurrency"] = str(self.tts_concurrency.value())
        self._settings["tts_consistency_mode"] = self.tts_consistency_mode.currentText().split(" ", 1)[0]
        self._settings["tts_provider"] = self._current_tts_provider()
        self._settings["tts_voice"] = self._selected_tts_voice()
        ref_audio = self.qwen_ref_audio.text().strip()
        if self._current_tts_provider() == "qwen3-tts" and "voice_clone" in self.qwen_mode.currentText():
            ref_audio = _copy_voice_ref(ref_audio)
            self.qwen_ref_audio.setText(ref_audio)
        self._settings["tts_qwen_mode"] = self.qwen_mode.currentText().split(" ", 1)[0]
        self._settings["tts_qwen_instruct"] = self.qwen_instruct.text().strip()
        self._settings["tts_qwen_ref_audio"] = ref_audio
        self._settings["tts_qwen_ref_text"] = self.qwen_ref_text.text().strip()
        self._settings["tts_qwen_seed"] = "-1" if self.qwen_seed.value() < 0 else str(self.qwen_seed.value())
        self._settings["tts_qwen_temperature"] = "" if self.qwen_temperature.value() <= 0 else str(self.qwen_temperature.value())
        self._settings["tts_qwen_top_p"] = "" if self.qwen_top_p.value() <= 0 else str(self.qwen_top_p.value())
        self._settings["tts_qwen_max_new_tokens"] = "" if self.qwen_max_tokens.value() <= 0 else str(self.qwen_max_tokens.value())
        self._settings["tts_segment_gap"] = f"{self.tts_segment_gap.value():.2f}"
        saved = save_settings(self._settings)
        apply_settings_to_env(saved, overwrite=True)
        self._sync_translation_runtime_env(saved)

        self.accept()

    def _sync_translation_runtime_env(self, settings: dict) -> None:
        """Apply the saved translation key to a running Localization Engine."""
        key = str(settings.get("translation_api_key", "") or "").strip()
        if not key:
            return
        engine_url = str(
            settings.get("localization_engine_url", "http://127.0.0.1:8766")
            or "http://127.0.0.1:8766"
        ).rstrip("/")
        try:
            body = json.dumps({"api_key": key}).encode("utf-8")
            req = urllib.request.Request(
                f"{engine_url}/config/translation-api-key",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3):
                pass
        except Exception:
            pass

    @property
    def is_translate_mode(self) -> bool:
        return self.mode_translate.isChecked()

    @property
    def is_dub_mode(self) -> bool:
        return self.mode_dub.isChecked()

    @property
    def source_language(self) -> str:
        text = self.source_lang.currentText()
        return _language_code(text, "auto")

    @property
    def target_language(self) -> str:
        text = self.target_lang.currentText()
        return _language_code(text, "zh-CN")

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
            api_type=_api_type_key(self.trans_api_type.currentText()),
            timeout=self.trans_timeout.value(),
            concurrency=self.trans_concurrency.value(),
        )

    def get_settings(self) -> dict:
        self._settings["localization_mode"] = (
            "dub" if self.is_dub_mode else "translate" if self.is_translate_mode else "subtitle"
        )
        self._settings["source_language_dialog"] = self.source_lang.currentText()
        self._settings["target_language_dialog"] = self.target_lang.currentText()
        self._settings["subtitle_mode_dialog"] = self.subtitle_mode.currentText()
        self._settings["export_srt"] = "true" if self.export_srt.isChecked() else "false"
        self._settings["export_ass"] = "true" if self.export_ass.isChecked() else "false"
        self._settings["burn_subtitles"] = "true" if self.burn_subtitles.isChecked() else "false"
        self._settings["embed_soft_subtitles"] = "true" if self.embed_soft.isChecked() else "false"
        self._settings["mute_original_audio"] = "true" if self.mute_original_audio.isChecked() else "false"
        self._settings["original_audio_volume_display"] = str(self.orig_volume.value())
        self._settings["tts_concurrency"] = str(self.tts_concurrency.value())
        self._settings["translation_provider"] = self.trans_provider.currentText()
        self._settings["translation_base_url"] = self.trans_base_url.text().strip()
        self._settings["translation_model"] = self.trans_model.text().strip()
        self._settings["translation_api_type"] = _api_type_key(self.trans_api_type.currentText())
        self._settings["translation_timeout"] = str(self.trans_timeout.value())
        self._settings["translation_concurrency"] = str(self.trans_concurrency.value())
        self._settings["tts_provider"] = self._current_tts_provider()
        self._settings["tts_voice"] = self._selected_tts_voice()
        self._settings["tts_consistency_mode"] = self.tts_consistency_mode.currentText().split(" ", 1)[0]
        self._settings["tts_qwen_mode"] = self.qwen_mode.currentText().split(" ", 1)[0]
        self._settings["tts_qwen_instruct"] = self.qwen_instruct.text().strip()
        self._settings["tts_qwen_ref_audio"] = self.qwen_ref_audio.text().strip()
        self._settings["tts_qwen_ref_text"] = self.qwen_ref_text.text().strip()
        self._settings["tts_qwen_seed"] = "-1" if self.qwen_seed.value() < 0 else str(self.qwen_seed.value())
        self._settings["tts_qwen_temperature"] = "" if self.qwen_temperature.value() <= 0 else str(self.qwen_temperature.value())
        self._settings["tts_qwen_top_p"] = "" if self.qwen_top_p.value() <= 0 else str(self.qwen_top_p.value())
        self._settings["tts_qwen_max_new_tokens"] = "" if self.qwen_max_tokens.value() <= 0 else str(self.qwen_max_tokens.value())
        self._settings["tts_segment_gap"] = f"{self.tts_segment_gap.value():.2f}"
        return localization_runtime_config(self._settings) or {
            "is_translate_mode": False,
            "is_dub_mode": False,
        }
