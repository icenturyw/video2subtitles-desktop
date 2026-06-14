from __future__ import annotations

import json
import logging
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from tts.base import TTSResult, TTSUnavailableError, TTSCache

logger = logging.getLogger("tts.qwen3_tts")

LANG_MAP = {
    "zh": "chinese",
    "zh-cn": "chinese",
    "zh-tw": "chinese",
    "en": "english",
    "ja": "japanese",
    "ko": "korean",
    "de": "german",
    "fr": "french",
    "ru": "russian",
    "pt": "portuguese",
    "es": "spanish",
    "it": "italian",
}

DEFAULT_VOICE = "Vivian"


class Qwen3TTSProvider:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8767",
        cache: Optional[TTSCache] = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._cache = cache
        self._voice_cache: Optional[List[Dict[str, str]]] = None

    def list_voices(self, language: Optional[str] = None) -> List[Dict[str, str]]:
        if self._voice_cache is None:
            try:
                resp = self._request("GET", "/voices")
                data = json.loads(resp.read().decode("utf-8"))
                languages = ",".join(data.get("languages", []))
                self._voice_cache = [
                    {
                        "name": v["name"],
                        "locale": v.get("locale") or v.get("language") or languages,
                    }
                    for v in data.get("voices", [])
                ]
            except Exception as e:
                logger.warning("Failed to list Qwen3-TTS voices: %s", e)
                return []
        if language:
            lang = language.lower()
            lang_base = lang.split("-", 1)[0]
            lang_code = LANG_MAP.get(lang, lang)
            result = []
            for voice in self._voice_cache:
                locale = voice.get("locale", "").lower()
                if not locale:
                    result.append(voice)
                    continue
                locales = [part.strip() for part in locale.replace(";", ",").split(",")]
                if any(
                    part in {lang, lang_base, lang_code} or
                    part.startswith(lang_base) or
                    part.startswith(lang_code)
                    for part in locales
                ):
                    result.append(voice)
            return result
        return list(self._voice_cache)

    def synthesize(
        self,
        text: str,
        language: str,
        voice: str,
        output_path: Path,
        options: dict,
    ) -> TTSResult:
        if not self._is_healthy():
            raise TTSUnavailableError(
                "Qwen3-TTS 服务未运行，请先启动（设置 → 安装 Qwen3-TTS）"
            )

        lang = LANG_MAP.get(language.lower(), language.lower())
        speaker = voice or DEFAULT_VOICE

        if self._cache:
            cached = self._cache.get(text, speaker, lang)
            if cached:
                dur = _get_wav_duration(cached)
                if dur > 0:
                    import shutil
                    shutil.copy2(str(cached), str(output_path))
                    return TTSResult(output_path=output_path, duration_seconds=dur)

        # Detect model capabilities to choose correct endpoint
        caps = self._get_capabilities()
        if caps.get("custom_voice"):
            endpoint = "/synthesize/custom-voice"
            payload = {
                "text": text,
                "speaker": speaker,
                "language": lang,
            }
            instruct = options.get("instruct")
            if instruct:
                payload["instruct"] = instruct
        elif caps.get("voice_clone"):
            endpoint = "/synthesize/voice-clone"
            payload = {
                "text": text,
                "language": lang,
            }
        elif caps.get("voice_design"):
            endpoint = "/synthesize/voice-design"
            payload = {
                "text": text,
                "instruct": options.get("instruct", f"A natural voice speaking {lang}"),
                "language": lang,
            }
        else:
            raise TTSUnavailableError(
                "当前加载的模型不支持任何合成模式"
            )

        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self._base_url}{endpoint}",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                duration_str = resp.headers.get("X-Duration", "0")
                duration = float(duration_str) if duration_str else 0
                out_dir = output_path.parent
                out_dir.mkdir(parents=True, exist_ok=True)
                with open(str(output_path), "wb") as f:
                    f.write(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise TTSUnavailableError(
                f"Qwen3-TTS 合成失败 (HTTP {e.code}): {detail}"
            ) from e
        except Exception as e:
            raise TTSUnavailableError(f"Qwen3-TTS 合成失败: {e}") from e

        if duration <= 0:
            duration = _get_wav_duration(output_path)

        if self._cache and duration > 0:
            self._cache.put(text, speaker, lang, output_path)

        return TTSResult(output_path=output_path, duration_seconds=duration)

    def _is_healthy(self) -> bool:
        try:
            resp = self._request("GET", "/health")
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("status") == "ok"
        except Exception:
            return False

    def _get_capabilities(self) -> Dict:
        try:
            resp = self._request("GET", "/health")
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("capabilities", {})
        except Exception:
            return {}

    def _request(self, method: str, path: str, body: Optional[bytes] = None):
        url = f"{self._base_url}{path}"
        req = urllib.request.Request(url, data=body, method=method)
        if body:
            req.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(req, timeout=5)


def _get_wav_duration(path: Path) -> float:
    try:
        import subprocess
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0
