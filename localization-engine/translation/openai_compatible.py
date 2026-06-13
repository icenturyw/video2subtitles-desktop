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
        api_key = os.environ.get(config.api_key_env, "")
        model = config.model or "gpt-4o-mini"
        temperature = config.temperature

        segments_json = json.dumps(segments, ensure_ascii=False)
        system_prompt = build_system_prompt(source_lang, target_lang)
        user_prompt = build_translate_prompt(
            segments_json=segments_json,
            source_lang=source_lang,
            target_lang=target_lang,
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
            "response_format": {"type": "json_object"},
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

                if response.status_code == 401:
                    raise AuthError()
                elif response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "30"))
                    raise RateLimitError(retry_after=retry_after)
                elif response.status_code >= 500:
                    raise TranslationError(
                        f"Server error: {response.status_code}",
                        recoverable=True,
                    )

                response.raise_for_status()
                result = response.json()

                content = result["choices"][0]["message"]["content"]
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
            except httpx.TimeoutException as e:
                last_error = TimeoutError_(str(e))
                wait = min(2 ** attempt * 5, 60)
                time.sleep(wait)
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401,):
                    raise AuthError()
                last_error = TranslationError(
                    f"HTTP error: {e.response.status_code}",
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
