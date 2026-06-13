import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "localization-engine"))

from translation.glossary import Glossary, GlossaryEntry


class TestGlossary(unittest.TestCase):
    def test_empty_glossary(self):
        g = Glossary()
        self.assertEqual(g.to_prompt_text(), "")

    def test_glossary_prompt_text(self):
        g = Glossary()
        g.add(GlossaryEntry(source="OpenAI", target="开放AI", force=True))
        text = g.to_prompt_text()
        self.assertIn("OpenAI", text)
        self.assertIn("开放AI", text)
        self.assertIn("[force]", text)

    def test_glossary_prompt_without_force(self):
        g = Glossary()
        g.add(GlossaryEntry(source="API", target="接口"))
        text = g.to_prompt_text()
        self.assertIn("API", text)
        self.assertNotIn("[force]", text)

    def test_apply_force_replace(self):
        g = Glossary()
        g.add(GlossaryEntry(source="GPU", target="显卡", force=True))
        result = g.apply_post_translate("Use GPU for processing")
        self.assertIn("显卡", result)
        self.assertNotIn("GPU", result)

    def test_apply_not_force_does_not_replace(self):
        g = Glossary()
        g.add(GlossaryEntry(source="CPU", target="处理器", force=False))
        result = g.apply_post_translate("Use CPU")
        self.assertIn("CPU", result)

    def test_save_and_load_json(self):
        g = Glossary()
        g.add(GlossaryEntry(source="hello", target="你好", case_sensitive=True))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "glossary.json"
            g.save_json(path)
            loaded = Glossary.load_json(path)
            self.assertEqual(len(loaded.entries), 1)
            self.assertEqual(loaded.entries[0].source, "hello")
            self.assertTrue(loaded.entries[0].case_sensitive)

    def test_case_sensitive_matching(self):
        g = Glossary()
        g.add(GlossaryEntry(source="OpenAI", target="OpenAI公司", force=True, case_sensitive=True))
        result = g.apply_post_translate("openai is great")
        self.assertIn("openai", result)


if __name__ == "__main__":
    unittest.main()
