from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from process_utils import hidden_subprocess_kwargs
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
DEFAULT_MODEL_BY_MODE = {
    "auto": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "custom_voice": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "voice_clone": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    "voice_design": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
}
DEFAULT_STABLE_SEED = 42


def _clean_options(options: dict) -> Dict:
    cleaned = dict(options or {})
    mode = str(cleaned.get("qwen_mode") or cleaned.get("mode") or "auto").strip().lower()
    if mode not in {"auto", "custom_voice", "voice_design", "voice_clone"}:
        mode = "auto"
    cleaned["qwen_mode"] = mode
    seed = cleaned.get("seed")
    seed_policy = str(cleaned.get("seed_policy", "") or "").lower()
    if seed in ("", None):
        if seed_policy == "random":
            cleaned.pop("seed", None)
            cleaned["seed_policy"] = "random"
        else:
            cleaned["seed"] = DEFAULT_STABLE_SEED
            cleaned["seed_policy"] = "default_stable"
    else:
        try:
            seed_int = int(seed)
        except (TypeError, ValueError):
            cleaned["seed"] = DEFAULT_STABLE_SEED
            cleaned["seed_policy"] = "default_stable"
        else:
            if seed_int < 0:
                cleaned.pop("seed", None)
                cleaned["seed_policy"] = "random"
            else:
                cleaned["seed"] = seed_int
                cleaned["seed_policy"] = str(cleaned.get("seed_policy") or "explicit")
    return cleaned


def _estimate_max_tokens(text: str) -> int:
    """Estimate a safe max_new_tokens value for a given text.

    Conservative estimates per character:
        - CJK: ~2 tokens per character
        - Latin/Cyrillic: ~1.5 tokens per character
        - Fallback: ~2 tokens per character + margin
    """
    if not text:
        return 256
    cjk_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff'
                    or '\u3040' <= c <= '\u30ff' or '\uac00' <= c <= '\ud7af')
    other_chars = len(text) - cjk_chars
    estimated = int(cjk_chars * 2.0 + other_chars * 1.5) + 256
    return max(256, min(4096, estimated))


def _generation_options(options: dict, text: str = "") -> Dict:
    payload: Dict[str, object] = {}
    for key in ("max_new_tokens", "seed"):
        value = options.get(key)
        if value in ("", None):
            if key == "max_new_tokens" and text:
                payload[key] = _estimate_max_tokens(text)
            continue
        try:
            payload[key] = int(value)
        except (TypeError, ValueError):
            if key == "max_new_tokens" and text and "max_new_tokens" not in payload:
                payload[key] = _estimate_max_tokens(text)
    for key in ("top_p", "temperature"):
        value = options.get(key)
        if value in ("", None):
            continue
        try:
            payload[key] = float(value)
        except (TypeError, ValueError):
            pass
    return payload


def _cache_variant(mode: str, options: dict, caps: Dict, speaker: str) -> str:
    relevant = {
        "mode": mode,
        "speaker": speaker,
        "capabilities": sorted(k for k, v in caps.items() if v),
        "instruct": options.get("instruct", ""),
        "voice_clone_prompt_id": options.get("voice_clone_prompt_id", ""),
        "ref_audio": options.get("ref_audio", ""),
        "ref_text": options.get("ref_text", ""),
        "x_vector_only_mode": bool(options.get("x_vector_only_mode", False)),
        "max_new_tokens": options.get("max_new_tokens", ""),
        "seed": options.get("seed", ""),
        "seed_policy": options.get("seed_policy", ""),
        "top_p": options.get("top_p", ""),
        "temperature": options.get("temperature", ""),
    }
    return json.dumps(relevant, ensure_ascii=False, sort_keys=True)


