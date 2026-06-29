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
import base64
import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add localization-engine to path
_LOCALIZATION_ENGINE = str(Path(__file__).resolve().parent.parent / "localization-engine")
if _LOCALIZATION_ENGINE not in sys.path:
    sys.path.insert(0, _LOCALIZATION_ENGINE)

from tts.qwen3_tts import (
    Qwen3TTSProvider, LANG_MAP, DEFAULT_STABLE_SEED, _estimate_max_tokens,
    QWEN3_VOICE_LANGUAGE_MAP, voice_language, is_voice_compatible,
    compatible_voice_for,
)
from tts.sapi_tts import SapiTTSProvider
from tts.base import TTSAuthError
from tts.openai_compatible_tts import OpenAICompatibleTTSProvider
from tts.volcengine_tts import VolcengineDoubaoTTSProvider


# ---------------------------------------------------------------------------
# Voice-language compatibility tests
# ---------------------------------------------------------------------------

class TestVoiceLanguageCompatibility:
    def test_voice_language_map_covers_all_preset_voices(self):
        for voice in ("Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric",
                      "Ryan", "Aiden", "Ono_Anna", "Sohee"):
            assert voice in QWEN3_VOICE_LANGUAGE_MAP, f"{voice} missing from map"

    def test_voice_language_returns_correct_native(self):
        assert voice_language("Vivian") == "zh"
        assert voice_language("Uncle_Fu") == "zh"
        assert voice_language("Serena") == "en"
        assert voice_language("Ono_Anna") == "ja"
        assert voice_language("Sohee") == "ko"

    def test_voice_language_returns_none_for_unknown(self):
        assert voice_language("UnknownVoice") is None
        assert voice_language("") is None

    def test_is_voice_compatible_when_languages_match(self):
        assert is_voice_compatible("Vivian", "zh") is True
        assert is_voice_compatible("Vivian", "zh-CN") is True
        assert is_voice_compatible("Serena", "en") is True
        assert is_voice_compatible("Ono_Anna", "ja") is True
        assert is_voice_compatible("Sohee", "ko") is True

    def test_is_voice_compatible_detects_mismatch(self):
        # Korean voice for Chinese text — the exact bug that caused
        # off-language speech and swallowed sounds.
        assert is_voice_compatible("Sohee", "zh-CN") is False
        assert is_voice_compatible("Sohee", "zh") is False
        assert is_voice_compatible("Vivian", "ko") is False
        assert is_voice_compatible("Ono_Anna", "en") is False

    def test_is_voice_compatible_allows_custom_voices(self):
        # Custom voices not in the preset map are assumed compatible.
        assert is_voice_compatible("MyCustomVoice", "zh") is True
        assert is_voice_compatible("Custom", "en") is True

    def test_compatible_voice_for_returns_preferred_when_compatible(self):
        assert compatible_voice_for("zh", "Vivian") == "Vivian"
        assert compatible_voice_for("en", "Serena") == "Serena"

    def test_compatible_voice_for_corrects_mismatch(self):
        # Sohee (Korean) should not be kept for Chinese.
        result = compatible_voice_for("zh-CN", "Sohee")
        assert result != "Sohee"
        assert voice_language(result) == "zh"

    def test_compatible_voice_for_picks_default_for_target(self):
        result = compatible_voice_for("zh", "Sohee")
        assert result == "Vivian"  # first Chinese voice in the map

    def test_compatible_voice_for_japanese(self):
        result = compatible_voice_for("ja", "Sohee")
        assert result == "Ono_Anna"

    def test_compatible_voice_for_korean(self):
        result = compatible_voice_for("ko", "Vivian")
        assert result == "Sohee"


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
        assert provider.supports_concurrency is False
        sig = inspect.signature(provider.synthesize)
        param_names = list(sig.parameters.keys())
        assert "text" in param_names
        assert "language" in param_names
        assert "voice" in param_names
        assert "output_path" in param_names
        assert "options" in param_names

    def test_estimated_max_tokens_is_small_for_punctuation_only_text(self):
        assert _estimate_max_tokens("。") <= 48
        assert _estimate_max_tokens("确定") < _estimate_max_tokens("人工智能正在改变就业格局")

    @patch("tts.qwen3_tts._get_wav_duration", return_value=1.2)
    @patch("tts.qwen3_tts.urllib.request.urlopen")
    @patch("tts.qwen3_tts.Qwen3TTSProvider._ensure_model_loaded")
    @patch("tts.qwen3_tts.Qwen3TTSProvider._get_capabilities")
    @patch("tts.qwen3_tts.Qwen3TTSProvider._is_healthy")
    def test_synthesize_voice_design_sends_advanced_options(
        self, mock_healthy, mock_caps, mock_ensure_loaded, mock_urlopen, mock_duration, tmp_path
    ):
        mock_healthy.return_value = True
        mock_caps.return_value = {"voice_design": True}
        captured = {}

        class FakeResponse:
            headers = {"X-Duration": "1.2"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"wav-bytes"

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse()

        mock_urlopen.side_effect = fake_urlopen
        provider = Qwen3TTSProvider(base_url="http://tts.test")
        result = provider.synthesize(
            "hello",
            "en",
            "Vivian",
            tmp_path / "out.wav",
            {
                "qwen_mode": "voice_design",
                "instruct": "warm narrator",
                "seed": 7,
                "temperature": 0.6,
                "top_p": 0.9,
                "max_new_tokens": 256,
            },
        )

        assert captured["url"].endswith("/synthesize/voice-design")
        assert captured["payload"]["instruct"] == "warm narrator"
        assert captured["payload"]["seed"] == 7
        assert captured["payload"]["temperature"] == 0.6
        assert captured["payload"]["top_p"] == 0.9
        assert captured["payload"]["max_new_tokens"] == 256
        assert result.mode == "voice_design"

    @patch("tts.qwen3_tts._get_wav_duration", return_value=1.2)
    @patch("tts.qwen3_tts.urllib.request.urlopen")
    @patch("tts.qwen3_tts.Qwen3TTSProvider._ensure_model_loaded")
    @patch("tts.qwen3_tts.Qwen3TTSProvider._get_capabilities")
    @patch("tts.qwen3_tts.Qwen3TTSProvider._is_healthy")
    def test_synthesize_custom_voice_uses_stable_seed_by_default(
        self, mock_healthy, mock_caps, mock_ensure_loaded, mock_urlopen, mock_duration, tmp_path
    ):
        mock_healthy.return_value = True
        mock_caps.return_value = {"custom_voice": True}
        captured = {}

        class FakeResponse:
            headers = {"X-Duration": "1.2"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"wav-bytes"

        def fake_urlopen(req, timeout=0):
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse()

        mock_urlopen.side_effect = fake_urlopen
        provider = Qwen3TTSProvider(base_url="http://tts.test")
        provider.synthesize(
            "hello",
            "en",
            "Vivian",
            tmp_path / "out.wav",
            {"qwen_mode": "custom_voice"},
        )

        assert captured["payload"]["seed"] == DEFAULT_STABLE_SEED

    @patch("tts.qwen3_tts._get_wav_duration", return_value=1.2)
    @patch("tts.qwen3_tts.urllib.request.urlopen")
    @patch("tts.qwen3_tts.Qwen3TTSProvider._ensure_model_loaded")
    @patch("tts.qwen3_tts.Qwen3TTSProvider._get_capabilities")
    @patch("tts.qwen3_tts.Qwen3TTSProvider._is_healthy")
    def test_synthesize_custom_voice_allows_random_seed_opt_out(
        self, mock_healthy, mock_caps, mock_ensure_loaded, mock_urlopen, mock_duration, tmp_path
    ):
        mock_healthy.return_value = True
        mock_caps.return_value = {"custom_voice": True}
        captured = {}

        class FakeResponse:
            headers = {"X-Duration": "1.2"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"wav-bytes"

        def fake_urlopen(req, timeout=0):
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse()

        mock_urlopen.side_effect = fake_urlopen
        provider = Qwen3TTSProvider(base_url="http://tts.test")
        provider.synthesize(
            "hello",
            "en",
            "Vivian",
            tmp_path / "out.wav",
            {"qwen_mode": "custom_voice", "seed": -1},
        )

        assert "seed" not in captured["payload"]

    @patch("tts.qwen3_tts.Qwen3TTSProvider._request")
    def test_ensure_model_loaded_uses_low_vram_defaults(self, mock_request):
        loaded_resp = MagicMock()
        loaded_resp.read.return_value = b'{"model_id": null}'
        load_resp = MagicMock()
        load_resp.read.return_value = b'{"status": "loaded"}'
        mock_request.side_effect = [loaded_resp, load_resp]

        provider = Qwen3TTSProvider()
        provider._ensure_model_loaded({"qwen_mode": "voice_clone"})

        assert mock_request.call_args_list[1].args[:2] == ("POST", "/models/load")
        payload = json.loads(mock_request.call_args_list[1].kwargs["body"].decode("utf-8"))
        assert payload["model_id"] == "Qwen/Qwen3-TTS-12Hz-0.6B-Base"

    @patch("tts.qwen3_tts.Qwen3TTSProvider._request")
    def test_ensure_model_loaded_skips_when_already_loaded(self, mock_request):
        loaded_resp = MagicMock()
        loaded_resp.read.return_value = b'{"model_id": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"}'
        mock_request.return_value = loaded_resp

        provider = Qwen3TTSProvider()
        provider._ensure_model_loaded({"qwen_mode": "auto"})

        assert mock_request.call_count == 1

    @patch("tts.qwen3_tts.Qwen3TTSProvider._request")
    def test_ensure_model_loaded_switches_mismatched_model(self, mock_request):
        loaded_resp = MagicMock()
        loaded_resp.read.return_value = b'{"model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"}'
        unload_resp = MagicMock()
        unload_resp.read.return_value = b'{"status": "unloaded"}'
        load_resp = MagicMock()
        load_resp.read.return_value = b'{"status": "loaded"}'
        mock_request.side_effect = [loaded_resp, unload_resp, load_resp]

        provider = Qwen3TTSProvider()
        provider._ensure_model_loaded({"qwen_mode": "custom_voice"})

        assert mock_request.call_args_list[1].args[:2] == ("POST", "/models/unload")
        assert mock_request.call_args_list[2].args[:2] == ("POST", "/models/load")
        payload = json.loads(mock_request.call_args_list[2].kwargs["body"].decode("utf-8"))
        assert payload["model_id"] == "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"


class TestSapiTTSProvider:
    @patch("tts.sapi_tts.os.name", "nt")
    @patch("tts.sapi_tts._run_powershell")
    def test_list_voices_parses_windows_sapi_voices(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"name":"Microsoft Huihui Desktop","locale":"zh-CN","gender":"Female"}]',
            stderr="",
        )

        provider = SapiTTSProvider()
        voices = provider.list_voices("zh-CN")

        assert len(voices) == 1
        assert voices[0]["name"] == "Microsoft Huihui Desktop"

    @patch("tts.sapi_tts.os.name", "nt")
    @patch("tts.sapi_tts._get_wav_duration", return_value=1.25)
    @patch("tts.sapi_tts._synthesize_with_com")
    @patch("tts.sapi_tts._run_powershell")
    def test_synthesize_returns_duration(self, mock_run, mock_com, mock_duration, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        provider = SapiTTSProvider()

        result = provider.synthesize(
            "hello",
            "en",
            "default",
            tmp_path / "out.wav",
            {"timeout": 10},
        )

        assert result.duration_seconds == 1.25
        assert result.output_path.name == "out.wav"


class TestVolcengineDoubaoTTSProvider:
    @patch("tts.volcengine_tts._get_audio_duration", return_value=1.25)
    @patch("tts.volcengine_tts.urllib.request.urlopen")
    def test_synthesize_sends_v3_headers_and_decodes_stream(
        self, mock_urlopen, mock_duration, tmp_path
    ):
        captured = {}
        audio_bytes = b"mp3-audio-bytes"
        body = (
            json.dumps({"code": 0, "data": base64.b64encode(audio_bytes).decode("ascii")})
            + "\n"
            + json.dumps({"code": 20000000, "message": "ok"})
        ).encode("utf-8")

        class FakeResponse:
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return body

        def fake_urlopen(req, timeout=0):
            captured["timeout"] = timeout
            captured["url"] = req.full_url
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            headers = {}
            headers.update(getattr(req, "headers", {}))
            headers.update(getattr(req, "unredirected_hdrs", {}))
            captured["headers"] = {str(k).lower(): v for k, v in headers.items()}
            return FakeResponse()

        mock_urlopen.side_effect = fake_urlopen
        provider = VolcengineDoubaoTTSProvider()
        out_path = tmp_path / "out.mp3"

        result = provider.synthesize(
            "hello",
            "zh-CN",
            "zh_female_vv_uranus_bigtts",
            out_path,
            {
                "volcengine_endpoint": "https://example.test/tts",
                "volcengine_api_key": "volc-key",
                "volcengine_resource_id": "seed-tts-2.0",
                "volcengine_model": "seed-tts-2.0-expressive",
                "volcengine_format": "mp3",
                "volcengine_sample_rate": 24000,
                "volcengine_speech_rate": 5,
                "volcengine_loudness_rate": -3,
                "timeout": 15,
            },
        )

        assert captured["url"] == "https://example.test/tts"
        assert captured["timeout"] == 15
        assert captured["headers"]["x-api-key"] == "volc-key"
        assert captured["headers"]["x-api-resource-id"] == "seed-tts-2.0"
        assert captured["payload"]["req_params"]["text"] == "hello"
        assert captured["payload"]["req_params"]["speaker"] == "zh_female_vv_uranus_bigtts"
        assert captured["payload"]["req_params"]["audio_params"]["format"] == "mp3"
        assert captured["payload"]["req_params"]["audio_params"]["sample_rate"] == 24000
        assert captured["payload"]["req_params"]["speech_rate"] == 5
        assert captured["payload"]["req_params"]["loudness_rate"] == -3
        assert out_path.read_bytes() == audio_bytes
        assert result.duration_seconds == 1.25

    def test_synthesize_requires_credentials(self, tmp_path):
        provider = VolcengineDoubaoTTSProvider()
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(TTSAuthError):
                provider.synthesize(
                    "hello",
                    "zh-CN",
                    "zh_female_vv_uranus_bigtts",
                    tmp_path / "out.mp3",
                    {},
                )

    @patch("tts.volcengine_tts.urllib.request.urlopen")
    def test_synthesize_maps_http_401_to_auth_error(self, mock_urlopen, tmp_path):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.test/tts",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"message":"Invalid X-Api-Key"}'),
        )
        provider = VolcengineDoubaoTTSProvider()

        with pytest.raises(TTSAuthError, match="HTTP 401"):
            provider.synthesize(
                "hello",
                "zh-CN",
                "zh_female_vv_uranus_bigtts",
                tmp_path / "out.mp3",
                {
                    "volcengine_endpoint": "https://example.test/tts",
                    "volcengine_api_key": "bad-key",
                },
            )


class TestOpenAICompatibleTTSProvider:
    @patch("tts.openai_compatible_tts._get_audio_duration", return_value=1.25)
    @patch("tts.openai_compatible_tts.urllib.request.urlopen")
    def test_synthesize_posts_audio_speech_request(self, mock_urlopen, mock_duration, tmp_path):
        captured = {}
        audio_bytes = b"mp3-audio-bytes"

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return audio_bytes

        def fake_urlopen(req, timeout=0):
            captured["timeout"] = timeout
            captured["url"] = req.full_url
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            headers = {}
            headers.update(getattr(req, "headers", {}))
            headers.update(getattr(req, "unredirected_hdrs", {}))
            captured["headers"] = {str(k).lower(): v for k, v in headers.items()}
            return FakeResponse()

        mock_urlopen.side_effect = fake_urlopen
        provider = OpenAICompatibleTTSProvider()
        out_path = tmp_path / "out.mp3"

        result = provider.synthesize(
            "hello",
            "zh-CN",
            "alloy",
            out_path,
            {
                "openai_tts_base_url": "https://example.test/v1",
                "openai_tts_api_key": "tts-key",
                "openai_tts_model": "tts-1",
                "openai_tts_format": "mp3",
                "openai_tts_sample_rate": 24000,
                "openai_tts_speed": 1.0,
                "timeout": 15,
            },
        )

        assert captured["url"] == "https://example.test/v1/audio/speech"
        assert captured["timeout"] == 15
        assert captured["headers"]["authorization"] == "Bearer tts-key"
        assert captured["payload"]["model"] == "tts-1"
        assert captured["payload"]["input"] == "hello"
        assert captured["payload"]["voice"] == "alloy"
        assert captured["payload"]["response_format"] == "mp3"
        assert captured["payload"]["sample_rate"] == 24000
        assert out_path.read_bytes() == audio_bytes
        assert result.duration_seconds == 1.25

    @patch("tts.openai_compatible_tts._get_audio_duration", return_value=1.25)
    @patch("tts.openai_compatible_tts.urllib.request.urlopen")
    def test_synthesize_mimo_posts_chat_completions_audio_request(self, mock_urlopen, mock_duration, tmp_path):
        captured = {}
        audio_bytes = b"wav-audio-bytes"
        body = json.dumps({
            "choices": [{
                "message": {
                    "audio": {
                        "data": base64.b64encode(audio_bytes).decode("ascii")
                    }
                }
            }]
        }).encode("utf-8")

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return body

        def fake_urlopen(req, timeout=0):
            captured["timeout"] = timeout
            captured["url"] = req.full_url
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            headers = {}
            headers.update(getattr(req, "headers", {}))
            headers.update(getattr(req, "unredirected_hdrs", {}))
            captured["headers"] = {str(k).lower(): v for k, v in headers.items()}
            return FakeResponse()

        mock_urlopen.side_effect = fake_urlopen
        provider = OpenAICompatibleTTSProvider()
        out_path = tmp_path / "out.wav"

        result = provider.synthesize(
            "你好",
            "zh-CN",
            "mimo_default",
            out_path,
            {
                "openai_tts_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
                "openai_tts_api_key": "tp-key",
                "openai_tts_model": "mimo-v2.5-tts",
                "openai_tts_format": "wav",
                "timeout": 15,
            },
        )

        assert captured["url"] == "https://token-plan-cn.xiaomimimo.com/v1/chat/completions"
        assert captured["headers"]["api-key"] == "tp-key"
        assert captured["payload"]["model"] == "mimo-v2.5-tts"
        assert captured["payload"]["messages"][1]["content"] == "你好"
        assert captured["payload"]["audio"]["format"] == "wav"
        assert captured["payload"]["audio"]["voice"] == "mimo_default"
        assert out_path.read_bytes() == audio_bytes
        assert result.duration_seconds == 1.25

    def test_synthesize_requires_credentials(self, tmp_path):
        provider = OpenAICompatibleTTSProvider()
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(TTSAuthError):
                provider.synthesize(
                    "hello",
                    "zh-CN",
                    "alloy",
                    tmp_path / "out.mp3",
                    {},
                )


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
            capture_output=True, text=True, timeout=120,
            env=env,
        )
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr[:500])
        assert result.returncode == 0, f"Engine tests failed:\n{result.stdout}\n{result.stderr}"
    finally:
        if test_file.exists():
            test_file.unlink()
