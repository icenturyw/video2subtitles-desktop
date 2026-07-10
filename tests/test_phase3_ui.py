from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = ROOT / "localization-engine"
for path in (ROOT, ENGINE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from subtitles import SubtitleDocument, UpdateCue  # noqa: E402
from ui.runtime_dashboard import RuntimeDashboardDialog  # noqa: E402
from ui.subtitle_timeline_dialog import (  # noqa: E402
    SubtitleTimelineDialog,
    _format_ms,
    _parse_time,
)
from ui.tts_preview_dialog import TTSPreviewDialog  # noqa: E402
import ui.subtitle_timeline_dialog as timeline_module  # noqa: E402
import ui.tts_preview_dialog as preview_module  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def application():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def disable_multimedia_in_headless_tests(monkeypatch):
    monkeypatch.setattr(timeline_module, "QMediaPlayer", None)
    monkeypatch.setattr(timeline_module, "QVideoWidget", None)
    monkeypatch.setattr(preview_module, "QMediaPlayer", None)


def _document():
    segments = [
        SimpleNamespace(start=0.5, end=2.0, text="Hello", translation="你好"),
        SimpleNamespace(start=2.2, end=4.0, text="World", translation="世界"),
    ]
    document = SubtitleDocument.from_segments(
        "job", segments, source_language="en", target_language="zh-CN"
    )
    document.version = 1
    return document


def test_timeline_time_format_round_trip_is_integer_milliseconds():
    assert _format_ms(3_723_456) == "01:02:03.456"
    assert _parse_time("01:02:03.456") == 3_723_456
    with pytest.raises(ValueError):
        _parse_time("1.5")


def test_runtime_dashboard_renders_cpu_memory_gpu_disk_and_models(application):
    class Client:
        def get_runtime_metrics(self, refresh=False):
            return {
                "metrics": {
                    "cpu_percent": 42, "memory_percent": 50,
                    "disk_free_bytes": 1024**3, "disk_total_bytes": 2 * 1024**3,
                    "gpus": [{
                        "utilization_percent": 60,
                        "memory_used_mb": 1000, "memory_total_mb": 2000,
                    }],
                },
                "models": [{
                    "kind": "tts", "model_id": "demo", "device": "cuda:0",
                    "state": "loaded", "ref_count": 1,
                }],
            }

    dialog = RuntimeDashboardDialog(client=Client())
    dialog.refresh(True)
    assert dialog.cpu.value() == 42
    assert dialog.memory.value() == 50
    assert dialog.gpu.value() == 60
    assert dialog.vram.value() == 50
    assert dialog.models.rowCount() == 1
    dialog.timer.stop()


class _TTSClient:
    def __init__(self):
        self.saved = None

    def get_tts_providers(self):
        return {"providers": [{
            "name": "edge-tts", "available": True,
            "capabilities": {
                "speed": True, "pitch": True, "emotion": False,
                "preview_character_limit": 20,
            },
        }]}

    def get_tts_voices(self, provider, language=""):
        return {"voices": [{"name": "voice-a", "locale": language}]}

    def list_voice_presets(self):
        return {"presets": []}

    def create_voice_preset(self, payload):
        self.saved = payload
        return {"id": "preset", **payload}

    def cancel_tts_preview(self, _preview_id):
        return True


def test_tts_dialog_builds_only_capability_supported_options(application):
    client = _TTSClient()
    dialog = TTSPreviewDialog(client=client)
    dialog.speed.setValue(1.2)
    dialog.pitch.setValue(5)
    options = dialog._options()
    assert options == {"rate": "+20%", "pitch": "+5Hz"}
    assert dialog.emotion.isHidden()


def test_tts_dialog_saves_parameter_snapshot_without_secret(application):
    client = _TTSClient()
    dialog = TTSPreviewDialog(client=client)
    dialog.preset_name.setText("Narrator")
    dialog._save_preset()
    assert client.saved["name"] == "Narrator"
    assert "api_key" not in client.saved["parameters"]


class _SubtitleClient:
    def __init__(self):
        self.document = _document()
        self.drafts = []
        self.saved = []

    def get_subtitle_document(self, _job_id):
        return {"document": self.document.to_dict(), "draft": None, "issues": []}

    def save_subtitle_draft(self, _job_id, document, base_version):
        self.drafts.append((document, base_version))
        return {"status": "saved"}

    def save_subtitle_revision(self, _job_id, document, base_version, regenerate=False):
        result = SubtitleDocument.from_dict(document)
        result.version = base_version + 1
        self.saved.append((result.to_dict(), regenerate))
        return {"document": result.to_dict(), "regenerating": regenerate}

    def list_subtitle_revisions(self, _job_id):
        return {"revisions": []}


def test_timeline_dialog_loads_rows_and_tracks_dirty_edits(application):
    client = _SubtitleClient()
    dialog = SubtitleTimelineDialog("job", client=client)
    dialog._load()
    assert dialog.table.rowCount() == 2
    cue_id = dialog.document.cues[0].cue_id
    dialog._apply(UpdateCue(cue_id, {"source_text": "Edited"}), cue_id)
    assert dialog.document.cues[0].source_text == "Edited"
    assert dialog.dirty is True
    dialog.autosave.stop()


def test_timeline_autosave_and_formal_save_have_distinct_paths(application):
    client = _SubtitleClient()
    dialog = SubtitleTimelineDialog("job", client=client)
    dialog._load()
    cue_id = dialog.document.cues[0].cue_id
    dialog._apply(UpdateCue(cue_id, {"translated_text": "草稿"}), cue_id)
    dialog._autosave()
    assert client.drafts and not client.saved
    dialog._save(False)
    assert client.saved and client.saved[-1][1] is False
    assert dialog.dirty is False
    assert dialog.base_version == 2
    dialog.autosave.stop()


def test_timeline_save_and_regenerate_uses_explicit_mode(application):
    client = _SubtitleClient()
    dialog = SubtitleTimelineDialog("job", client=client)
    dialog._load()
    dialog._save(True)
    assert client.saved[-1][1] is True
    assert "重新生成" in dialog.save_state.text()
    dialog.autosave.stop()
