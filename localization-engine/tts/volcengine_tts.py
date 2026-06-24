from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from process_utils import hidden_subprocess_kwargs
from tts.base import TTSAuthError, TTSCache, TTSResult, TTSUnavailableError

logger = logging.getLogger("tts.volcengine_tts")

DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
DEFAULT_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_MODEL = "seed-tts-2.0-expressive"
DEFAULT_FORMAT = "mp3"
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_VOICE = "zh_female_vv_uranus_bigtts"

DEFAULT_VOICES = [
    {
        "name": "zh_female_vv_uranus_bigtts",
        "locale": "zh-CN",
        "gender": "Female",
        "description": "Volcengine Doubao female voice",
    },
    {
        "name": "zh_male_dayi_uranus_bigtts",
        "locale": "zh-CN",
        "gender": "Male",
        "description": "Volcengine Doubao male voice",
    },
    {
        "name": "zh_female_shuangkuaisisi_moon_bigtts",
        "locale": "zh-CN",
        "gender": "Female",
        "description": "Volcengine Chinese female voice",
    },
    {
        "name": "zh_male_wennuanahu_moon_bigtts",
        "locale": "zh-CN",
        "gender": "Male",
        "description": "Volcengine Chinese male voice",
    },
]


def _option(options: dict, option_key: str, env_key: str, default: str = "") -> str:
    value = str((options or {}).get(option_key, "") or "").strip()
    if value:
        return value
    value = os.environ.get(env_key, "").strip()
    return value or default


