from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


ENGINE_ROOT = Path(__file__).resolve().parents[1] / "localization-engine"
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from engine.runtime.preflight import PreflightIssue, PreflightResult  # noqa: E402
from tts.preview import TTSPreviewResult  # noqa: E402


@pytest.fixture()
def engine_api(tmp_path):
    spec = importlib.util.spec_from_file_location(
        f"phase3_engine_main_{tmp_path.name}", ENGINE_ROOT / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.DATA_DIR = tmp_path / "data"
    module.start_pipeline = MagicMock()
    with TestClient(module.app) as client:
        yield module, client, tmp_path


def _source(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    subtitle = workspace / "source.srt"
    subtitle.write_text(
        "1\n00:00:00,500 --> 00:00:02,000\nHello world\n\n"
        "2\n00:00:02,200 --> 00:00:04,000\nSecond line\n",
        encoding="utf-8",
    )
    return workspace, subtitle


def _create_job(client, tmp_path, **extra):
    workspace, subtitle = _source(tmp_path)
    payload = {
        "workspace_dir": str(workspace),
        "source_subtitle": str(subtitle),
        "source_language": "en",
        "target_language": "en",
        **extra,
    }
    response = client.post("/jobs", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["job_id"]


def test_runtime_metrics_endpoint_returns_models_without_sqlite_sampling(engine_api):
    _module, client, _tmp = engine_api
    response = client.get("/runtime/metrics?refresh=true")
    assert response.status_code == 200
    data = response.json()
    assert "metrics" in data
    assert "models" in data


def test_tts_provider_endpoint_exposes_capabilities(engine_api):
    _module, client, _tmp = engine_api
    response = client.get("/tts/providers")
    assert response.status_code == 200
    providers = response.json()["providers"]
    assert providers
    assert all("capabilities" in provider for provider in providers)
    assert all("preview_character_limit" in provider["capabilities"] for provider in providers)


def test_strict_preflight_blocks_errors_and_requires_warning_confirmation(engine_api):
    module, client, tmp_path = engine_api
    workspace, subtitle = _source(tmp_path)
    payload = {"workspace_dir": str(workspace), "source_subtitle": str(subtitle)}
    module._state["preflight_checker"].check = MagicMock(return_value=PreflightResult(
        errors=[PreflightIssue("error", "FFMPEG_NOT_FOUND", "missing")]
    ))
    blocked = client.post("/jobs?enforce_preflight=true", json=payload)
    assert blocked.status_code == 422
    assert blocked.json()["detail"]["error_code"] == "PREFLIGHT_FAILED"
    module._state["preflight_checker"].check = MagicMock(return_value=PreflightResult(
        warnings=[PreflightIssue("warning", "DISK_SPACE_LOW", "low")]
    ))
    warning = client.post("/jobs?enforce_preflight=true", json=payload)
    assert warning.status_code == 409
    confirmed = client.post(
        "/jobs?enforce_preflight=true&confirm_warnings=true", json=payload
    )
    assert confirmed.status_code == 200


def test_voice_preset_crud_and_default_api(engine_api):
    _module, client, _tmp = engine_api
    created = client.post("/voice-presets", json={
        "name": "Narrator", "provider": "edge-tts", "voice_id": "voice",
        "language": "en", "parameters": {"rate": "+10%"},
    })
    assert created.status_code == 200
    preset = created.json()
    updated = client.put(f"/voice-presets/{preset['id']}", json={"voice_id": "voice-2"})
    assert updated.json()["voice_id"] == "voice-2"
    defaulted = client.post(f"/voice-presets/{preset['id']}/default")
    assert defaulted.json()["is_default"] is True
    assert client.get("/voice-presets").json()["presets"][0]["id"] == preset["id"]
    assert client.delete(f"/voice-presets/{preset['id']}").status_code == 200


def test_voice_preset_api_rejects_sensitive_parameters(engine_api):
    _module, client, _tmp = engine_api
    response = client.post("/voice-presets", json={
        "name": "Unsafe", "provider": "edge-tts", "parameters": {"api_key": "secret"},
    })
    assert response.status_code == 400
    assert "Sensitive" in response.text


def test_task_stores_applied_voice_preset_snapshot(engine_api):
    module, client, tmp_path = engine_api
    preset = client.post("/voice-presets", json={
        "name": "Snapshot", "provider": "edge-tts", "voice_id": "voice",
        "parameters": {"rate": "+5%"},
    }).json()
    job_id = _create_job(client, tmp_path, tts_preset_id=preset["id"], tts_options={"pitch": "+2Hz"})
    request = module._store().get(job_id).request_payload
    assert request["tts_provider"] == "edge-tts"
    assert request["tts_options"] == {"rate": "+5%", "pitch": "+2Hz"}
    client.put(f"/voice-presets/{preset['id']}", json={"parameters": {"rate": "-5%"}})
    assert module._store().get(job_id).request_payload["tts_options"]["rate"] == "+5%"


def test_tts_preview_endpoint_returns_audio_headers_without_task_history(engine_api):
    module, client, tmp_path = engine_api
    audio = tmp_path / "preview.wav"
    audio.write_bytes(b"audio")

    class FakePreview:
        def preview(self, **kwargs):
            assert "api_key" not in kwargs["options"]
            return TTSPreviewResult(kwargs["preview_id"], audio, "audio/wav", False, 1.0)

        def cancel(self, _preview_id):
            return True

    module._state["tts_preview"] = FakePreview()
    before = len(module._store().list_all())
    response = client.post("/tts/preview", json={
        "preview_id": "preview-id", "provider": "edge-tts", "text": "hello",
        "options": {"api_key": "never-persist"},
    })
    assert response.status_code == 200
    assert response.headers["X-Preview-Id"] == "preview-id"
    assert len(module._store().list_all()) == before


def test_subtitle_document_draft_and_formal_revision_api(engine_api):
    _module, client, tmp_path = engine_api
    job_id = _create_job(client, tmp_path)
    opened = client.get(f"/jobs/{job_id}/subtitles")
    assert opened.status_code == 200
    document = opened.json()["document"]
    first_id = document["cues"][0]["cue_id"]
    document["cues"][0]["source_text"] = "Edited"
    draft = client.put(
        f"/jobs/{job_id}/subtitles/draft",
        json={"document": document, "base_version": document["version"]},
    )
    assert draft.status_code == 200
    reopened = client.get(f"/jobs/{job_id}/subtitles").json()
    assert reopened["draft"]["cues"][0]["cue_id"] == first_id
    saved = client.post(
        f"/jobs/{job_id}/subtitles/revisions",
        json={"document": document, "base_version": document["version"]},
    )
    assert saved.status_code == 200
    assert saved.json()["document"]["version"] == document["version"] + 1


def test_subtitle_api_returns_optimistic_conflict(engine_api):
    _module, client, tmp_path = engine_api
    job_id = _create_job(client, tmp_path)
    document = client.get(f"/jobs/{job_id}/subtitles").json()["document"]
    first = client.post(
        f"/jobs/{job_id}/subtitles/revisions",
        json={"document": document, "base_version": document["version"]},
    )
    assert first.status_code == 200
    stale = client.post(
        f"/jobs/{job_id}/subtitles/revisions",
        json={"document": document, "base_version": document["version"]},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["error_code"] == "SUBTITLE_VERSION_CONFLICT"


def test_subtitle_edit_endpoint_runs_command_and_validation(engine_api):
    _module, client, tmp_path = engine_api
    job_id = _create_job(client, tmp_path)
    document = client.get(f"/jobs/{job_id}/subtitles").json()["document"]
    cue_id = document["cues"][0]["cue_id"]
    edited = client.post(f"/jobs/{job_id}/subtitles/edit", json={
        "document": document,
        "command": {"type": "update", "cue_id": cue_id, "changes": {"start_ms": -1}},
    })
    assert edited.status_code == 200
    validated = client.post(
        f"/jobs/{job_id}/subtitles/validate",
        json={"document": edited.json()["document"]},
    )
    assert any(issue["code"] == "SUBTITLE_NEGATIVE_TIME" for issue in validated.json()["issues"])


def test_task_detail_includes_runtime_models_events_and_whitelisted_guidance(engine_api):
    module, client, tmp_path = engine_api
    job_id = _create_job(client, tmp_path)
    module._store().update(job_id, status="error", error_code="MODEL_RESOURCE_UNAVAILABLE")
    detail = client.get(f"/jobs/{job_id}/detail")
    assert detail.status_code == 200
    payload = detail.json()
    assert {"runtime", "loaded_models", "events", "guidance", "log_endpoint"} <= set(payload)
    assert {action["action_id"] for action in payload["guidance"]} <= module.ErrorGuidanceRegistry.ALLOWED_ACTIONS
