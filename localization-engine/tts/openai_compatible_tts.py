from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import base64
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from process_utils import hidden_subprocess_kwargs
from tts.base import TTSAuthError, TTSCache, TTSResult, TTSUnavailableError

logger = logging.getLogger("tts.openai_compatible_tts")

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "tts-1"
DEFAULT_VOICE = "alloy"
DEFAULT_FORMAT = "mp3"
DEFAULT_SAMPLE_RATE = 24000
MIMO_DEFAULT_MODEL = "mimo-v2.5-tts"
MIMO_DEFAULT_VOICE = "Chloe"

DEFAULT_VOICES = [
    {"name": "alloy", "locale": "multi", "gender": "", "description": "OpenAI TTS voice"},
    {"name": "ash", "locale": "multi", "gender": "", "description": "OpenAI TTS voice"},
    {"name": "ballad", "locale": "multi", "gender": "", "description": "OpenAI TTS voice"},
    {"name": "coral", "locale": "multi", "gender": "", "description": "OpenAI TTS voice"},
    {"name": "echo", "locale": "multi", "gender": "", "description": "OpenAI TTS voice"},
    {"name": "fable", "locale": "multi", "gender": "", "description": "OpenAI TTS voice"},
    {"name": "nova", "locale": "multi", "gender": "", "description": "OpenAI TTS voice"},
    {"name": "onyx", "locale": "multi", "gender": "", "description": "OpenAI TTS voice"},
    {"name": "sage", "locale": "multi", "gender": "", "description": "OpenAI TTS voice"},
    {"name": "shimmer", "locale": "multi", "gender": "", "description": "OpenAI TTS voice"},
]


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


def _endpoint_url(base_url: str) -> str:
    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith("/audio/speech"):
        return base
    return f"{base}/audio/speech"


def _chat_endpoint_url(base_url: str) -> str:
    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _is_mimo_request(base_url: str, model: str, options: dict) -> bool:
    provider_hint = str((options or {}).get("provider", "") or "").lower()
    return (
        "mimo" in str(model or "").lower()
        or "xiaomimimo.com" in str(base_url or "").lower()
        or "mimo" in provider_hint
    )


def _cache_variant(options: dict, voice: str) -> str:
    relevant = {
        "provider": "openai-compatible",
        "base_url": _option(
            options,
            ("base_url", "openai_base_url", "openai_tts_base_url", "volcengine_endpoint"),
            ("OPENAI_TTS_BASE_URL", "V2S_TTS_BASE_URL", "VOLCENGINE_TTS_ENDPOINT"),
            DEFAULT_BASE_URL,
        ),
        "model": _option(
            options,
            ("model", "openai_model", "openai_tts_model", "volcengine_model"),
            ("OPENAI_TTS_MODEL", "V2S_TTS_MODEL", "VOLCENGINE_TTS_MODEL"),
            DEFAULT_MODEL,
        ),
        "voice": voice,
        "format": _option(
            options,
            ("format", "response_format", "openai_tts_format", "volcengine_format"),
            ("OPENAI_TTS_FORMAT", "V2S_TTS_FORMAT", "VOLCENGINE_TTS_FORMAT"),
            DEFAULT_FORMAT,
        ),
        "sample_rate": _int_option(
            options,
            ("sample_rate", "openai_tts_sample_rate", "volcengine_sample_rate"),
            ("OPENAI_TTS_SAMPLE_RATE", "V2S_TTS_SAMPLE_RATE", "VOLCENGINE_TTS_SAMPLE_RATE"),
            DEFAULT_SAMPLE_RATE,
        ),
        "speed": _float_option(
            options,
            ("speed", "openai_tts_speed", "volcengine_speech_rate"),
            ("OPENAI_TTS_SPEED", "V2S_TTS_SPEED"),
            1.0,
        ),
    }
    return json.dumps(relevant, ensure_ascii=False, sort_keys=True)


