"""Tests for Qwen3-TTS provider adapter and engine modules.

NOTE: qwen3-tts-engine has its own 'engine' package that conflicts
with localization-engine/engine/. These tests must be run in a separate
process to avoid module namespace collisions.

Run with:
    pytest tests/test_qwen3_tts.py
    pytest tests/ (skips engine tests, only runs provider tests)

To run engine module tests:
    python tests/test_qwen3_tts.py --engine
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add localization-engine to path
_LOCALIZATION_ENGINE = str(Path(__file__).resolve().parent.parent / "localization-engine")
if _LOCALIZATION_ENGINE not in sys.path:
    sys.path.insert(0, _LOCALIZATION_ENGINE)

from tts.qwen3_tts import Qwen3TTSProvider, LANG_MAP


# ---------------------------------------------------------------------------
# Provider adapter tests
# ---------------------------------------------------------------------------

class TestQwen3TTSProvider:
    def test_lang_map_contains_common_languages(self):
        assert LANG_MAP["zh"] == "chinese"
        assert LANG_MAP["en"] == "english"
        assert LANG_MAP["ja"] == "japanese"
        assert LANG_MAP["ko"] == "korean"
        assert LANG_MAP["de"] == "german"
        assert LANG_MAP["fr"] == "french"

    def test_list_voices_returns_empty_when_offline(self):
        provider = Qwen3TTSProvider(base_url="http://127.0.0.1:1")
        voices = provider.list_voices()
        assert voices == []

    @patch("tts.qwen3_tts.Qwen3TTSProvider._request")
    def test_list_voices_parses_response(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"voices": [{"name": "Vivian"}, {"name": "Serena"}], "languages": ["zh", "en"]}'
        mock_request.return_value = mock_resp

        provider = Qwen3TTSProvider()
        provider._voice_cache = None
        voices = provider.list_voices()
        assert len(voices) == 2
        assert voices[0]["name"] == "Vivian"

    @patch("tts.qwen3_tts.Qwen3TTSProvider._request")
    def test_list_voices_filters_by_language(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"voices": [{"name": "Vivian"}, {"name": "Serena"}], "languages": ["zh", "en"]}'
        mock_request.return_value = mock_resp

        provider = Qwen3TTSProvider()
        provider._voice_cache = None
        voices = provider.list_voices(language="zh")
        assert len(voices) == 2
        assert voices[0]["name"] == "Vivian"

    @patch("tts.qwen3_tts.Qwen3TTSProvider._is_healthy")
    def test_synthesize_raises_when_offline(self, mock_healthy):
        mock_healthy.return_value = False
        provider = Qwen3TTSProvider(base_url="http://127.0.0.1:1")
        from tts.base import TTSUnavailableError
        with pytest.raises(TTSUnavailableError):
            provider.synthesize("hello", "en", "Vivian", Path("out.wav"), {})

    def test_tts_provider_conforms_to_protocol(self):
        import inspect
        provider = Qwen3TTSProvider()
        assert hasattr(provider, "synthesize")
        assert hasattr(provider, "list_voices")
        sig = inspect.signature(provider.synthesize)
        param_names = list(sig.parameters.keys())
        assert "text" in param_names
        assert "language" in param_names
        assert "voice" in param_names
        assert "output_path" in param_names
        assert "options" in param_names


# ---------------------------------------------------------------------------
# Engine modules run in a subprocess to avoid namespace conflicts
# ---------------------------------------------------------------------------

_ENGINE_TESTS = """
from __future__ import annotations

import os
import sys
from pathlib import Path

_QWEN3 = os.environ.get("QWEN3_ENGINE_DIR")
if not _QWEN3:
    _QWEN3 = str(Path(__file__).resolve().parent)
if _QWEN3 not in sys.path:
    sys.path.insert(0, _QWEN3)

for key in list(sys.modules.keys()):
    if key.startswith("engine") or key == "engine":
        del sys.modules[key]


def test_device_detect():
    from engine.device import detect_device
    info = detect_device()
    assert "device" in info
    assert "dtype" in info
    assert "flash_attention" in info


def test_schemas():
    from engine.schemas import Capabilities, SynthesizeCustomVoiceRequest
    caps = Capabilities()
    assert caps.custom_voice is False
    assert caps.voice_clone is False
    assert caps.voice_design is False
    req = SynthesizeCustomVoiceRequest(text="hello", speaker="Vivian")
    assert req.text == "hello"
    assert req.speaker == "Vivian"


def test_cache():
    from engine.cache import TTSCache
    import tempfile
    cache = TTSCache(Path(tempfile.mkdtemp()))
    assert cache is not None


def test_model_manager_singleton():
    from engine.model_manager import ModelManager
    m1 = ModelManager()
    m2 = ModelManager()
    assert m1 is m2


def test_model_manager_lists_models():
    from engine.model_manager import ModelManager
    models = ModelManager().list_models()
    assert len(models) == 5
    assert any("0.6B-CustomVoice" in m["short"] for m in models)
    assert any("1.7B-VoiceDesign" in m["short"] for m in models)


def test_base_models_do_not_advertise_custom_voice():
    from engine.model_manager import MODEL_TIERS
    base_caps = MODEL_TIERS["Qwen/Qwen3-TTS-12Hz-1.7B-Base"]["capabilities"]
    assert "voice_clone" in base_caps
    assert "custom_voice" not in base_caps


def test_model_manager_speakers():
    from engine.model_manager import ModelManager
    speakers = ModelManager().list_speakers()
    assert "Vivian" in speakers
    assert "Serena" in speakers
    assert "Uncle_Fu" in speakers


def test_voice_clone_module():
    from engine import voice_clone
    prompts = voice_clone.list_prompts()
    assert isinstance(prompts, list)
    assert len(prompts) == 0
    deleted = voice_clone.delete_prompt("nonexistent")
    assert deleted is False


def test_voice_design_module():
    from engine import voice_design
    designs = voice_design.list_designs()
    assert isinstance(designs, list)


if __name__ == "__main__":
    results = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                results.append(f"PASS {name}")
            except Exception as e:
                results.append(f"FAIL {name}: {e}")

    print("--- engine test results ---")
    for r in results:
        print(r)
    num_pass = sum(1 for r in results if r.startswith('PASS'))
    print(f"--- {num_pass}/{len(results)} passed ---")
    if any(r.startswith("FAIL") for r in results):
        exit(1)
"""


def test_qwen3_engine_modules_in_subprocess():
    """Run engine module tests in a subprocess because the 'engine'
    package in qwen3-tts-engine conflicts with localization-engine/engine/."""
    qwen3_dir = str(Path(__file__).resolve().parent.parent / "qwen3-tts-engine")
    test_file = Path(qwen3_dir) / "_run_tests.py"
    test_file.write_text(_ENGINE_TESTS, encoding="utf-8")
    env = os.environ.copy()
    env["QWEN3_ENGINE_DIR"] = qwen3_dir
    try:
        result = subprocess.run(
            [sys.executable, str(test_file)],
            capture_output=True, text=True, timeout=30,
            env=env,
        )
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr[:500])
        assert result.returncode == 0, f"Engine tests failed:\n{result.stdout}\n{result.stderr}"
    finally:
        if test_file.exists():
            test_file.unlink()
