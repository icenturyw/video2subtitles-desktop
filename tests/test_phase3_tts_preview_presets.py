from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
import pytest


ENGINE_ROOT = Path(__file__).resolve().parents[1] / "localization-engine"
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from engine.sqlite_repository import SQLiteTaskRepository  # noqa: E402
from tts.base import BaseTTSProvider, TTSCapabilities, TTSResult  # noqa: E402
from tts.preview import TTSPreviewError, TTSPreviewService  # noqa: E402
from tts.registry import ProviderRegistry  # noqa: E402


class FakeProvider(BaseTTSProvider):
    def __init__(self, *, delay=0.0, voices=None):
        self.delay = delay
        self.voices = voices if voices is not None else [{"name": "voice-a", "locale": "en"}]
        self.calls = []

    def list_voices(self, language=None):
        return list(self.voices)

    def synthesize(self, text, language, voice, output_path, options):
        if self.delay:
            time.sleep(self.delay)
        self.calls.append((text, language, voice, dict(options)))
        output_path.write_bytes(b"preview-audio")
        return TTSResult(output_path, 1.25)


def _service(tmp_path, *, provider=None, capabilities=None):
    provider = provider or FakeProvider()
    registry = ProviderRegistry()
    registry.register(
        "fake", lambda **_kwargs: provider,
        capabilities=capabilities or TTSCapabilities(
            preview_character_limit=20,
            supported_output_formats=("wav",),
            supported_parameters=("speed",),
            speed=True,
        ),
    )
    return TTSPreviewService(tmp_path, registry, lambda _name: provider, ttl_seconds=10), provider


def test_registry_exposes_declared_provider_capabilities():
    registry = ProviderRegistry()
    caps = TTSCapabilities(speed=True, pitch=True, preview_character_limit=42)
    registry.register("demo", FakeProvider, capabilities=caps)
    assert registry.capabilities("demo") == caps
    assert registry.capabilities("demo").to_dict()["pitch"] is True


def test_preview_generates_audio_without_task_repository(tmp_path):
    service, provider = _service(tmp_path)
    result = service.preview(text="hello", provider_name="fake", voice="voice-a", language="en")
    assert result.path.read_bytes() == b"preview-audio"
    assert result.cached is False
    assert result.duration_seconds == 1.25
    assert provider.calls[0][:3] == ("hello", "en", "voice-a")


def test_preview_rejects_text_over_provider_limit(tmp_path):
    service, _provider = _service(tmp_path)
    with pytest.raises(TTSPreviewError) as caught:
        service.preview(text="x" * 21, provider_name="fake")
    assert caught.value.error_code == "TTS_PREVIEW_TEXT_TOO_LONG"


def test_preview_rejects_unknown_provider(tmp_path):
    service, _provider = _service(tmp_path)
    with pytest.raises(TTSPreviewError) as caught:
        service.preview(text="hello", provider_name="missing")
    assert caught.value.error_code == "TTS_PROVIDER_NOT_FOUND"


def test_preview_rejects_unknown_voice_when_list_is_available(tmp_path):
    service, _provider = _service(tmp_path)
    with pytest.raises(TTSPreviewError) as caught:
        service.preview(text="hello", provider_name="fake", voice="missing")
    assert caught.value.error_code == "TTS_VOICE_NOT_FOUND"


def test_preview_rejects_unsupported_parameter(tmp_path):
    service, _provider = _service(tmp_path)
    with pytest.raises(TTSPreviewError) as caught:
        service.preview(text="hello", provider_name="fake", options={"pitch": 2})
    assert caught.value.error_code == "TTS_PARAMETER_UNSUPPORTED"


def test_preview_only_submits_supported_parameters(tmp_path):
    service, provider = _service(tmp_path)
    service.preview(
        text="hello", provider_name="fake",
        options={"speed": 1.2, "api_key": "do-not-submit", "timeout": 10},
    )
    assert provider.calls[0][3] == {"speed": 1.2, "timeout": 10}


def test_identical_preview_hits_cache(tmp_path):
    service, provider = _service(tmp_path)
    first = service.preview(text="hello", provider_name="fake", options={"speed": 1.1})
    second = service.preview(text="hello", provider_name="fake", options={"speed": 1.1})
    assert first.cached is False
    assert second.cached is True
    assert first.path == second.path
    assert len(provider.calls) == 1


