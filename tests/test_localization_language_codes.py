from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "localization-engine"))

from ui.localization_dialog import (  # noqa: E402
    _default_tts_voice,
    _language_code,
    _preview_text,
    localization_runtime_config,
)
from translation.openai_compatible import _language_name  # noqa: E402


def test_localization_dialog_language_code_uses_prefix():
    assert _language_code("zh-CN (简体中文)", "zh-CN") == "zh-CN"
    assert _language_code("en (英文)", "auto") == "en"
    assert _language_code("auto (自动检测)", "auto") == "auto"
    assert _language_code("", "zh-CN") == "zh-CN"


def test_translation_prompt_language_names_are_unambiguous():
    assert _language_name("zh-CN") == "Simplified Chinese"
    assert _language_name("auto") == "the source language detected from the subtitle text"
    assert _language_name("cs") == "Czech"
    assert _language_name("pt-BR") == "pt-BR"


def test_tts_voice_defaults_follow_provider_and_language():
    assert _default_tts_voice("edge-tts", "zh-CN") == "zh-CN-XiaoxiaoNeural"
    assert _default_tts_voice("edge-tts", "ja") == "ja-JP-NanamiNeural"
    assert _default_tts_voice("qwen3-tts", "en") == "Vivian"
    assert _default_tts_voice("sapi", "zh-CN") == "default"


def test_tts_preview_text_follows_language():
    assert "preview" in _preview_text("en")
    assert "试听" in _preview_text("zh-CN")


def test_saved_translate_mode_builds_runtime_config():
    cfg = localization_runtime_config({
        "localization_mode": "translate",
        "source_language_dialog": "en (英文)",
        "target_language_dialog": "zh-CN (简体中文)",
        "subtitle_mode_dialog": "bilingual (双语)",
        "burn_subtitles": "true",
        "embed_soft_subtitles": "false",
        "translation_provider": "openai_compatible",
        "translation_base_url": "https://example.test/v1",
        "translation_model": "test-model",
        "translation_api_type": "anthropic_messages",
        "translation_api_key": "sk-test-secret",
    })

    assert cfg is not None
    assert cfg["is_translate_mode"] is True
    assert cfg["is_dub_mode"] is False
    assert cfg["source_language"] == "en"
    assert cfg["target_language"] == "zh-CN"
    assert cfg["subtitle_mode"] == "bilingual"
    assert cfg["translation_config"]["model"] == "test-model"
    assert cfg["translation_config"]["api_type"] == "anthropic_messages"
    assert cfg["translation_config"]["api_key"] == "sk-test-secret"


def test_saved_subtitle_mode_disables_localization_runtime_config():
    cfg = localization_runtime_config({"localization_mode": "subtitle"})
    assert cfg is None


def test_subtitle_mode_auto_enables_translation_when_provider_is_configured():
    cfg = localization_runtime_config({
        "localization_mode": "subtitle",
        "translation_base_url": "https://example.test/v1",
        "translation_api_key": "sk-test-secret",
        "target_language_dialog": "zh-CN (简体中文)",
    })

    assert cfg is not None
    assert cfg["is_translate_mode"] is True
    assert cfg["is_dub_mode"] is False
    assert cfg["target_language"] == "zh-CN"


def test_saved_dub_mode_sets_dubbing_provider():
    cfg = localization_runtime_config({
        "localization_mode": "dub",
        "target_language_dialog": "ja (日文)",
        "tts_provider": "qwen3-tts",
        "tts_voice": "Vivian",
        "tts_concurrency": "3",
        "mute_original_audio": "false",
        "original_audio_volume_display": "25",
    })

    assert cfg is not None
    assert cfg["is_translate_mode"] is True
    assert cfg["is_dub_mode"] is True
    assert cfg["target_language"] == "ja"
    assert cfg["dubbing_enabled"] is True
    assert cfg["tts_provider"] == "qwen3-tts"
    assert cfg["tts_voice"] == "Vivian"
    assert cfg["tts_concurrency"] == 3
    assert cfg["mute_original_audio"] is False
    assert cfg["original_volume"] == 0.25


def test_saved_dub_mode_mutes_original_audio_by_default():
    cfg = localization_runtime_config({
        "localization_mode": "dub",
        "target_language_dialog": "ja (鏃ユ枃)",
        "tts_provider": "qwen3-tts",
        "original_audio_volume_display": "25",
    })

    assert cfg is not None
    assert cfg["mute_original_audio"] is True
    assert cfg["original_volume"] == 0.0


def test_saved_dub_mode_includes_qwen3_tts_options():
    cfg = localization_runtime_config({
        "localization_mode": "dub",
        "target_language_dialog": "zh-CN (简体中文)",
        "tts_provider": "qwen3-tts",
        "tts_qwen_mode": "voice_design",
        "tts_qwen_instruct": "warm narrator",
        "tts_qwen_seed": "42",
        "tts_qwen_temperature": "0.7",
        "tts_qwen_top_p": "0.8",
        "tts_qwen_max_new_tokens": "512",
        "tts_segment_gap": "0.08",
    })

    assert cfg is not None
    opts = cfg["tts_options"]
    assert opts["qwen_mode"] == "voice_design"
    assert opts["instruct"] == "warm narrator"
    assert opts["seed"] == 42
    assert opts["temperature"] == 0.7
    assert opts["top_p"] == 0.8
    assert opts["max_new_tokens"] == 512
    assert opts["tts_segment_gap"] == 0.08