class OpenAICompatibleTTSProvider:
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
            ("api_key", "openai_api_key", "openai_tts_api_key", "volcengine_api_key"),
            ("OPENAI_TTS_API_KEY", "V2S_TTS_API_KEY", "VOLCENGINE_TTS_API_KEY"),
            "",
        )
        if not api_key:
            raise TTSAuthError("OpenAI-compatible TTS requires an API key")

        base_url = _option(
            options,
            ("base_url", "openai_base_url", "openai_tts_base_url", "volcengine_endpoint"),
            ("OPENAI_TTS_BASE_URL", "V2S_TTS_BASE_URL", "VOLCENGINE_TTS_ENDPOINT"),
            DEFAULT_BASE_URL,
        )
        model = _option(
            options,
            ("model", "openai_model", "openai_tts_model", "volcengine_model"),
            ("OPENAI_TTS_MODEL", "V2S_TTS_MODEL", "VOLCENGINE_TTS_MODEL"),
            DEFAULT_MODEL,
        )
        fmt = _option(
            options,
            ("format", "response_format", "openai_tts_format", "volcengine_format"),
            ("OPENAI_TTS_FORMAT", "V2S_TTS_FORMAT", "VOLCENGINE_TTS_FORMAT"),
            DEFAULT_FORMAT,
        )
        sample_rate = _int_option(
            options,
            ("sample_rate", "openai_tts_sample_rate", "volcengine_sample_rate"),
            ("OPENAI_TTS_SAMPLE_RATE", "V2S_TTS_SAMPLE_RATE", "VOLCENGINE_TTS_SAMPLE_RATE"),
            DEFAULT_SAMPLE_RATE,
        )
        speed = _float_option(
            options,
            ("speed", "openai_tts_speed", "volcengine_speech_rate"),
            ("OPENAI_TTS_SPEED", "V2S_TTS_SPEED"),
            1.0,
        )
        timeout = _int_option(options, ("timeout",), ("OPENAI_TTS_TIMEOUT", "V2S_TTS_TIMEOUT"), 120)
        selected_voice = (voice or _option(
            options,
            ("voice", "openai_tts_voice"),
            ("OPENAI_TTS_VOICE", "V2S_TTS_VOICE"),
            DEFAULT_VOICE,
        )).strip() or DEFAULT_VOICE
        if _is_mimo_request(base_url, model, options):
            if model == DEFAULT_MODEL:
                model = MIMO_DEFAULT_MODEL
            if selected_voice == DEFAULT_VOICE:
                selected_voice = MIMO_DEFAULT_VOICE
            if fmt not in {"wav", "pcm16"}:
                fmt = "wav"
            options["openai_tts_model"] = model
            options["openai_tts_voice"] = selected_voice
            options["openai_tts_format"] = fmt

        cache_variant = _cache_variant(options, selected_voice)
        if self._cache:
            cached = self._cache.get(text, selected_voice, language, cache_variant)
            if cached:
                duration = _get_audio_duration(cached)
                if duration > 0:
                    shutil.copy2(str(cached), str(output_path))
                    return TTSResult(output_path=output_path, duration_seconds=duration)

        if _is_mimo_request(base_url, model, options):
            audio = self._synthesize_mimo_chat(
                text=text,
                base_url=base_url,
                api_key=api_key,
                model=model,
                voice=selected_voice,
                audio_format=fmt,
                timeout=timeout,
                options=options,
            )
        else:
            payload: Dict[str, object] = {
                "model": model,
                "input": text,
                "voice": selected_voice,
                "response_format": fmt,
            }
            if sample_rate:
                payload["sample_rate"] = sample_rate
            if speed is not None:
                payload["speed"] = speed

            audio = self._post_audio_speech(base_url, api_key, payload, timeout)

        if not audio:
            raise TTSUnavailableError("OpenAI-compatible TTS returned an empty response")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)
        duration = _get_audio_duration(output_path)
        if self._cache and duration > 0:
            self._cache.put(text, selected_voice, language, output_path, cache_variant)
        return TTSResult(output_path=output_path, duration_seconds=duration)

    def _post_audio_speech(self, base_url: str, api_key: str, payload: Dict[str, object],
                           timeout: int) -> bytes:
        req = urllib.request.Request(
            _endpoint_url(base_url),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return self._read_request(req, timeout, "OpenAI-compatible TTS")

    def _synthesize_mimo_chat(
        self,
        *,
        text: str,
        base_url: str,
        api_key: str,
        model: str,
        voice: str,
        audio_format: str,
        timeout: int,
        options: dict,
    ) -> bytes:
        instruction = str(
            options.get("instruct")
            or options.get("style")
            or options.get("openai_tts_instruction")
            or "Natural, clear narration voice. Keep pacing steady and pronunciation accurate."
        ).strip()
        payload: Dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "user", "content": instruction},
                {"role": "assistant", "content": text},
            ],
            "audio": {
                "format": audio_format,
                "voice": voice,
            },
        }
        req = urllib.request.Request(
            _chat_endpoint_url(base_url),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "api-key": api_key,
                "x-api-key": api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        raw = self._read_request(req, timeout, "MiMo-compatible TTS", retry_on_rate_limit=True)
        try:
            data = json.loads(raw.decode("utf-8"))
            audio = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("audio", {})
                .get("data", "")
            )
            if not audio:
                raise KeyError("choices[0].message.audio.data")
            return base64.b64decode(audio)
        except Exception as exc:
            raise TTSUnavailableError(
                f"MiMo-compatible TTS returned no decodable audio: {raw[:300]!r}"
            ) from exc

    def _read_request(
        self,
        req: urllib.request.Request,
        timeout: int,
        label: str,
        *,
        retry_on_rate_limit: bool = False,
    ) -> bytes:
        max_attempts = 1
        if retry_on_rate_limit:
            try:
                max_attempts = max(1, int(os.environ.get("MIMO_TTS_RATE_LIMIT_RETRIES", "8")))
            except ValueError:
                max_attempts = 8
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    detail = str(exc)
                last_error = f"HTTP {exc.code}: {detail}"
                if exc.code in (401, 403):
                    raise TTSAuthError(
                        f"{label} authentication failed (HTTP {exc.code}): {detail}"
                    ) from exc
                if retry_on_rate_limit and exc.code in (429, 503) and attempt < max_attempts:
                    retry_after = 0.0
                    try:
                        retry_after = float(exc.headers.get("Retry-After", "") or 0)
                    except Exception:
                        retry_after = 0.0
                    delay = retry_after if retry_after > 0 else min(90.0, 10.0 * (2 ** (attempt - 1)))
                    logger.warning(
                        "%s rate limited (HTTP %s), retrying in %.1fs (%s/%s)",
                        label, exc.code, delay, attempt, max_attempts,
                    )
                    time.sleep(delay)
                    continue
                raise TTSUnavailableError(f"{label} HTTP {exc.code}: {detail}") from exc
            except TTSAuthError:
                raise
            except Exception as exc:
                raise TTSUnavailableError(f"{label} failed: {exc}") from exc
        raise TTSUnavailableError(f"{label} failed after retries: {last_error}")


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
