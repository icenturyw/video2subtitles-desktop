from __future__ import annotations

from pathlib import Path
from typing import Optional

from tts.base import TTSProvider, TTSCache, TTSResult
from tts.edge_tts import EdgeTTSProvider
from tts.qwen3_tts import Qwen3TTSProvider
from tts.sapi_tts import SapiTTSProvider

_PROVIDER_CACHE: dict = {}
_DEFAULT_EDGE_CACHE: Optional[TTSCache] = None


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
    key = f"{name}:{cache_dir}"
    if key in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[key]

    if name == "edge-tts":
        provider = EdgeTTSProvider(cache=_get_edge_cache(cache_dir))
    elif name in ("qwen3-tts", "qwen3_tts", "qwen3"):
        from tts.qwen3_tts import Qwen3TTSProvider
        provider = Qwen3TTSProvider(cache=_get_edge_cache(cache_dir))
    elif name in ("sapi", "windows-sapi", "windows_sapi"):
        provider = SapiTTSProvider(cache=_get_edge_cache(cache_dir))
    else:
        raise ValueError(f"Unknown TTS provider: {name}")

    _PROVIDER_CACHE[key] = provider
    return provider


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
    return providers
