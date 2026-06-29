import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtWidgets import QApplication, QComboBox
except Exception:  # pragma: no cover - optional GUI dependency may be absent in CI
    QApplication = None
    QComboBox = None

from provider_presets import ProviderPreset


@unittest.skipIf(QApplication is None, "PyQt5 is not installed")
class ProviderPresetEditDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_qwen3_tts_preset_uses_voice_dropdown(self):
        from ui.provider_presets_dialog import ProviderPresetEditDialog

        preset = ProviderPreset(
            id="preset-qwen3",
            type="tts",
            name="本地 Qwen3-TTS",
            provider="qwen3-tts",
            enabled=True,
            isDefault=True,
            createdAt="2026-06-24T00:00:00",
            updatedAt="2026-06-24T00:00:00",
            config={"voice": "Vivian", "format": "wav", "sampleRate": 24000},
        )

        dialog = ProviderPresetEditDialog("tts", preset)
        self.assertIsInstance(dialog.voice, QComboBox)
        self.assertTrue(dialog.voice.isEditable())
        # Voices now carry a language tag in the display text, so look up by data.
        self.assertGreaterEqual(dialog.voice.findData("Vivian"), 0)
        self.assertGreaterEqual(dialog.voice.findData("Serena"), 0)
        self.assertEqual(dialog.voice.currentData(), "Vivian")
        self.assertEqual(dialog.to_preset().config["voice"], "Vivian")

    def test_custom_qwen3_voice_is_preserved_in_dropdown(self):
        from ui.provider_presets_dialog import ProviderPresetEditDialog

        preset = ProviderPreset(
            id="preset-qwen3-custom",
            type="tts",
            name="自定义 Qwen3-TTS",
            provider="qwen3-tts",
            enabled=True,
            isDefault=False,
            createdAt="2026-06-24T00:00:00",
            updatedAt="2026-06-24T00:00:00",
            config={"voice": "MyCustomVoice", "format": "wav", "sampleRate": 24000},
        )

        dialog = ProviderPresetEditDialog("tts", preset)
        self.assertGreaterEqual(dialog.voice.findData("MyCustomVoice"), 0)
        self.assertEqual(dialog.voice.currentData(), "MyCustomVoice")


if __name__ == "__main__":
    unittest.main()