def test_sensitive_values_never_affect_cache_key(tmp_path):
    service, _provider = _service(tmp_path)
    first = service.cache_key("text", "fake", "", "en", {"api_key": "first", "speed": 1})
    second = service.cache_key("text", "fake", "", "en", {"api_key": "second", "speed": 1})
    assert first == second
    assert "first" not in first and "second" not in second


def test_preview_timeout_returns_stable_code(tmp_path):
    service, _provider = _service(tmp_path, provider=FakeProvider(delay=0.2))
    with pytest.raises(TTSPreviewError) as caught:
        service.preview(text="hello", provider_name="fake", timeout_seconds=0.02)
    assert caught.value.error_code == "TTS_PREVIEW_TIMEOUT"


def test_preview_can_be_cancelled_by_id(tmp_path):
    service, _provider = _service(tmp_path, provider=FakeProvider(delay=0.3))
    error = []

    def run():
        try:
            service.preview(text="hello", provider_name="fake", preview_id="cancel-me")
        except TTSPreviewError as exc:
            error.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.time() + 1
    while not service.cancel("cancel-me") and time.time() < deadline:
        time.sleep(0.005)
    thread.join(1)
    assert error and error[0].error_code == "TTS_PREVIEW_CANCELLED"


def test_preview_cleanup_removes_expired_and_partial_files(tmp_path):
    service, _provider = _service(tmp_path)
    old = tmp_path / "old.wav"
    partial = tmp_path / ".partial.wav"
    old.write_bytes(b"old")
    partial.write_bytes(b"partial")
    os.utime(old, (1, 1))
    assert service.cleanup(now=100) == 2
    assert list(tmp_path.iterdir()) == []


def test_voice_preset_crud_round_trip(tmp_path):
    repo = SQLiteTaskRepository(tmp_path)
    created = repo.create_voice_preset(
        name="Narrator", provider="fake", voice_id="voice-a",
        language="en", parameters={"speed": 1.1},
    )
    assert repo.get_voice_preset(created["id"])["parameters"] == {"speed": 1.1}
    updated = repo.update_voice_preset(created["id"], voice_id="voice-b", parameters={"speed": 0.9})
    assert updated["voice_id"] == "voice-b"
    assert repo.list_voice_presets()[0]["parameters"] == {"speed": 0.9}
    assert repo.delete_voice_preset(created["id"]) is True
    assert repo.get_voice_preset(created["id"]) is None


def test_voice_preset_default_is_unique(tmp_path):
    repo = SQLiteTaskRepository(tmp_path)
    first = repo.create_voice_preset(name="One", provider="fake", is_default=True)
    second = repo.create_voice_preset(name="Two", provider="fake", is_default=True)
    assert repo.get_voice_preset(first["id"])["is_default"] is False
    assert repo.get_voice_preset(second["id"])["is_default"] is True
    repo.set_default_voice_preset(first["id"])
    assert repo.default_voice_preset()["id"] == first["id"]


def test_voice_preset_rejects_duplicate_names(tmp_path):
    repo = SQLiteTaskRepository(tmp_path)
    repo.create_voice_preset(name="Same", provider="fake")
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_voice_preset(name="same", provider="fake")


def test_voice_preset_update_rejects_unknown_fields(tmp_path):
    repo = SQLiteTaskRepository(tmp_path)
    preset = repo.create_voice_preset(name="Preset", provider="fake")
    with pytest.raises(ValueError, match="Unsupported"):
        repo.update_voice_preset(preset["id"], api_key="secret")


def test_voice_preset_migration_is_recorded(tmp_path):
    repo = SQLiteTaskRepository(tmp_path)
    with repo.transaction(immediate=False) as conn:
        migrations = dict(conn.execute("SELECT version, name FROM schema_migrations").fetchall())
    assert migrations[1] == "initial_task_repository"
    assert migrations[2] == "voice_presets"


def test_missing_voice_preset_mutations_are_safe(tmp_path):
    repo = SQLiteTaskRepository(tmp_path)
    assert repo.update_voice_preset("missing", name="x") is None
    assert repo.set_default_voice_preset("missing") is None
    assert repo.delete_voice_preset("missing") is False


def test_preset_storage_keeps_parameter_snapshot_independent(tmp_path):
    repo = SQLiteTaskRepository(tmp_path)
    params = {"speed": 1.0}
    preset = repo.create_voice_preset(name="Snapshot", provider="fake", parameters=params)
    params["speed"] = 2.0
    assert repo.get_voice_preset(preset["id"])["parameters"] == {"speed": 1.0}
