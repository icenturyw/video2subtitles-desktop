"""Short-lived, capability-aware TTS previews outside formal task history."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from tts.base import BaseTTSProvider, TTSCapabilities
from tts.registry import ProviderRegistry

from engine.runtime import ModelResourceError, ModelResourceManager, qwen3_tts_definition


SENSITIVE_MARKERS = ("api_key", "apikey", "secret", "token", "access_key", "credential", "password")
GENERIC_OPTIONS = {"timeout"}


class TTSPreviewError(RuntimeError):
    def __init__(self, message: str, error_code: str = "TTS_PREVIEW_FAILED") -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class TTSPreviewResult:
    preview_id: str
    path: Path
    media_type: str
    cached: bool
    duration_seconds: float = 0.0


class TTSPreviewService:
    def __init__(
        self,
        cache_dir: str | Path,
        registry: ProviderRegistry,
        provider_factory: Callable[[str], BaseTTSProvider],
        *,
        ttl_seconds: float = 3600,
        maximum_timeout_seconds: float = 300,
        model_resources: ModelResourceManager | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.registry = registry
        self.provider_factory = provider_factory
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.maximum_timeout_seconds = max(1.0, float(maximum_timeout_seconds))
        self.model_resources = model_resources
        self._cancel: dict[str, threading.Event] = {}
        self._results: dict[str, Path] = {}
        self._lock = threading.RLock()

    def preview(
        self,
        *,
        text: str,
        provider_name: str,
        voice: str = "",
        language: str = "",
        options: dict[str, Any] | None = None,
        preview_id: str = "",
        timeout_seconds: float = 60,
    ) -> TTSPreviewResult:
        cleaned_text = str(text or "").strip()
        if not cleaned_text:
            raise TTSPreviewError("Preview text is required", "TTS_PREVIEW_TEXT_REQUIRED")
        try:
            canonical = self.registry.canonical_name(provider_name)
        except ValueError as exc:
            raise TTSPreviewError(str(exc), "TTS_PROVIDER_NOT_FOUND") from exc
        capabilities = self.registry.capabilities(canonical)
        if len(cleaned_text) > capabilities.preview_character_limit:
            raise TTSPreviewError(
                f"Preview text exceeds {capabilities.preview_character_limit} characters",
                "TTS_PREVIEW_TEXT_TOO_LONG",
            )
        filtered = self.validate_options(capabilities, options or {})
        provider = self.provider_factory(canonical)
        voices = provider.list_voices(language or None) if capabilities.voice_list else []
        if voice and voices and not any(
            voice == str(item.get("name") or item.get("id") or "") for item in voices
        ):
            raise TTSPreviewError(f"Voice does not exist: {voice}", "TTS_VOICE_NOT_FOUND")

        preview_id = _clean_preview_id(preview_id or uuid.uuid4().hex)
        cancel_event = threading.Event()
        with self._lock:
            self._cancel[preview_id] = cancel_event
        model_lease = None
        completed = None
        try:
            extension = _output_extension(capabilities, filtered)
            key = self.cache_key(cleaned_text, canonical, voice, language, filtered)
            cached_path = self.cache_dir / f"{key}.{extension}"
            if self._fresh(cached_path):
                with self._lock:
                    self._results[preview_id] = cached_path
                return TTSPreviewResult(
                    preview_id, cached_path, _media_type(extension), True,
                    _duration(cached_path),
                )

            if self.model_resources and canonical == "qwen3-tts":
                try:
                    model_lease = self.model_resources.acquire(qwen3_tts_definition(filtered))
                except ModelResourceError as exc:
                    raise TTSPreviewError(str(exc), exc.error_code) from exc

            work_path = self.cache_dir / f".{preview_id}.{uuid.uuid4().hex}.part.{extension}"
            outcome: list[Any] = []
            errors: list[BaseException] = []
            completed = threading.Event()

            def synthesize() -> None:
                try:
                    outcome.append(provider.synthesize(
                        cleaned_text, language, voice, work_path, dict(filtered)
                    ))
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    completed.set()

            thread = threading.Thread(target=synthesize, name=f"tts-preview-{preview_id[:8]}", daemon=True)
            thread.start()
            deadline = time.monotonic() + min(
                self.maximum_timeout_seconds, max(0.1, float(timeout_seconds))
            )
            while not completed.wait(0.05):
                if cancel_event.is_set():
                    work_path.unlink(missing_ok=True)
                    raise TTSPreviewError("Preview was cancelled", "TTS_PREVIEW_CANCELLED")
                if time.monotonic() >= deadline:
                    cancel_event.set()
                    work_path.unlink(missing_ok=True)
                    raise TTSPreviewError("Preview timed out", "TTS_PREVIEW_TIMEOUT")
            if cancel_event.is_set():
                work_path.unlink(missing_ok=True)
                raise TTSPreviewError("Preview was cancelled", "TTS_PREVIEW_CANCELLED")
            if errors:
                work_path.unlink(missing_ok=True)
                raise TTSPreviewError(str(errors[0])) from errors[0]
            if not work_path.is_file() or work_path.stat().st_size <= 0:
                raise TTSPreviewError("Provider did not produce preview audio")
            os.replace(work_path, cached_path)
            with self._lock:
                self._results[preview_id] = cached_path
            duration = float(getattr(outcome[0], "duration_seconds", 0.0)) if outcome else 0.0
            return TTSPreviewResult(
                preview_id, cached_path, _media_type(extension), False, duration
            )
        finally:
            if model_lease is not None:
                if completed is not None and not completed.is_set():
                    threading.Thread(
                        target=lambda: (completed.wait(), model_lease.release()),
                        name=f"tts-preview-release-{preview_id[:8]}",
                        daemon=True,
                    ).start()
                else:
                    model_lease.release()
            with self._lock:
                self._cancel.pop(preview_id, None)

    def cancel(self, preview_id: str) -> bool:
        with self._lock:
            event = self._cancel.get(_clean_preview_id(preview_id))
        if event is None:
            return False
        event.set()
        return True

    def result_path(self, preview_id: str) -> Path | None:
        with self._lock:
            path = self._results.get(_clean_preview_id(preview_id))
        return path if path and self._fresh(path) else None

    def cleanup(self, *, now: float | None = None) -> int:
        current = time.time() if now is None else now
        removed = 0
        for path in self.cache_dir.iterdir():
            if not path.is_file():
                continue
            try:
                expired = path.name.startswith(".") or current - path.stat().st_mtime > self.ttl_seconds
                if expired:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
        with self._lock:
            self._results = {key: path for key, path in self._results.items() if path.exists()}
        return removed

    @staticmethod
    def validate_options(capabilities: TTSCapabilities, options: dict[str, Any]) -> dict[str, Any]:
        allowed = set(capabilities.supported_parameters) | GENERIC_OPTIONS
        unsupported = sorted(
            key for key, value in options.items()
            if value not in (None, "") and key not in allowed and not _sensitive(key)
        )
        if unsupported:
            raise TTSPreviewError(
                f"Provider does not support parameters: {', '.join(unsupported)}",
                "TTS_PARAMETER_UNSUPPORTED",
            )
        return {
            key: value for key, value in options.items()
            if key in allowed and value not in (None, "") and not _sensitive(key)
        }

    @staticmethod
    def cache_key(
        text: str,
        provider: str,
        voice: str,
        language: str,
        options: dict[str, Any],
    ) -> str:
        safe_options = _scrub_sensitive(options)
        payload = {
            "text": text,
            "provider": provider,
            "voice": voice,
            "language": language,
            "options": safe_options,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _fresh(self, path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_size > 0 and time.time() - path.stat().st_mtime <= self.ttl_seconds
        except OSError:
            return False


def _sensitive(key: str) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(marker in normalized for marker in SENSITIVE_MARKERS)


def _scrub_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _scrub_sensitive(item)
            for key, item in value.items()
            if not _sensitive(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_scrub_sensitive(item) for item in value]
    return value


def _clean_preview_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))[:64]
    if not cleaned:
        raise TTSPreviewError("Invalid preview id", "TTS_PREVIEW_ID_INVALID")
    return cleaned


def _output_extension(capabilities: TTSCapabilities, options: dict) -> str:
    value = next((
        options.get(key) for key in (
            "openai_tts_format", "volcengine_format", "fish_audio_format", "format"
        ) if options.get(key)
    ), "")
    extension = str(value or (capabilities.supported_output_formats or ("wav",))[0]).lower()
    extension = re.sub(r"[^a-z0-9]", "", extension)
    return "ogg" if extension == "oggopus" else (extension or "wav")


def _media_type(extension: str) -> str:
    return {
        "mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/opus",
        "ogg": "audio/ogg", "aac": "audio/aac", "flac": "audio/flac",
        "pcm": "application/octet-stream",
    }.get(extension, "application/octet-stream")


def _duration(path: Path) -> float:
    try:
        import wave
        with wave.open(str(path), "rb") as handle:
            return handle.getnframes() / float(handle.getframerate())
    except Exception:
        return 0.0
