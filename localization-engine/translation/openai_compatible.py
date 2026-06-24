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

    anthropic_content = result.get("content")
    if isinstance(anthropic_content, list):
        parts = []
        for item in anthropic_content:
            if isinstance(item, dict):
                text = item.get("text")
                if text is not None:
                    parts.append(str(text))
            else:
                parts.append(str(item))
        if parts:
            return "".join(parts)

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

    output = result.get("output")
    if isinstance(output, list):
        parts = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("text") is not None:
                        parts.append(str(part.get("text")))
                    elif part.get("type") in ("output_text", "text") and part.get("content") is not None:
                        parts.append(str(part.get("content")))
            elif item.get("text") is not None:
                parts.append(str(item.get("text")))
        if parts:
            return "".join(parts)

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
    """Translates subtitles via OpenAI-compatible or Anthropic-compatible APIs."""

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

    def _endpoint_order(self, api_type: str) -> List[str]:
        value = str(api_type or "auto").strip().lower()
        if value in {"responses", "response", "openai_responses"}:
            return ["responses"]
        if value in {"chat", "chat_completions", "chat-completions"}:
            return ["chat_completions"]
        if value in {"anthropic", "anthropic_messages", "messages", "claude"}:
            return ["anthropic_messages"]
        return ["responses", "chat_completions", "anthropic_messages"]

    def _auto_endpoint_order(self, api_type: str, model: str, base_url: str) -> List[str]:
        explicit = self._endpoint_order(api_type)
        value = str(api_type or "auto").strip().lower()
        if value not in {"", "auto"}:
            return explicit

        model_lower = str(model or "").lower()
        base_lower = str(base_url or "").lower()
        if "claude" in model_lower or "anthropic" in base_lower:
            return ["anthropic_messages", "responses", "chat_completions"]
        return explicit

    def _build_payload(self, endpoint: str, model: str, system_prompt: str,
                       user_prompt: str, temperature: float) -> Dict[str, Any]:
        if endpoint == "responses":
            return {
                "model": model,
                "instructions": system_prompt,
                "input": user_prompt,
            }
        if endpoint == "anthropic_messages":
            return {
                "model": model,
                "max_tokens": 4096,
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": user_prompt},
                ],
            }
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            # Note: response_format removed - not supported by all OpenAI-compatible servers
        }

    def _endpoint_url(self, base_url: str, endpoint: str) -> str:
        if endpoint == "responses":
            return f"{base_url}/responses"
        if endpoint == "anthropic_messages":
            if base_url.endswith("/v1"):
                return f"{base_url}/messages"
            return f"{base_url}/v1/messages"
        return f"{base_url}/chat/completions"

    def _is_endpoint_fallback_error(self, response: httpx.Response) -> bool:
        if response.status_code in (404, 405):
            return True
        if response.status_code != 400:
            return False
        detail = _response_error_detail(response).lower()
        markers = (
            "responses", "chat/completions", "unsupported endpoint",
            "unknown endpoint", "unknown url", "not found", "not supported",
            "messages",
        )
        return any(marker in detail for marker in markers)

    def _can_fallback_endpoint(self, api_type: str, endpoint_index: int,
                               endpoints: List[str]) -> bool:
        value = str(api_type or "auto").strip().lower()
        return value in {"", "auto"} and endpoint_index < len(endpoints) - 1

    def _post_endpoint(self, endpoint: str, base_url: str,
                       payload: Dict[str, Any], headers: Dict[str, str],
                       timeout: int) -> httpx.Response:
        endpoint_headers = dict(headers)
        if endpoint == "anthropic_messages":
            endpoint_headers["anthropic-beta"] = "context-1m-2025-08-07"
        response = self.client.post(
            self._endpoint_url(base_url, endpoint),
            json=payload,
            headers=endpoint_headers,
            timeout=timeout,
        )
        if response.status_code == 400 and "temperature" in payload:
            detail = _response_error_detail(response).lower()
            if "temperature" in detail and ("unsupported" in detail or "unknown" in detail):
                slim_payload = dict(payload)
                slim_payload.pop("temperature", None)
                response = self.client.post(
                    self._endpoint_url(base_url, endpoint),
                    json=slim_payload,
                    headers=endpoint_headers,
                    timeout=timeout,
                )
        return response

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
            "User-Agent": "Video2Subtitles/1.0",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"

        endpoints = self._auto_endpoint_order(
            getattr(config, "api_type", "auto"), model, base_url
        )

        last_error: Optional[Exception] = None
        max_attempts = max(1, config.retry_count + 1)
        for attempt in range(max_attempts):
            for endpoint_index, endpoint in enumerate(endpoints):
                payload = self._build_payload(
                    endpoint, model, system_prompt, user_prompt, temperature
                )
                try:
                    response = self._post_endpoint(
                        endpoint, base_url, payload, headers, config.timeout
                    )

                    if response.status_code in (401, 403):
                        raise AuthError(_response_error_detail(response))
                    elif response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", "30"))
                        raise RateLimitError(retry_after=retry_after)
                    elif response.status_code >= 400:
                        error_detail = _response_error_detail(response)
                        if self._is_endpoint_fallback_error(response):
                            last_error = TranslationError(error_detail, recoverable=False)
                            logger.warning("%s endpoint unavailable: %s", endpoint, error_detail)
                            continue
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
                        if self._can_fallback_endpoint(
                            getattr(config, "api_type", "auto"), endpoint_index, endpoints
                        ):
                            last_error = InvalidResponseError(
                                f"{endpoint} endpoint returned non-JSON response "
                                f"(content-type={content_type}): {body}"
                            )
                            logger.warning("%s endpoint returned non-JSON response; "
                                           "trying next endpoint: %s",
                                           endpoint, body)
                            continue
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
                            if self._can_fallback_endpoint(
                                getattr(config, "api_type", "auto"), endpoint_index, endpoints
                            ):
                                last_error = InvalidResponseError(
                                    f"{endpoint} endpoint returned invalid translation JSON: {errors}"
                                )
                                logger.warning("%s endpoint returned invalid translation JSON; "
                                               "trying next endpoint", endpoint)
                                continue
                            raise InvalidResponseError(
                                f"Empty translations after validation: {errors}"
                            )

                    return translations

                except (AuthError, InvalidResponseError) as e:
                    raise
                except (RateLimitError, TimeoutError_) as e:
                    last_error = e
                    if attempt < max_attempts - 1:
                        wait = min(2 ** attempt * 5, 120)
                        logger.warning("Translation attempt %d failed: %s; retrying in %ds",
                                       attempt + 1, e, wait)
                        time.sleep(wait)
                    break
                except TranslationError as e:
                    if not e.recoverable:
                        raise
                    last_error = e
                    if attempt < max_attempts - 1:
                        wait = min(2 ** attempt * 5, 60)
                        logger.warning("Translation attempt %d failed: %s; retrying in %ds",
                                       attempt + 1, e, wait)
                        time.sleep(wait)
                    break
                except httpx.TimeoutException as e:
                    last_error = TimeoutError_(str(e))
                    if attempt < max_attempts - 1:
                        wait = min(2 ** attempt * 5, 60)
                        time.sleep(wait)
                    break
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
                    if attempt < max_attempts - 1:
                        wait = min(2 ** attempt * 5, 60)
                        time.sleep(wait)
                    break
                except Exception as e:
                    last_error = TranslationError(str(e), recoverable=True)
                    if attempt < max_attempts - 1:
                        wait = min(2 ** attempt * 5, 30)
                        time.sleep(wait)
                    break

            if isinstance(last_error, TranslationError) and not last_error.recoverable:
                raise last_error

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
