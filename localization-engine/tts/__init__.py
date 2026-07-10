from __future__ import annotations

from pathlib import Path
from typing import Optional

from tts.base import BaseTTSProvider, TTSProvider, TTSCache, TTSResult, TTSCapabilities
from tts.edge_tts import EdgeTTSProvider
from tts.fish_audio_tts import FishAudioTTSProvider
from tts.openai_compatible_tts import OpenAICompatibleTTSProvider
from tts.qwen3_tts import Qwen3TTSProvider
from tts.sapi_tts import SapiTTSProvider
from tts.volcengine_tts import VolcengineDoubaoTTSProvider
from tts.registry import ProviderRegistry, provider_registry

_PROVIDER_CACHE: dict = {}
_DEFAULT_EDGE_CACHE: Optional[TTSCache] = None


def _register_builtin_providers() -> None:
    builtins = (
        ("edge-tts", EdgeTTSProvider, ()),
        ("qwen3-tts", Qwen3TTSProvider, ("qwen3_tts", "qwen3")),
        ("sapi", SapiTTSProvider, ("windows-sapi", "windows_sapi")),
        (
            "openai-compatible",
            OpenAICompatibleTTSProvider,
            ("openai_compatible", "openai", "openai-tts", "openai_tts"),
        ),
        (
            "volcengine-doubao",
            VolcengineDoubaoTTSProvider,
            ("volcengine", "volcano", "doubao-tts", "doubao"),
        ),
        ("fish-audio", FishAudioTTSProvider, ("fish_audio", "fish", "fish.audio")),
    )
    registered = set(provider_registry.names())
    for name, factory, aliases in builtins:
        if name not in registered:
            provider_registry.register(name, factory, aliases=aliases)

    capabilities = {
        "edge-tts": TTSCapabilities(
            speed=True, pitch=True, preview_character_limit=1000,
            supported_output_formats=("mp3",),
            supported_parameters=("rate", "pitch", "volume"),
        ),
        "qwen3-tts": TTSCapabilities(
            preview_character_limit=300, supported_output_formats=("wav",),
            supported_parameters=(
                "qwen_mode", "qwen_model", "model", "instruct", "ref_audio",
                "ref_text", "x_vector_only_mode", "voice_clone_prompt_id",
                "max_new_tokens", "seed", "seed_policy", "top_p", "temperature",
                "device", "min_vram_mb", "idle_timeout_seconds", "load_timeout_seconds",
            ),
        ),
        "sapi": TTSCapabilities(
            speed=True, preview_character_limit=1000,
            supported_output_formats=("wav",),
            supported_parameters=("sapi_rate", "sapi_volume"),
        ),
        "openai-compatible": TTSCapabilities(
            speed=True, preview_character_limit=4096,
            supported_output_formats=("mp3", "opus", "aac", "flac", "wav", "pcm"),
            supported_parameters=(
                "speed", "openai_tts_model", "openai_tts_voice", "openai_tts_format",
                "openai_tts_base_url", "openai_tts_sample_rate", "instruct", "style",
            ),
        ),
        "volcengine-doubao": TTSCapabilities(
            speed=True, emotion=True, preview_character_limit=1000,
            supported_output_formats=("mp3", "wav", "ogg_opus", "pcm"),
            supported_parameters=(
                "volcengine_voice", "volcengine_model", "volcengine_format",
                "volcengine_sample_rate", "volcengine_speech_rate",
                "volcengine_loudness_rate", "volcengine_emotion",
                "volcengine_emotion_scale", "volcengine_user_uid",
            ),
        ),
        "fish-audio": TTSCapabilities(
            emotion=True, preview_character_limit=1000,
            supported_output_formats=("mp3", "wav", "pcm", "opus"),
            supported_parameters=(
                "fish_audio_model", "fish_audio_format", "fish_audio_reference_id",
                "fish_audio_references", "fish_audio_normalize", "fish_audio_latency",
            ),
        ),
    }
    for name, declaration in capabilities.items():
        provider_registry.set_capabilities(name, declaration)


_register_builtin_providers()


def _get_edge_cache(cache_dir: Optional[Path] = None) -> TTSCache:
    global _DEFAULT_EDGE_CACHE
    if cache_dir:
        return TTSCache(cache_dir)
    if _DEFAULT_EDGE_CACHE is None:
        from pathlib import Path
        import tempfile
        _DEFAULT_EDGE_CACHE = TTSCache(
            Path(tempfile.gettempdir()) / "v2s_tts_cache"
        )
    return _DEFAULT_EDGE_CACHE


def get_provider(name: str = "edge-tts",
                 cache_dir: Optional[Path] = None) -> TTSProvider:
    canonical = provider_registry.canonical_name(name)
    key = f"{canonical}:{cache_dir}"
    if key in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[key]

    provider = provider_registry.create(
        canonical,
        cache=_get_edge_cache(cache_dir),
    )

    _PROVIDER_CACHE[key] = provider
    return provider


def register_provider(name: str, factory, *, aliases=(), replace: bool = False) -> None:
    """Register a provider while preserving the legacy lookup API."""
    provider_registry.register(name, factory, aliases=aliases, replace=replace)
    _PROVIDER_CACHE.clear()


def _check_qwen3_healthy() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(
            "http://127.0.0.1:8767/health", timeout=2
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def list_available_providers() -> list[dict]:
    providers = []
    try:
        import edge_tts
        providers.append({"name": "edge-tts", "available": True})
    except ImportError:
        providers.append({"name": "edge-tts", "available": False})
    qwen3_ok = _check_qwen3_healthy()
    try:
        import qwen_tts
        providers.append({
            "name": "qwen3-tts",
            "available": qwen3_ok,
            "service_running": qwen3_ok,
        })
    except ImportError:
        providers.append({
            "name": "qwen3-tts",
            "available": False,
            "service_running": False,
        })
    providers.append({
        "name": "sapi",
        "available": __import__("os").name == "nt",
        "local": True,
    })
    providers.append({
        "name": "openai-compatible",
        "available": True,
        "remote": True,
    })
    providers.append({
        "name": "volcengine-doubao",
        "available": True,
        "remote": True,
    })
    providers.append({
        "name": "fish-audio",
        "available": True,
        "remote": True,
    })
    return providers
