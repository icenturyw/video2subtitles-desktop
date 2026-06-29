from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client_settings import (
    DEFAULT_SETTINGS,
    _as_bool_text,
    _clean_settings,
    apply_settings_to_env,
    get_effective_settings,
    get_runtime_settings,
    load_settings,
    save_settings,
)


class TestAsBoolText(unittest.TestCase):
    def test_bool_values(self):
        self.assertEqual(_as_bool_text(True), "true")
        self.assertEqual(_as_bool_text(False), "false")

    def test_string_true_variants(self):
        for v in ("true", "True", "TRUE", "1", "yes", "Yes", "on", "On", "y", "Y"):
            self.assertEqual(_as_bool_text(v), "true")

    def test_string_false_variants(self):
        for v in ("false", "False", "FALSE", "0", "no", "No", "off", "Off", "n", "N"):
            self.assertEqual(_as_bool_text(v), "false")

    def test_none_falls_back_to_default(self):
        self.assertEqual(_as_bool_text(None, "true"), "true")
        self.assertEqual(_as_bool_text(None, "false"), "false")

    def test_unknown_string_returns_default(self):
        self.assertEqual(_as_bool_text("maybe"), "true")
        self.assertEqual(_as_bool_text("maybe", "false"), "false")


class TestCleanSettings(unittest.TestCase):
    def test_unknown_model_size_falls_back(self):
        cleaned = _clean_settings({"model_size": "gpt-4"})
        self.assertEqual(cleaned["model_size"], DEFAULT_SETTINGS["model_size"])

    def test_valid_model_size_preserved(self):
        for size in ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"):
            cleaned = _clean_settings({"model_size": size})
            self.assertEqual(cleaned["model_size"], size)

    def test_empty_whisper_model_dir_falls_back(self):
        cleaned = _clean_settings({"whisper_model_dir": ""})
        self.assertEqual(cleaned["whisper_model_dir"], DEFAULT_SETTINGS["whisper_model_dir"])

    def test_unknown_device_falls_back_to_auto(self):
        cleaned = _clean_settings({"device": "rocm"})
        self.assertEqual(cleaned["device"], "auto")

    def test_valid_device_preserved(self):
        for d in ("auto", "cuda", "cpu"):
            cleaned = _clean_settings({"device": d})
            self.assertEqual(cleaned["device"], d)

    def test_unknown_compute_type_falls_back_to_auto(self):
        cleaned = _clean_settings({"compute_type": "bfloat16"})
        self.assertEqual(cleaned["compute_type"], "auto")

    def test_valid_compute_type_preserved(self):
        for c in ("auto", "float16", "int8_float16", "int8", "float32"):
            cleaned = _clean_settings({"compute_type": c})
            self.assertEqual(cleaned["compute_type"], c)

    def test_unknown_download_mode_falls_back(self):
        cleaned = _clean_settings({"download_mode": "torrent"})
        self.assertEqual(cleaned["download_mode"], DEFAULT_SETTINGS["download_mode"])

    def test_unknown_download_quality_falls_back(self):
        cleaned = _clean_settings({"download_quality": "4k"})
        self.assertEqual(cleaned["download_quality"], DEFAULT_SETTINGS["download_quality"])

    def test_keep_downloaded_video_force_true_in_video_mode(self):
        cleaned = _clean_settings({"download_mode": "video", "keep_downloaded_video": "false"})
        self.assertEqual(cleaned["keep_downloaded_video"], "true")

    def test_none_values_ignored(self):
        cleaned = _clean_settings({"model_size": None, "device": None})
        self.assertEqual(cleaned["model_size"], DEFAULT_SETTINGS["model_size"])
        self.assertEqual(cleaned["device"], DEFAULT_SETTINGS["device"])

    def test_all_defaults_present_after_clean(self):
        cleaned = _clean_settings({})
        for key in DEFAULT_SETTINGS:
            self.assertIn(key, cleaned)

    def test_str_value_stripped(self):
        cleaned = _clean_settings({"model_size": "  base  "})
        self.assertEqual(cleaned["model_size"], "base")

    def test_not_a_dict_returns_defaults(self):
        cleaned = _clean_settings(None)
        self.assertEqual(cleaned, DEFAULT_SETTINGS)

    def test_non_dict_type_returns_defaults(self):
        cleaned = _clean_settings("invalid")
        self.assertEqual(cleaned, DEFAULT_SETTINGS)


class TestLoadSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings_path = self.tmp / "settings.json"
        self.patcher = patch("client_settings.SETTINGS_PATH", self.settings_path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_no_file_returns_defaults(self):
        result = load_settings()
        self.assertEqual(result, DEFAULT_SETTINGS)

    def test_loads_saved_settings(self):
        saved = {"model_size": "large-v3", "device": "cuda"}
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(saved), encoding="utf-8")
        result = load_settings()
        self.assertEqual(result["model_size"], "large-v3")
        self.assertEqual(result["device"], "cuda")

    def test_corrupt_json_returns_defaults(self):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text("{bad json}", encoding="utf-8")
        result = load_settings()
        self.assertEqual(result, DEFAULT_SETTINGS)

    def test_saved_settings_are_cleaned(self):
        saved = {"model_size": "gpt-4"}
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(saved), encoding="utf-8")
        result = load_settings()
        self.assertEqual(result["model_size"], DEFAULT_SETTINGS["model_size"])


class TestEffectiveSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings_path = self.tmp / "settings.json"
        self.patcher = patch("client_settings.SETTINGS_PATH", self.settings_path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        for key in ("V2S_TRANSLATION_BASE_URL", "V2S_TARGET_LANGUAGE", "MODEL_SIZE"):
            os.environ.pop(key, None)

    def test_no_env_overlay_returns_saved(self):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps({"model_size": "small"}), encoding="utf-8")
        result = get_effective_settings()
        self.assertEqual(result["model_size"], "small")

    def test_env_var_overrides_saved(self):
        os.environ["MODEL_SIZE"] = "large-v3"
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps({"model_size": "small"}), encoding="utf-8")
        result = get_effective_settings()
        self.assertEqual(result["model_size"], "large-v3")

    def test_env_var_also_cleaned(self):
        os.environ["MODEL_SIZE"] = "invalid-model"
        result = get_effective_settings()
        self.assertEqual(result["model_size"], DEFAULT_SETTINGS["model_size"])

    def test_empty_env_var_does_not_override(self):
        os.environ["MODEL_SIZE"] = ""
        result = get_effective_settings()
        self.assertEqual(result["model_size"], DEFAULT_SETTINGS["model_size"])


class TestRuntimeSettings(unittest.TestCase):
    def test_resolves_auto_device_to_cpu_when_no_gpu(self):
        with patch("client_settings.resolve_device_and_compute",
                   return_value=("cpu", "int8")):
            result = get_runtime_settings({"device": "auto", "compute_type": "auto"})
            self.assertEqual(result["resolved_device"], "cpu")
            self.assertEqual(result["resolved_compute_type"], "int8")

    def test_explicit_device_passed_through(self):
        with patch("client_settings.resolve_device_and_compute",
                   return_value=("cuda", "float16")):
            result = get_runtime_settings({"device": "cuda", "compute_type": "float16"})
            self.assertEqual(result["resolved_device"], "cuda")
            self.assertEqual(result["resolved_compute_type"], "float16")


class TestSaveSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings_dir = self.tmp / ".cache"
        self.settings_path = self.settings_dir / "settings.json"
        self.patchers = [
            patch("client_settings.SETTINGS_PATH", self.settings_path),
            patch("client_settings.SETTINGS_DIR", self.settings_dir),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def test_save_and_load_roundtrip(self):
        save_settings({"model_size": "large-v3", "device": "cuda"})
        loaded = load_settings()
        self.assertEqual(loaded["model_size"], "large-v3")
        self.assertEqual(loaded["device"], "cuda")

    def test_save_merges_with_existing(self):
        save_settings({"model_size": "medium"})
        save_settings({"device": "cpu"})
        loaded = load_settings()
        self.assertEqual(loaded["model_size"], "medium")
        self.assertEqual(loaded["device"], "cpu")

    def test_save_cleans_before_write(self):
        save_settings({"model_size": "gpt-4"})
        loaded = load_settings()
        self.assertEqual(loaded["model_size"], DEFAULT_SETTINGS["model_size"])


class TestApplySettingsToEnv(unittest.TestCase):
    def setUp(self):
        self.env_keys = [
            "MODEL_SIZE", "V2S_DEVICE_SETTING", "V2S_COMPUTE_TYPE_SETTING",
            "DEVICE", "COMPUTE_TYPE",
        ]
        for k in self.env_keys:
            os.environ.pop(k, None)

    def tearDown(self):
        for k in self.env_keys:
            os.environ.pop(k, None)

    def test_sets_env_vars(self):
        apply_settings_to_env({"model_size": "small"})
        self.assertEqual(os.environ.get("MODEL_SIZE"), "small")

    def test_sets_device_and_compute_type_env(self):
        apply_settings_to_env({"device": "cuda", "compute_type": "float16"})
        self.assertEqual(os.environ.get("V2S_DEVICE_SETTING"), "cuda")
        self.assertEqual(os.environ.get("V2S_COMPUTE_TYPE_SETTING"), "float16")

    def test_existing_env_preserved_when_overwrite_false(self):
        os.environ["MODEL_SIZE"] = "large-v3"
        apply_settings_to_env({"model_size": "small"}, overwrite=False)
        self.assertEqual(os.environ.get("MODEL_SIZE"), "large-v3")

    def test_overwrite_true_replaces_existing(self):
        os.environ["MODEL_SIZE"] = "large-v3"
        apply_settings_to_env({"model_size": "small"}, overwrite=True)
        self.assertEqual(os.environ.get("MODEL_SIZE"), "small")
