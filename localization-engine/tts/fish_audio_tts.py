from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from process_utils import hidden_subprocess_kwargs
from tts.base import BaseTTSProvider, TTSAuthError, TTSCache, TTSResult, TTSUnavailableError

logger = logging.getLogger("tts.fish_audio_tts")

DEFAULT_BASE_URL = "https://api.fish.audio"
DEFAULT_MODEL = "s2.1-pro-free"
DEFAULT_VOICE = "auto"
DEFAULT_FORMAT = "mp3"
DEFAULT_SAMPLE_RATE = 44100

DEFAULT_VOICES = [
    {"name": "auto", "locale": "multi", "gender": "", "description": "Auto-select voice"},
]


def _endpoint_url(base_url: str) -> str:
    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith("/tts"):
        return base
    if base.endswith("/v1"):
        return f"{base}/tts"
    return f"{base}/v1/tts"


def _option(options: dict, keys: tuple[str, ...], env_keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = str((options or {}).get(key, "") or "").strip()
        if value:
            return value
    for env_key in env_keys:
        value = os.environ.get(env_key, "").strip()
        if value:
            return value
    return default


def _int_option(options: dict, keys: tuple[str, ...], env_keys: tuple[str, ...], default: int) -> int:
    raw = _option(options, keys, env_keys, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float_option(options: dict, keys: tuple[str, ...], env_keys: tuple[str, ...], default: Optional[float]) -> Optional[float]:
    raw = _option(options, keys, env_keys, "")
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _cache_variant(options: dict, voice: str) -> str:
    relevant = {
        "provider": "fish-audio",
        "base_url": _option(
            options,
            (
                "base_url", "baseUrl", "api_base", "apiBase",
                "fish_api_base", "fishApiBase", "fish_tts_api_base", "fishTtsApiBase",
            ),
            ("FISH_TTS_API_BASE",),
            DEFAULT_BASE_URL,
        ),
        "model": _option(
            options,
            ("model", "fish_tts_model", "fishTtsModel", "fish_model", "fishModel"),
            ("FISH_TTS_MODEL",),
            DEFAULT_MODEL,
        ),
        "voice": voice,
        "format": _option(
            options,
            ("format", "fish_tts_format", "fishTtsFormat", "fish_format", "fishFormat"),
            ("FISH_TTS_FORMAT",),
            DEFAULT_FORMAT,
        ),
        "sample_rate": _int_option(
            options,
            (
                "sample_rate", "sampleRate",
                "fish_tts_sample_rate", "fishTtsSampleRate",
                "fish_sample_rate", "fishSampleRate",
            ),
            ("FISH_TTS_SAMPLE_RATE",),
            DEFAULT_SAMPLE_RATE,
        ),
        "temperature": _float_option(
            options,
            ("temperature", "fish_tts_temperature", "fishTtsTemperature", "fish_temperature", "fishTemperature"),
            ("FISH_TTS_TEMPERATURE",),
            None,
        ),
        "top_p": _float_option(
            options,
            ("top_p", "topP", "fish_tts_top_p", "fishTtsTopP", "fish_top_p", "fishTopP"),
            ("FISH_TTS_TOP_P",),
            None,
        ),
    }
    return json.dumps(relevant, ensure_ascii=False, sort_keys=True)


class FishAudioTTSProvider(BaseTTSProvider):
    supports_concurrency = True

    def __init__(self, cache: Optional[TTSCache] = None):
        self._cache = cache

    def list_voices(self, language: Optional[str] = None) -> List[Dict[str, str]]:
        return list(DEFAULT_VOICES)

    def synthesize(
        self,
        text: str,
        language: str,
        voice: str,
        output_path: Path,
        options: dict,
    ) -> TTSResult:
        options = dict(options or {})
        api_key = _option(
            options,
            ("api_key", "apiKey", "fish_api_key", "fishApiKey", "fish_tts_api_key", "fishTtsApiKey"),
            ("FISH_TTS_API_KEY",),
            "",
        )
        if not api_key:
            raise TTSAuthError("Fish.audio TTS requires an API key")

        base_url = _option(
            options,
            (
                "base_url", "baseUrl", "api_base", "apiBase",
                "fish_api_base", "fishApiBase", "fish_tts_api_base", "fishTtsApiBase",
            ),
            ("FISH_TTS_API_BASE",),
            DEFAULT_BASE_URL,
        ).rstrip("/")

        model = _option(
            options,
            ("model", "fish_tts_model", "fishTtsModel", "fish_model", "fishModel"),
            ("FISH_TTS_MODEL",),
            DEFAULT_MODEL,
        )
        fmt = _option(
            options,
            ("format", "fish_tts_format", "fishTtsFormat", "fish_format", "fishFormat"),
            ("FISH_TTS_FORMAT",),
            DEFAULT_FORMAT,
        )
        sample_rate = _int_option(
            options,
            (
                "sample_rate", "sampleRate",
                "fish_tts_sample_rate", "fishTtsSampleRate",
                "fish_sample_rate", "fishSampleRate",
            ),
            ("FISH_TTS_SAMPLE_RATE",),
            DEFAULT_SAMPLE_RATE,
        )
        temperature = _float_option(
            options,
            ("temperature", "fish_tts_temperature", "fishTtsTemperature", "fish_temperature", "fishTemperature"),
            ("FISH_TTS_TEMPERATURE",),
            None,
        )
        top_p = _float_option(
            options,
            ("top_p", "topP", "fish_tts_top_p", "fishTtsTopP", "fish_top_p", "fishTopP"),
            ("FISH_TTS_TOP_P",),
            None,
        )
        timeout = _int_option(options, ("timeout",), ("FISH_TTS_TIMEOUT",), 120)

        selected_voice = (voice or _option(
            options,
            ("voice", "fish_tts_voice", "fishTtsVoice", "fish_voice", "fishVoice"),
            ("FISH_TTS_VOICE",),
            DEFAULT_VOICE,
        )).strip() or DEFAULT_VOICE

        cache_variant = _cache_variant(options, selected_voice)
        if self._cache:
            cached = self._cache.get(text, selected_voice, language, cache_variant)
            if cached:
                duration = _get_audio_duration(cached)
                if duration > 0:
                    shutil.copy2(str(cached), str(output_path))
                    return TTSResult(output_path=output_path, duration_seconds=duration)

        payload: Dict[str, object] = {
            "text": text,
        }
        if selected_voice and selected_voice != "auto":
            payload["reference_id"] = selected_voice
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if fmt:
            payload["format"] = fmt
        if sample_rate:
            payload["sample_rate"] = sample_rate

        url = _endpoint_url(base_url)
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "model": model,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                audio = resp.read()
                if not audio:
                    raise TTSUnavailableError("Fish.audio TTS returned an empty response")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                detail = str(exc)
            if exc.code in (401, 403):
                raise TTSAuthError(
                    f"Fish.audio TTS authentication failed (HTTP {exc.code}): {detail}"
                ) from exc
            raise TTSUnavailableError(
                f"Fish.audio TTS HTTP {exc.code}: {detail}"
            ) from exc
        except TTSAuthError:
            raise
        except TTSUnavailableError:
            raise
        except Exception as exc:
            raise TTSUnavailableError(f"Fish.audio TTS failed: {exc}") from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)
        duration = _get_audio_duration(output_path)

        if self._cache and duration > 0:
            self._cache.put(text, selected_voice, language, output_path, cache_variant)

        return TTSResult(output_path=output_path, duration_seconds=duration)


def _get_audio_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
            **hidden_subprocess_kwargs(),
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0