def _int_option(options: dict, option_key: str, env_key: str, default: int) -> int:
    raw = _option(options, option_key, env_key, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float_option(options: dict, option_key: str, env_key: str, default: Optional[float]) -> Optional[float]:
    raw = _option(options, option_key, env_key, "")
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _cache_variant(options: dict, speaker: str) -> str:
    relevant = {
        "provider": "volcengine-doubao",
        "endpoint": _option(options, "volcengine_endpoint", "VOLCENGINE_TTS_ENDPOINT", DEFAULT_ENDPOINT),
        "resource_id": _option(options, "volcengine_resource_id", "VOLCENGINE_TTS_RESOURCE_ID", DEFAULT_RESOURCE_ID),
        "model": _option(options, "volcengine_model", "VOLCENGINE_TTS_MODEL", DEFAULT_MODEL),
        "speaker": speaker,
        "format": _option(options, "volcengine_format", "VOLCENGINE_TTS_FORMAT", DEFAULT_FORMAT),
        "sample_rate": _int_option(options, "volcengine_sample_rate", "VOLCENGINE_TTS_SAMPLE_RATE", DEFAULT_SAMPLE_RATE),
        "speech_rate": _int_option(options, "volcengine_speech_rate", "VOLCENGINE_TTS_SPEECH_RATE", 0),
        "loudness_rate": _int_option(options, "volcengine_loudness_rate", "VOLCENGINE_TTS_LOUDNESS_RATE", 0),
        "emotion": _option(options, "volcengine_emotion", "VOLCENGINE_TTS_EMOTION", ""),
        "emotion_scale": _float_option(options, "volcengine_emotion_scale", "VOLCENGINE_TTS_EMOTION_SCALE", None),
    }
    return json.dumps(relevant, ensure_ascii=False, sort_keys=True)


def _maybe_b64_decode(value: object) -> bytes:
    if not value:
        return b""
    if isinstance(value, bytes):
        raw = value.strip()
    else:
        raw = str(value).strip().encode("ascii", errors="ignore")
    if not raw:
        return b""
    return base64.b64decode(raw)


def _audio_values(event: object) -> Iterable[object]:
    if not isinstance(event, dict):
        return []
    values = []
    for key in ("data", "audio", "audio_data"):
        if key in event:
            values.append(event[key])
    for parent_key in ("result", "payload", "response"):
        parent = event.get(parent_key)
        if isinstance(parent, dict):
            for key in ("data", "audio", "audio_data"):
                if key in parent:
                    values.append(parent[key])
    return values


def _parse_audio_response(raw: bytes, content_type: str = "") -> bytes:
    if not raw:
        raise TTSUnavailableError("Volcengine TTS returned an empty response")

    ctype = (content_type or "").lower()
    stripped = raw.lstrip()
    if (
        "audio/" in ctype
        or ctype in {"application/octet-stream", "binary/octet-stream"}
        or not stripped.startswith((b"{", b"["))
    ):
        return raw

    audio = bytearray()
    first_error = ""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        lines = [raw.strip()]

    for line in lines:
        if line.startswith(b"data:"):
            line = line[5:].strip()
        if not line:
            continue
        try:
            event = json.loads(line.decode("utf-8"))
        except Exception:
            logger.debug("Skipping non-JSON Volcengine TTS line: %r", line[:80])
            continue

        code = event.get("code") if isinstance(event, dict) else None
        if code not in (None, 0, "0", 20000000, "20000000"):
            message = str(event.get("message") or event.get("msg") or event)[:300]
            if not first_error:
                first_error = f"code={code}, message={message}"
            continue
        for value in _audio_values(event):
            try:
                audio.extend(_maybe_b64_decode(value))
            except Exception:
                logger.debug("Skipping undecodable Volcengine TTS audio chunk")

    if audio:
        return bytes(audio)
    if first_error:
        raise TTSUnavailableError(f"Volcengine TTS failed: {first_error}")
    raise TTSUnavailableError(f"Volcengine TTS returned no audio chunks: {raw[:300]!r}")


class VolcengineDoubaoTTSProvider:
    supports_concurrency = True

    def __init__(self, cache: Optional[TTSCache] = None):
        self._cache = cache

    def list_voices(self, language: Optional[str] = None) -> List[Dict[str, str]]:
        if not language:
            return list(DEFAULT_VOICES)
        lang = str(language or "").lower().split("-", 1)[0]
        return [
            voice for voice in DEFAULT_VOICES
            if str(voice.get("locale", "")).lower().startswith(lang)
        ] or list(DEFAULT_VOICES)

    def synthesize(
        self,
        text: str,
        language: str,
        voice: str,
        output_path: Path,
        options: dict,
    ) -> TTSResult:
        options = dict(options or {})
        speaker = (voice or _option(options, "volcengine_voice", "VOLCENGINE_TTS_VOICE", DEFAULT_VOICE)).strip()
        endpoint = _option(options, "volcengine_endpoint", "VOLCENGINE_TTS_ENDPOINT", DEFAULT_ENDPOINT)
        resource_id = _option(options, "volcengine_resource_id", "VOLCENGINE_TTS_RESOURCE_ID", DEFAULT_RESOURCE_ID)
        api_key = _option(options, "volcengine_api_key", "VOLCENGINE_TTS_API_KEY", "")
        app_id = _option(options, "volcengine_app_id", "VOLCENGINE_TTS_APP_ID", "")
        access_key = _option(options, "volcengine_access_key", "VOLCENGINE_TTS_ACCESS_KEY", "")
        timeout = _int_option(options, "timeout", "VOLCENGINE_TTS_TIMEOUT", 120)

        if not api_key and not (app_id and access_key):
            raise TTSAuthError(
                "Volcengine TTS requires X-Api-Key, or X-Api-App-Id plus X-Api-Access-Key"
            )

        cache_variant = _cache_variant(options, speaker)
        if self._cache:
            cached = self._cache.get(text, speaker, language, cache_variant)
            if cached:
                duration = _get_audio_duration(cached)
                if duration > 0:
                    shutil.copy2(str(cached), str(output_path))
                    return TTSResult(output_path=output_path, duration_seconds=duration)

        payload = self._build_payload(text, language, speaker, options)
        headers = {
            "Content-Type": "application/json",
            "Connection": "keep-alive",
            "X-Api-Resource-Id": resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        if api_key:
            headers["X-Api-Key"] = api_key
        else:
            headers["X-Api-App-Id"] = app_id
            headers["X-Api-Access-Key"] = access_key

        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                content_type = ""
                headers_obj = getattr(resp, "headers", None)
                if headers_obj is not None:
                    content_type = headers_obj.get("Content-Type", "")
                audio = _parse_audio_response(raw, content_type)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                detail = str(exc)
            if exc.code in (401, 403):
                raise TTSAuthError(
                    f"Volcengine TTS authentication failed (HTTP {exc.code}): {detail}"
                ) from exc
            raise TTSUnavailableError(
                f"Volcengine TTS HTTP {exc.code}: {detail}"
            ) from exc
        except TTSAuthError:
            raise
        except TTSUnavailableError:
            raise
        except Exception as exc:
            raise TTSUnavailableError(f"Volcengine TTS failed: {exc}") from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)
        duration = _get_audio_duration(output_path)

        if self._cache and duration > 0:
            self._cache.put(text, speaker, language, output_path, cache_variant)

        return TTSResult(output_path=output_path, duration_seconds=duration)

    def _build_payload(self, text: str, language: str, speaker: str, options: dict) -> Dict[str, object]:
        audio_params: Dict[str, object] = {
            "format": _option(options, "volcengine_format", "VOLCENGINE_TTS_FORMAT", DEFAULT_FORMAT),
            "sample_rate": _int_option(options, "volcengine_sample_rate", "VOLCENGINE_TTS_SAMPLE_RATE", DEFAULT_SAMPLE_RATE),
        }
        model = _option(options, "volcengine_model", "VOLCENGINE_TTS_MODEL", DEFAULT_MODEL)
        params: Dict[str, object] = {
            "text": text,
            "speaker": speaker,
            "audio_params": audio_params,
        }
        if model:
            params["model"] = model

        for source, env_key, key in [
            ("volcengine_speech_rate", "VOLCENGINE_TTS_SPEECH_RATE", "speech_rate"),
            ("volcengine_loudness_rate", "VOLCENGINE_TTS_LOUDNESS_RATE", "loudness_rate"),
        ]:
            value = _int_option(options, source, env_key, 0)
            if value:
                params[key] = value

        emotion = _option(options, "volcengine_emotion", "VOLCENGINE_TTS_EMOTION", "")
        if emotion:
            params["emotion"] = emotion
        emotion_scale = _float_option(options, "volcengine_emotion_scale", "VOLCENGINE_TTS_EMOTION_SCALE", None)
        if emotion_scale is not None:
            params["emotion_scale"] = emotion_scale

        return {
            "user": {"uid": _option(options, "volcengine_user_uid", "VOLCENGINE_TTS_USER_UID", "video2subtitles")},
            "req_params": {k: v for k, v in params.items() if v is not None},
            "language": language,
        }


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
