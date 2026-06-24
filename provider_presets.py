"""Provider preset persistence and migration helpers."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from client_settings import SETTINGS_DIR, load_settings


ProviderPresetType = Literal["tts", "translation"]
ProviderTestStatus = Literal["success", "failed", "unknown"]

PRESETS_PATH = SETTINGS_DIR / "provider-presets.json"


@dataclass
class ProviderPreset:
    id: str
    type: ProviderPresetType
    name: str
    provider: str
    enabled: bool = True
    isDefault: bool = False
    createdAt: str = ""
    updatedAt: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    lastTestAt: str = ""
    lastTestStatus: ProviderTestStatus = "unknown"
    lastTestMessage: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if not data.get("lastTestAt"):
            data.pop("lastTestAt", None)
        if not data.get("lastTestMessage"):
            data.pop("lastTestMessage", None)
        return data


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _new_id() -> str:
    return f"preset-{uuid.uuid4().hex[:12]}"


def _str(value: Any, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _language_code(text: Any, default: str) -> str:
    value = str(text or "").split("(", 1)[0].strip()
    return value or default


def _normalize_preset(data: Dict[str, Any]) -> Optional[ProviderPreset]:
    if not isinstance(data, dict):
        return None
    preset_type = str(data.get("type", "")).strip()
    if preset_type not in {"tts", "translation"}:
        return None
    now = _now()
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    name = _str(data.get("name"), "未命名配置")
    provider = _str(data.get("provider"), "openai_compatible" if preset_type == "translation" else "edge-tts")
    status = _str(data.get("lastTestStatus"), "unknown")
    if status not in {"success", "failed", "unknown"}:
        status = "unknown"
    return ProviderPreset(
        id=_str(data.get("id")) or _new_id(),
        type=preset_type,  # type: ignore[arg-type]
        name=name,
        provider=provider,
        enabled=_bool(data.get("enabled"), True),
        isDefault=_bool(data.get("isDefault"), False),
        createdAt=_str(data.get("createdAt"), now) or now,
        updatedAt=_str(data.get("updatedAt"), now) or now,
        config=dict(config),
        lastTestAt=_str(data.get("lastTestAt")),
        lastTestStatus=status,  # type: ignore[arg-type]
        lastTestMessage=_str(data.get("lastTestMessage")),
    )


def _load_raw(path: Path = PRESETS_PATH) -> List[ProviderPreset]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    if isinstance(raw, dict):
        raw_items = raw.get("presets", [])
    else:
        raw_items = raw
    if not isinstance(raw_items, list):
        return []
    presets = []
    seen = set()
    for item in raw_items:
        preset = _normalize_preset(item)
        if not preset:
            continue
        if preset.id in seen:
            preset.id = _new_id()
        seen.add(preset.id)
        presets.append(preset)
    return _ensure_single_default_per_type(presets)


def _ensure_single_default_per_type(presets: List[ProviderPreset]) -> List[ProviderPreset]:
    for preset_type in ("translation", "tts"):
        defaults = [p for p in presets if p.type == preset_type and p.isDefault]
        if len(defaults) > 1:
            for preset in defaults[1:]:
                preset.isDefault = False
        if not defaults:
            enabled = [p for p in presets if p.type == preset_type and p.enabled]
            if enabled:
                enabled[0].isDefault = True
    return presets


def save_provider_presets(presets: List[ProviderPreset], path: Path = PRESETS_PATH) -> List[ProviderPreset]:
    cleaned = _ensure_single_default_per_type([p for p in presets if p.type in {"tts", "translation"}])
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schemaVersion": 1, "presets": [p.to_dict() for p in cleaned]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return cleaned


def load_provider_presets(path: Path = PRESETS_PATH, migrate: bool = True) -> List[ProviderPreset]:
    presets = _load_raw(path)
    if presets or not migrate:
        return presets
    migrated = migrate_legacy_settings_to_presets()
    if migrated and path == PRESETS_PATH:
        save_provider_presets(migrated, path)
    return migrated


def migrate_legacy_settings_to_presets(settings: Optional[Dict[str, Any]] = None) -> List[ProviderPreset]:
    settings = settings or load_settings()
    now = _now()
    presets: List[ProviderPreset] = []

    trans_has_config = any(
        _str(settings.get(key))
        for key in ("translation_base_url", "translation_model", "translation_api_key")
    )
    if trans_has_config:
        presets.append(ProviderPreset(
            id=_new_id(),
            type="translation",
            name="默认翻译配置",
            provider=_str(settings.get("translation_provider"), "openai_compatible") or "openai_compatible",
            enabled=True,
            isDefault=True,
            createdAt=now,
            updatedAt=now,
            config=translation_config_from_settings(settings),
        ))

    tts_has_config = any(
        _str(settings.get(key))
        for key in (
            "tts_provider", "tts_voice", "tts_volcengine_api_key",
            "tts_volcengine_app_id", "tts_volcengine_access_key",
            "tts_qwen_instruct", "tts_qwen_ref_audio",
        )
    )
    if tts_has_config:
        presets.append(ProviderPreset(
            id=_new_id(),
            type="tts",
            name="默认 TTS 配置",
            provider=_str(settings.get("tts_provider"), "edge-tts") or "edge-tts",
            enabled=True,
            isDefault=True,
            createdAt=now,
            updatedAt=now,
            config=tts_config_from_settings(settings),
        ))
    return presets


def get_provider_preset(preset_id: str, preset_type: Optional[ProviderPresetType] = None) -> Optional[ProviderPreset]:
    for preset in load_provider_presets():
        if preset.id == preset_id and (preset_type is None or preset.type == preset_type):
            return preset
    return None


def get_default_provider_preset(preset_type: ProviderPresetType, enabled_only: bool = True) -> Optional[ProviderPreset]:
    presets = [p for p in load_provider_presets() if p.type == preset_type]
    if enabled_only:
        presets = [p for p in presets if p.enabled]
    for preset in presets:
        if preset.isDefault:
            return preset
    return presets[0] if presets else None


def upsert_provider_preset(preset: ProviderPreset, path: Path = PRESETS_PATH) -> ProviderPreset:
    presets = _load_raw(path)
    now = _now()
    preset.updatedAt = now
    if not preset.createdAt:
        preset.createdAt = now
    found = False
    for idx, existing in enumerate(presets):
        if existing.id == preset.id:
            presets[idx] = preset
            found = True
            break
    if not found:
        presets.append(preset)
    if preset.isDefault:
        for item in presets:
            if item.type == preset.type and item.id != preset.id:
                item.isDefault = False
    save_provider_presets(presets, path)
    return preset


def delete_provider_preset(preset_id: str, path: Path = PRESETS_PATH) -> None:
    presets = [p for p in _load_raw(path) if p.id != preset_id]
    save_provider_presets(presets, path)


def duplicate_provider_preset(preset_id: str, path: Path = PRESETS_PATH) -> Optional[ProviderPreset]:
    presets = _load_raw(path)
    source = next((p for p in presets if p.id == preset_id), None)
    if not source:
        return None
    now = _now()
    clone = ProviderPreset(
        id=_new_id(),
        type=source.type,
        name=unique_preset_name(f"{source.name} 副本", presets, source.type),
        provider=source.provider,
        enabled=source.enabled,
        isDefault=False,
        createdAt=now,
        updatedAt=now,
        config=dict(source.config),
        lastTestStatus="unknown",
    )
    presets.append(clone)
    save_provider_presets(presets, path)
    return clone


def set_default_provider_preset(preset_id: str, path: Path = PRESETS_PATH) -> None:
    presets = _load_raw(path)
    target = next((p for p in presets if p.id == preset_id), None)
    if not target:
        return
    target.enabled = True
    for preset in presets:
        if preset.type == target.type:
            preset.isDefault = preset.id == target.id
            preset.updatedAt = _now()
    save_provider_presets(presets, path)


def unique_preset_name(name: str, presets: List[ProviderPreset], preset_type: ProviderPresetType) -> str:
    base = _str(name, "未命名配置") or "未命名配置"
    names = {p.name for p in presets if p.type == preset_type}
    if base not in names:
        return base
    idx = 2
    while f"{base} {idx}" in names:
        idx += 1
    return f"{base} {idx}"


def translation_config_from_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "apiKey": _str(settings.get("translation_api_key")),
        "baseUrl": _str(settings.get("translation_base_url")),
        "model": _str(settings.get("translation_model")),
        "apiType": _str(settings.get("translation_api_type"), "auto") or "auto",
        "sourceLanguage": _language_code(settings.get("source_language_dialog"), "auto"),
        "targetLanguage": _language_code(settings.get("target_language_dialog"), _str(settings.get("default_target_language"), "zh-CN") or "zh-CN"),
        "temperature": _float(settings.get("translation_temperature"), 0.3),
        "maxBatchItems": _int(settings.get("translation_max_batch_items"), 10),
        "concurrency": _int(settings.get("translation_concurrency"), 2),
        "retries": _int(settings.get("translation_retry_count"), 3),
        "timeout": _int(settings.get("translation_timeout"), 60),
        "qualityMode": _str(settings.get("translation_quality"), "fast") or "fast",
    }


def tts_config_from_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    provider = _str(settings.get("tts_provider"), "edge-tts") or "edge-tts"
    gap_seconds = _float(settings.get("tts_segment_gap"), 0.12)
    return {
        "apiKey": _str(settings.get("tts_volcengine_api_key")),
        "baseUrl": _str(settings.get("tts_volcengine_endpoint")),
        "model": _str(settings.get("tts_volcengine_model") or settings.get("tts_qwen_model")),
        "voice": _str(settings.get("tts_voice")),
        "speed": _float(settings.get("tts_speed"), 1.0),
        "volume": _int(settings.get("tts_volcengine_loudness_rate"), 0),
        "pitch": _int(settings.get("tts_pitch"), 0),
        "format": _str(settings.get("tts_volcengine_format"), "mp3") or "mp3",
        "sampleRate": _int(settings.get("tts_volcengine_sample_rate"), 24000),
        "concurrency": _int(settings.get("tts_concurrency"), 1),
        "minSentenceGapMs": max(0, int(gap_seconds * 1000)),
        "maxAudioStretchRatio": _float(settings.get("tts_max_audio_stretch_ratio"), 1.15),
        "enableDurationAlign": _bool(settings.get("tts_enable_duration_align"), True),
        "consistencyMode": _str(settings.get("tts_consistency_mode"), "stable") or "stable",
        "qwenMode": _str(settings.get("tts_qwen_mode"), "auto") or "auto",
        "qwenInstruct": _str(settings.get("tts_qwen_instruct")),
        "qwenRefAudio": _str(settings.get("tts_qwen_ref_audio")),
        "qwenRefText": _str(settings.get("tts_qwen_ref_text")),
        "qwenSeed": _str(settings.get("tts_qwen_seed"), "42"),
        "qwenTemperature": _str(settings.get("tts_qwen_temperature")),
        "qwenTopP": _str(settings.get("tts_qwen_top_p")),
        "qwenMaxNewTokens": _str(settings.get("tts_qwen_max_new_tokens")),
        "volcengineAppId": _str(settings.get("tts_volcengine_app_id")),
        "volcengineAccessKey": _str(settings.get("tts_volcengine_access_key")),
        "volcengineResourceId": _str(settings.get("tts_volcengine_resource_id"), "seed-tts-2.0") or "seed-tts-2.0",
        "volcengineSpeechRate": _int(settings.get("tts_volcengine_speech_rate"), 0),
        "provider": provider,
    }


def apply_translation_preset_to_settings(settings: Dict[str, Any], preset: ProviderPreset) -> Dict[str, Any]:
    cfg = preset.config or {}
    updated = dict(settings)
    updated["translation_provider"] = preset.provider
    updated["translation_base_url"] = _str(cfg.get("baseUrl") or cfg.get("base_url"))
    updated["translation_model"] = _str(cfg.get("model"))
    updated["translation_api_key"] = _str(cfg.get("apiKey") or cfg.get("api_key"))
    updated["translation_api_type"] = _str(cfg.get("apiType") or cfg.get("api_type"), "auto") or "auto"
    updated["translation_timeout"] = str(_int(cfg.get("timeout"), 60))
    updated["translation_concurrency"] = str(_int(cfg.get("concurrency"), 2))
    updated["translation_max_batch_items"] = str(_int(cfg.get("maxBatchItems") or cfg.get("max_batch_items"), 10))
    updated["translation_quality"] = _str(cfg.get("qualityMode") or cfg.get("quality_mode"), "fast") or "fast"
    source = _str(cfg.get("sourceLanguage") or cfg.get("source_language"))
    target = _str(cfg.get("targetLanguage") or cfg.get("target_language"))
    if source:
        updated["source_language_dialog"] = source
    if target:
        updated["target_language_dialog"] = target
        updated["default_target_language"] = target
    return updated


def apply_tts_preset_to_settings(settings: Dict[str, Any], preset: ProviderPreset) -> Dict[str, Any]:
    cfg = preset.config or {}
    updated = dict(settings)
    updated["tts_provider"] = preset.provider
    updated["tts_voice"] = _str(cfg.get("voice"))
    updated["tts_concurrency"] = str(_int(cfg.get("concurrency"), 1))
    updated["tts_consistency_mode"] = _str(cfg.get("consistencyMode") or cfg.get("consistency_mode"), "stable") or "stable"
    min_gap = _int(cfg.get("minSentenceGapMs") or cfg.get("min_sentence_gap_ms"), 120)
    updated["tts_segment_gap"] = f"{max(0, min_gap) / 1000.0:.2f}"
    updated["tts_qwen_mode"] = _str(cfg.get("qwenMode") or cfg.get("qwen_mode"), "auto") or "auto"
    updated["tts_qwen_instruct"] = _str(cfg.get("qwenInstruct") or cfg.get("qwen_instruct"))
    updated["tts_qwen_ref_audio"] = _str(cfg.get("qwenRefAudio") or cfg.get("qwen_ref_audio"))
    updated["tts_qwen_ref_text"] = _str(cfg.get("qwenRefText") or cfg.get("qwen_ref_text"))
    updated["tts_qwen_seed"] = _str(cfg.get("qwenSeed") or cfg.get("qwen_seed"), "42") or "42"
    updated["tts_qwen_temperature"] = _str(cfg.get("qwenTemperature") or cfg.get("qwen_temperature"))
    updated["tts_qwen_top_p"] = _str(cfg.get("qwenTopP") or cfg.get("qwen_top_p"))
    updated["tts_qwen_max_new_tokens"] = _str(cfg.get("qwenMaxNewTokens") or cfg.get("qwen_max_new_tokens"))
    updated["tts_volcengine_endpoint"] = _str(cfg.get("baseUrl") or cfg.get("base_url"))
    updated["tts_volcengine_api_key"] = _str(cfg.get("apiKey") or cfg.get("api_key"))
    updated["tts_volcengine_app_id"] = _str(cfg.get("volcengineAppId") or cfg.get("volcengine_app_id"))
    updated["tts_volcengine_access_key"] = _str(cfg.get("volcengineAccessKey") or cfg.get("volcengine_access_key"))
    updated["tts_volcengine_resource_id"] = _str(cfg.get("volcengineResourceId") or cfg.get("volcengine_resource_id"), "seed-tts-2.0") or "seed-tts-2.0"
    updated["tts_volcengine_model"] = _str(cfg.get("model"))
    updated["tts_volcengine_format"] = _str(cfg.get("format"), "mp3") or "mp3"
    updated["tts_volcengine_sample_rate"] = str(_int(cfg.get("sampleRate") or cfg.get("sample_rate"), 24000))
    updated["tts_volcengine_speech_rate"] = str(_int(cfg.get("volcengineSpeechRate") or cfg.get("volcengine_speech_rate") or cfg.get("speed"), 0))
    updated["tts_volcengine_loudness_rate"] = str(_int(cfg.get("volume"), 0))
    return updated


def export_presets(path: Path, presets: Optional[List[ProviderPreset]] = None) -> None:
    presets = presets if presets is not None else load_provider_presets()
    safe = []
    for preset in presets:
        item = preset.to_dict()
        cfg = dict(item.get("config") or {})
        for key in ("apiKey", "api_key", "volcengineAccessKey", "volcengine_access_key"):
            if key in cfg:
                cfg[key] = ""
        item["config"] = cfg
        safe.append(item)
    path.write_text(json.dumps({"schemaVersion": 1, "presets": safe}, ensure_ascii=False, indent=2), encoding="utf-8")


def import_presets(path: Path, target_path: Path = PRESETS_PATH) -> int:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    raw_items = raw.get("presets", raw) if isinstance(raw, dict) else raw
    if not isinstance(raw_items, list):
        raise ValueError("配置文件格式无效：需要 ProviderPreset 数组")
    existing = _load_raw(target_path)
    imported = 0
    for item in raw_items:
        preset = _normalize_preset(item)
        if not preset:
            continue
        preset.id = _new_id()
        preset.name = unique_preset_name(f"{preset.name} 导入副本", existing, preset.type)
        preset.isDefault = not any(p.type == preset.type and p.isDefault for p in existing)
        preset.createdAt = _now()
        preset.updatedAt = preset.createdAt
        existing.append(preset)
        imported += 1
    save_provider_presets(existing, target_path)
    return imported
