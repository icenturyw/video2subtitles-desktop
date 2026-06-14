"""OpenAI-compatible translation provider."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from job_models import TranslationConfig

from .base import (
    AuthError,
    InvalidResponseError,
    RateLimitError,
    TimeoutError_,
    TranslationError,
    TranslationProvider,
)
from .batching import batch_segments, batch_to_request
from .prompts import build_system_prompt, build_translate_prompt
from .response_parser import parse_translation_response, sanitize_for_log

logger = logging.getLogger("translation.openai")

_DEFAULT_TIMEOUT = 120
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_CONCURRENCY = 2


_LANGUAGE_NAMES = {
    "auto": "the source language detected from the subtitle text",
    "zh": "Simplified Chinese",
    "zh-cn": "Simplified Chinese",
    "zh-hans": "Simplified Chinese",
    "zh-tw": "Traditional Chinese",
    "zh-hant": "Traditional Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "cs": "Czech",
}


def _language_name(language: str) -> str:
    text = str(language or "auto").strip()
    return _LANGUAGE_NAMES.get(text.lower(), text or "auto")


def _extract_response_content(result: Any) -> str:
    """Extract assistant content from OpenAI-compatible or local LLM responses."""
    if isinstance(result, list):
        return json.dumps(result, ensure_ascii=False)
    if not isinstance(result, dict):
        raise InvalidResponseError(f"Expected JSON object response, got {type(result).__name__}")

    wrapped_data = result.get("data")
    if isinstance(wrapped_data, dict):
        return _extract_response_content(wrapped_data)

    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, dict):
                            parts.append(str(item.get("text", "")))
                        else:
                            parts.append(str(item))
                    return "".join(parts)
                if content is not None:
                    return str(content)
            if first.get("text") is not None:
                return str(first.get("text"))

    for key in ("content", "text", "response", "output_text"):
        value = result.get(key)
        if value is not None:
            return str(value)

    for key in ("translations", "items", "segments", "result", "data"):
        value = result.get(key)
        if isinstance(value, list):
            return json.dumps({key: value}, ensure_ascii=False)

    safe = sanitize_for_log(json.dumps(result, ensure_ascii=False))
    raise InvalidResponseError(f"Response missing assistant content/choices: {safe}")


def _response_error_detail(response: httpx.Response) -> str:
    """Return a useful, sanitized error detail from an HTTP response."""
    prefix = f"HTTP {response.status_code}"
    try:
        payload = response.json()
        return f"{prefix}: {sanitize_for_log(json.dumps(payload, ensure_ascii=False), 800)}"
    except Exception:
        text = sanitize_for_log(response.text or "", 800)
        return f"{prefix}: {text}" if text else prefix


class OpenAICompatibleProvider(TranslationProvider):
    """Translates subtitles via any OpenAI-compatible chat completions API."""

    def __init__(self):
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=_DEFAULT_TIMEOUT)
        return self._client

    @classmethod
    def provider_name(cls) -> str:
        return "openai_compatible"

    def translate_batch(self, segments: List[Dict], config: TranslationConfig,
                        source_lang: str, target_lang: str,
                        glossary: Optional[str] = None) -> List[Dict]:
        """Translate a single batch of segments."""
        base_url = config.base_url.rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise TranslationError(
                "Translation API address must start with http:// or https://",
                recoverable=False,
            )
        api_key = os.environ.get(config.api_key_env, "")
        model = config.model or "gpt-4o-mini"
        temperature = config.temperature

        segments_json = json.dumps(segments, ensure_ascii=False)
        source_prompt_lang = _language_name(source_lang)
        target_prompt_lang = _language_name(target_lang)
        system_prompt = build_system_prompt(source_prompt_lang, target_prompt_lang)
        user_prompt = build_translate_prompt(
            segments_json=segments_json,
            source_lang=source_prompt_lang,
            target_lang=target_prompt_lang,
            count=len(segments),
            glossary_text=glossary or "",
        )

        headers = {
            "Content-Type": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            # Note: response_format removed - not supported by all OpenAI-compatible servers
        }

        last_error: Optional[Exception] = None
        for attempt in range(max(1, config.retry_count + 1)):
            try:
                response = self.client.post(
                    f"{base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=config.timeout,
                )

                if response.status_code in (401, 403):
                    raise AuthError(_response_error_detail(response))
                elif response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "30"))
                    raise RateLimitError(retry_after=retry_after)
                elif response.status_code >= 400:
                    error_detail = _response_error_detail(response)
                    if response.status_code == 400:
                        logger.error("Translation API returned 400 Bad Request: %s", error_detail)
                    raise TranslationError(
                        error_detail,
                        recoverable=response.status_code >= 500,
                    )

                response.raise_for_status()
                try:
                    result = response.json()
                except json.JSONDecodeError as exc:
                    content_type = response.headers.get("content-type", "")
                    body = sanitize_for_log(response.text or "", 800)
                    raise InvalidResponseError(
                        f"Translation API returned non-JSON response "
                        f"(content-type={content_type}): {body}"
                    ) from exc

                content = _extract_response_content(result)
                expected_ids = [s["id"] for s in segments]

                translations, errors = parse_translation_response(content, expected_ids)
                if errors:
                    logger.warning("Translation parse issues: %s; response: %s",
                                   errors, sanitize_for_log(content))
                    if not translations:
                        raise InvalidResponseError(
                            f"Empty translations after validation: {errors}"
                        )

                return translations

            except (AuthError, InvalidResponseError) as e:
                raise
            except (RateLimitError, TimeoutError_) as e:
                last_error = e
                wait = min(2 ** attempt * 5, 120)
                logger.warning("Translation attempt %d failed: %s; retrying in %ds",
                               attempt + 1, e, wait)
                time.sleep(wait)
            except TranslationError as e:
                if not e.recoverable:
                    raise
                last_error = e
                wait = min(2 ** attempt * 5, 60)
                logger.warning("Translation attempt %d failed: %s; retrying in %ds",
                               attempt + 1, e, wait)
                time.sleep(wait)
            except httpx.TimeoutException as e:
                last_error = TimeoutError_(str(e))
                wait = min(2 ** attempt * 5, 60)
                time.sleep(wait)
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401,):
                    raise AuthError()

                # Log detailed error info for 400 Bad Request
                error_detail = f"HTTP {e.response.status_code}"
                if e.response.status_code == 400:
                    try:
                        error_body = e.response.json()
                        error_detail = f"HTTP 400: {json.dumps(error_body, ensure_ascii=False)[:500]}"
                    except:
                        error_detail = f"HTTP 400: {e.response.text[:500]}"
                    logger.error("Translation API returned 400 Bad Request: %s", error_detail)

                last_error = TranslationError(
                    error_detail,
                    recoverable=e.response.status_code >= 500,
                )
                wait = min(2 ** attempt * 5, 60)
                time.sleep(wait)
            except Exception as e:
                last_error = TranslationError(str(e), recoverable=True)
                wait = min(2 ** attempt * 5, 30)
                time.sleep(wait)

        raise TranslationError(
            f"All retries exhausted: {last_error}",
            recoverable=True,
        )

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None


_PROVIDERS: Dict[str, type] = {}


def register_provider(provider_class: type) -> None:
    name = getattr(provider_class, "provider_name", lambda: "unknown")()
    _PROVIDERS[name] = provider_class


def get_provider(name: str = "openai_compatible") -> TranslationProvider:
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown translation provider: {name}")
    return cls()


def list_providers() -> List[str]:
    return list(_PROVIDERS.keys())


register_provider(OpenAICompatibleProvider)