class Qwen3TTSProvider:
    supports_concurrency = False

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

        options = _clean_options(options)
        self._ensure_model_loaded(options)
        lang = LANG_MAP.get(language.lower(), language.lower())
        speaker = voice or DEFAULT_VOICE
        caps = self._get_capabilities()
        requested_mode = options.get("qwen_mode", "auto")
        endpoint = ""
        payload: Dict[str, object] = {}

        if requested_mode == "voice_clone":
            if not caps.get("voice_clone"):
                raise TTSUnavailableError("当前 Qwen3-TTS 模型不支持声音克隆")
            endpoint = "/synthesize/voice-clone"
            payload = {
                "text": text,
                "language": lang,
                "ref_audio": options.get("ref_audio") or None,
                "ref_text": options.get("ref_text") or None,
                "x_vector_only_mode": bool(options.get("x_vector_only_mode", False)),
                "voice_clone_prompt_id": options.get("voice_clone_prompt_id") or None,
            }
        elif requested_mode == "voice_design":
            if not caps.get("voice_design"):
                raise TTSUnavailableError("当前 Qwen3-TTS 模型不支持音色设计")
            endpoint = "/synthesize/voice-design"
            payload = {
                "text": text,
                "instruct": options.get("instruct") or f"A natural voice speaking {lang}",
                "language": lang,
            }
        elif requested_mode == "custom_voice":
            if not caps.get("custom_voice"):
                raise TTSUnavailableError("当前 Qwen3-TTS 模型不支持预设音色")
            endpoint = "/synthesize/custom-voice"
            payload = {
                "text": text,
                "speaker": speaker,
                "language": lang,
            }
            if options.get("instruct"):
                payload["instruct"] = options["instruct"]
        elif caps.get("custom_voice"):
            endpoint = "/synthesize/custom-voice"
            payload = {
                "text": text,
                "speaker": speaker,
                "language": lang,
            }
            if options.get("instruct"):
                payload["instruct"] = options["instruct"]
            requested_mode = "custom_voice"
        elif caps.get("voice_clone"):
            endpoint = "/synthesize/voice-clone"
            payload = {
                "text": text,
                "language": lang,
                "ref_audio": options.get("ref_audio") or None,
                "ref_text": options.get("ref_text") or None,
                "x_vector_only_mode": bool(options.get("x_vector_only_mode", False)),
                "voice_clone_prompt_id": options.get("voice_clone_prompt_id") or None,
            }
            requested_mode = "voice_clone"
        elif caps.get("voice_design"):
            endpoint = "/synthesize/voice-design"
            payload = {
                "text": text,
                "instruct": options.get("instruct") or f"A natural voice speaking {lang}",
                "language": lang,
            }
            requested_mode = "voice_design"
        else:
            raise TTSUnavailableError("当前加载的模型不支持任何合成模式")

        payload.update(_generation_options(options, text))
        payload = {k: v for k, v in payload.items() if v is not None}
        cache_variant = _cache_variant(requested_mode, options, caps, speaker)

        if self._cache:
            cached = self._cache.get(text, speaker, lang, cache_variant)
            if cached:
                dur = _get_wav_duration(cached)
                if dur > 0:
                    import shutil
                    shutil.copy2(str(cached), str(output_path))
                    return TTSResult(output_path=output_path, duration_seconds=dur,
                                     cached=True, mode=requested_mode)

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

        if duration > 0 and text:
            chars_per_sec = len(text) / duration if duration > 0.01 else 0
            is_cjk = any('\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff'
                        or '\uac00' <= c <= '\ud7af' for c in text[:50])
            min_cps = 3.0 if is_cjk else 6.0
            if chars_per_sec > 80 or (chars_per_sec < min_cps and duration < 0.3):
                logger.warning(
                    "Possible TTS truncation: text=%d chars, duration=%.2fs, "
                    "cps=%.1f, language=%s",
                    len(text), duration, chars_per_sec, lang,
                )

        if self._cache and duration > 0:
            self._cache.put(text, speaker, lang, output_path, cache_variant)

        return TTSResult(output_path=output_path, duration_seconds=duration,
                         cached=False, mode=requested_mode)

    def _desired_model_id(self, options: dict) -> str:
        configured = (
            str(options.get("qwen_model", "") or "").strip()
            or str(options.get("model", "") or "").strip()
            or os.environ.get("V2S_QWEN3_TTS_MODEL", "").strip()
            or os.environ.get("QWEN3_TTS_MODEL", "").strip()
        )
        if configured:
            return configured
        mode = str(options.get("qwen_mode") or "auto").strip().lower()
        return DEFAULT_MODEL_BY_MODE.get(mode, DEFAULT_MODEL_BY_MODE["auto"])

    def _ensure_model_loaded(self, options: dict) -> None:
        desired_model = self._desired_model_id(options)
        try:
            resp = self._request("GET", "/models/loaded")
            loaded = json.loads(resp.read().decode("utf-8")).get("model_id")
        except Exception:
            loaded = None
        if loaded == desired_model:
            return
        if loaded:
            try:
                self._request("POST", "/models/unload", timeout=30)
                logger.info("Qwen3-TTS model unloaded before switching: %s", loaded)
            except Exception as e:
                logger.warning("Failed to unload Qwen3-TTS model %s: %s", loaded, e)

        body = json.dumps({"model_id": desired_model}).encode("utf-8")
        try:
            self._request("POST", "/models/load", body=body, timeout=1800)
            logger.info("Qwen3-TTS model auto-loaded: %s", desired_model)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise TTSUnavailableError(
                f"Qwen3-TTS 自动加载模型失败 (HTTP {e.code}): {detail}"
            ) from e
        except Exception as e:
            raise TTSUnavailableError(f"Qwen3-TTS 自动加载模型失败: {e}") from e

    def create_voice_clone_prompt(
        self,
        ref_audio: str,
        ref_text: str = "",
        x_vector_only_mode: bool = False,
    ) -> str:
        payload = {
            "ref_audio": ref_audio,
            "ref_text": ref_text or None,
            "x_vector_only_mode": bool(x_vector_only_mode),
        }
        body = json.dumps(payload).encode("utf-8")
        resp = self._request("POST", "/voice-clone/prompts", body=body)
        data = json.loads(resp.read().decode("utf-8"))
        return str(data.get("prompt_id") or "")

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

    def _request(self, method: str, path: str, body: Optional[bytes] = None,
                 timeout: float = 5):
        url = f"{self._base_url}{path}"
        req = urllib.request.Request(url, data=body, method=method)
        if body:
            req.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(req, timeout=timeout)


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
            **hidden_subprocess_kwargs(),
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0
