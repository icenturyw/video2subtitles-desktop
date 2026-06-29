import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from provider_presets import (
    BUILTIN_QWEN3_TTS_PRESET_ID,
    ProviderPreset,
    apply_translation_preset_to_settings,
    apply_tts_preset_to_settings,
    builtin_provider_presets,
    ensure_builtin_provider_presets,
    duplicate_provider_preset,
    export_presets,
    import_presets,
    load_provider_presets,
    migrate_legacy_settings_to_presets,
    save_provider_presets,
    set_default_provider_preset,
)


class TestProviderPresets(unittest.TestCase):

    def test_builtin_qwen3_tts_preset_available(self):
        presets = builtin_provider_presets(now="2026-06-24T00:00:00")
        self.assertEqual(len(presets), 1)
        preset = presets[0]
        self.assertEqual(preset.id, BUILTIN_QWEN3_TTS_PRESET_ID)
        self.assertEqual(preset.type, "tts")
        self.assertEqual(preset.provider, "qwen3-tts")
        self.assertTrue(preset.isDefault)
        self.assertEqual(preset.config["voice"], "Vivian")
        self.assertEqual(preset.config["qwenMode"], "auto")
        self.assertEqual(preset.config["consistencyMode"], "stable")

    def test_load_provider_presets_includes_builtin_qwen3_tts(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "presets.json"
            loaded = load_provider_presets(path, migrate=False, include_builtins=True)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].provider, "qwen3-tts")
            self.assertTrue(loaded[0].isDefault)

    def test_builtin_qwen3_tts_does_not_override_existing_tts_default(self):
        presets, changed = ensure_builtin_provider_presets([
            ProviderPreset(
                id="edge",
                type="tts",
                name="Edge 默认",
                provider="edge-tts",
                enabled=True,
                isDefault=True,
                config={"voice": "zh-CN-XiaoxiaoNeural"},
            )
        ])
        self.assertTrue(changed)
        self.assertEqual(len(presets), 2)
        self.assertTrue(next(p for p in presets if p.provider == "edge-tts").isDefault)
        self.assertFalse(next(p for p in presets if p.provider == "qwen3-tts").isDefault)

    def test_save_load_and_single_default(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "presets.json"
            presets = [
                ProviderPreset(
                    id="a",
                    type="translation",
                    name="A",
                    provider="openai_compatible",
                    isDefault=True,
                    config={"apiKey": "k1"},
                ),
                ProviderPreset(
                    id="b",
                    type="translation",
                    name="B",
                    provider="openai_compatible",
                    isDefault=True,
                    config={"apiKey": "k2"},
                ),
            ]
            save_provider_presets(presets, path)
            loaded = load_provider_presets(path, migrate=False)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(sum(1 for p in loaded if p.isDefault), 1)

    def test_migrate_legacy_settings(self):
        settings = {
            "translation_provider": "openai_compatible",
            "translation_base_url": "https://api.test/v1",
            "translation_model": "gpt-test",
            "translation_api_key": "secret",
            "target_language_dialog": "zh-CN (简体中文)",
            "tts_provider": "volcengine-doubao",
            "tts_voice": "voice-a",
            "tts_volcengine_api_key": "tts-secret",
            "tts_volcengine_model": "seed",
        }
        presets = migrate_legacy_settings_to_presets(settings)
        self.assertEqual({p.type for p in presets}, {"translation", "tts"})
        trans = next(p for p in presets if p.type == "translation")
        self.assertEqual(trans.config["baseUrl"], "https://api.test/v1")
        tts = next(p for p in presets if p.type == "tts")
        self.assertEqual(tts.config["voice"], "voice-a")

    def test_apply_presets_to_settings(self):
        settings = {}
        trans = ProviderPreset(
            id="t1",
            type="translation",
            name="Trans",
            provider="openai_compatible",
            config={
                "apiKey": "secret",
                "baseUrl": "https://api.test",
                "model": "m",
                "targetLanguage": "ja",
                "concurrency": 8,
            },
        )
        tts = ProviderPreset(
            id="v1",
            type="tts",
            name="Voice",
            provider="volcengine-doubao",
            config={
                "apiKey": "tts-secret",
                "baseUrl": "https://tts.test",
                "model": "seed",
                "voice": "voice-b",
                "sampleRate": 24000,
                "minSentenceGapMs": 150,
            },
        )
        applied = apply_translation_preset_to_settings(settings, trans)
        applied = apply_tts_preset_to_settings(applied, tts)
        self.assertEqual(applied["translation_api_key"], "secret")
        self.assertEqual(applied["default_target_language"], "ja")
        self.assertEqual(applied["tts_provider"], "volcengine-doubao")
        self.assertEqual(applied["tts_segment_gap"], "0.15")


    def test_apply_tts_preset_can_preserve_current_voice(self):
        settings = {
            "tts_provider": "qwen3-tts",
            "tts_voice": "Serena",
        }
        preset = ProviderPreset(
            id="qwen",
            type="tts",
            name="本地 Qwen3-TTS",
            provider="qwen3-tts",
            config={
                "voice": "Vivian",
                "qwenMode": "auto",
                "concurrency": 1,
            },
        )

        applied = apply_tts_preset_to_settings(
            settings,
            preset,
            preserve_current_voice=True,
        )

        self.assertEqual(applied["tts_provider"], "qwen3-tts")
        self.assertEqual(applied["tts_voice"], "Serena")
        self.assertEqual(applied["tts_qwen_mode"], "auto")

    def test_apply_tts_preset_does_not_preserve_voice_across_providers(self):
        settings = {
            "tts_provider": "edge-tts",
            "tts_voice": "zh-CN-XiaoxiaoNeural",
        }
        preset = ProviderPreset(
            id="qwen",
            type="tts",
            name="本地 Qwen3-TTS",
            provider="qwen3-tts",
            config={"voice": "Vivian"},
        )

        applied = apply_tts_preset_to_settings(
            settings,
            preset,
            preserve_current_voice=True,
        )

        self.assertEqual(applied["tts_provider"], "qwen3-tts")
        self.assertEqual(applied["tts_voice"], "Vivian")

    def test_duplicate_set_default_and_export_redacts_keys(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "presets.json"
            export_path = Path(td) / "export.json"
            save_provider_presets([
                ProviderPreset(
                    id="a",
                    type="tts",
                    name="Voice",
                    provider="volcengine-doubao",
                    isDefault=True,
                    config={"apiKey": "secret", "volcengineAccessKey": "access"},
                )
            ], path)
            clone = duplicate_provider_preset("a", path)
            self.assertIsNotNone(clone)
            set_default_provider_preset(clone.id, path)
            loaded = load_provider_presets(path, migrate=False)
            self.assertTrue(next(p for p in loaded if p.id == clone.id).isDefault)
            export_presets(export_path, loaded)
            exported = json.loads(export_path.read_text(encoding="utf-8"))
            for item in exported["presets"]:
                self.assertEqual(item["config"].get("apiKey"), "")
                self.assertEqual(item["config"].get("volcengineAccessKey"), "")

    def test_import_presets_renames_and_preserves_existing(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "target.json"
            source = Path(td) / "source.json"
            save_provider_presets([
                ProviderPreset(
                    id="existing",
                    type="translation",
                    name="默认翻译配置",
                    provider="openai_compatible",
                    isDefault=True,
                    config={},
                )
            ], target)
            source.write_text(json.dumps({
                "presets": [{
                    "id": "incoming",
                    "type": "translation",
                    "name": "默认翻译配置",
                    "provider": "openai_compatible",
                    "enabled": True,
                    "isDefault": True,
                    "config": {},
                }]
            }, ensure_ascii=False), encoding="utf-8")
            count = import_presets(source, target)
            loaded = load_provider_presets(target, migrate=False)
            self.assertEqual(count, 1)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(sum(1 for p in loaded if p.isDefault), 1)
            self.assertTrue(any("导入副本" in p.name for p in loaded))


if __name__ == "__main__":
    unittest.main()
