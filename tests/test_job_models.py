"""Tests for job_models and pipeline_types."""
import unittest

from job_models import (
    Artifact,
    ErrorCode,
    JobSpec,
    SubtitleSegment,
    SubtitleStyle,
    TaskResult,
    TranslationConfig,
    WordTiming,
    error_message,
    segments_from_srt_dicts,
    validate_segments,
)
from pipeline_types import (
    PipelineStage,
    ProcessingMode,
    SubtitleMode,
    stage_progress,
)


class TestPipelineStage(unittest.TestCase):
    def test_stage_values(self):
        self.assertEqual(PipelineStage.PREPARE.value, "prepare")
        self.assertEqual(PipelineStage.COMPLETED.value, "completed")

    def test_ui_text(self):
        self.assertEqual(PipelineStage.PREPARE.ui_text(), "准备")
        self.assertEqual(PipelineStage.TRANSLATE.ui_text(), "翻译")
        self.assertEqual(PipelineStage.COMPLETED.ui_text(), "完成")

    def test_is_terminal(self):
        self.assertFalse(PipelineStage.PREPARE.is_terminal())
        self.assertFalse(PipelineStage.TRANSLATE.is_terminal())
        self.assertTrue(PipelineStage.COMPLETED.is_terminal())
        self.assertTrue(PipelineStage.ERROR.is_terminal())
        self.assertTrue(PipelineStage.CANCELLED.is_terminal())

    def test_stage_from_string(self):
        self.assertEqual(PipelineStage("prepare"), PipelineStage.PREPARE)


class TestStageProgress(unittest.TestCase):
    def test_translate_mode_progress(self):
        self.assertEqual(stage_progress(PipelineStage.PREPARE, 0), 0)
        self.assertEqual(stage_progress(PipelineStage.PREPARE, 100), 5)
        self.assertEqual(stage_progress(PipelineStage.TRANSLATE, 50), 57)
        self.assertEqual(stage_progress(PipelineStage.COMPLETED, 0), 100)
        self.assertEqual(stage_progress(PipelineStage.ERROR, 0), 0)

    def test_dub_mode_progress(self):
        pct = stage_progress(PipelineStage.TTS, 50, ProcessingMode.DUB)
        self.assertGreater(pct, 58)
        self.assertLess(pct, 78)

    def test_clamped_progress(self):
        pct = stage_progress(PipelineStage.PREPARE, 200)
        self.assertEqual(pct, 5)
        pct = stage_progress(PipelineStage.PREPARE, -50)
        self.assertEqual(pct, 0)


class TestSubtitleStyle(unittest.TestCase):
    def test_defaults(self):
        style = SubtitleStyle()
        self.assertEqual(style.preset, "default")
        self.assertEqual(style.font_size, 48)

    def test_to_dict_and_back(self):
        style = SubtitleStyle(preset="netflix", font_size=52, bold=True)
        d = style.to_dict()
        restored = SubtitleStyle.from_dict(d)
        self.assertEqual(restored.preset, "netflix")
        self.assertEqual(restored.font_size, 52)
        self.assertTrue(restored.bold)

    def test_from_dict_ignores_unknown_fields(self):
        d = {"preset": "youtube", "font_size": 44, "unknown_field": True}
        style = SubtitleStyle.from_dict(d)
        self.assertEqual(style.preset, "youtube")
        self.assertFalse(hasattr(style, "unknown_field"))

    def test_presets(self):
        presets = SubtitleStyle.presets()
        self.assertIn("default", presets)
        self.assertIn("netflix", presets)
        self.assertIn("bilingual", presets)
        self.assertEqual(presets["netflix"].preset, "netflix")


class TestWordTiming(unittest.TestCase):
    def test_roundtrip(self):
        w = WordTiming(word="hello", start=1.0, end=1.5, score=0.95)
        d = w.to_dict()
        restored = WordTiming.from_dict(d)
        self.assertEqual(restored.word, "hello")
        self.assertAlmostEqual(restored.start, 1.0)
        self.assertAlmostEqual(restored.end, 1.5)


class TestSubtitleSegment(unittest.TestCase):
    def test_valid_segment(self):
        seg = SubtitleSegment(index=1, start=0.0, end=2.0, text="Hello world")
        self.assertEqual(seg.validate(), [])

    def test_invalid_negative_start(self):
        seg = SubtitleSegment(index=1, start=-1.0, end=2.0, text="Hello")
        errors = seg.validate()
        self.assertTrue(any("start" in e for e in errors))

    def test_invalid_end_before_start(self):
        seg = SubtitleSegment(index=1, start=5.0, end=3.0, text="Hello")
        errors = seg.validate()
        self.assertTrue(any("end" in e for e in errors))

    def test_invalid_empty_text(self):
        seg = SubtitleSegment(index=1, start=0.0, end=2.0, text="  ")
        errors = seg.validate()
        self.assertTrue(any("empty" in e for e in errors))

    def test_roundtrip(self):
        words = [WordTiming(word="hi", start=0.0, end=0.5)]
        seg = SubtitleSegment(
            index=1, start=0.0, end=2.0, text="Hi there",
            translation="你好", speaker="A", words=words,
            metadata={"confidence": 0.9},
        )
        d = seg.to_dict()
        restored = SubtitleSegment.from_dict(d)
        self.assertEqual(restored.index, 1)
        self.assertEqual(restored.text, "Hi there")
        self.assertEqual(restored.translation, "你好")
        self.assertEqual(len(restored.words), 1)

    def test_from_srt_dict(self):
        srt_data = {"start": 1.0, "end": 3.5, "text": "Hello world"}
        seg = SubtitleSegment.from_srt_dict(srt_data, index=5)
        self.assertEqual(seg.index, 5)
        self.assertAlmostEqual(seg.start, 1.0)
        self.assertEqual(seg.text, "Hello world")

    def test_to_srt_dict(self):
        seg = SubtitleSegment(index=1, start=0.0, end=2.0, text="Hi", translation="你好")
        d = seg.to_srt_dict()
        self.assertEqual(d["text"], "Hi")
        self.assertEqual(d["translation"], "你好")

    def test_to_srt_dict_no_translation(self):
        seg = SubtitleSegment(index=1, start=0.0, end=2.0, text="Hi")
        d = seg.to_srt_dict()
        self.assertNotIn("translation", d)

    def test_segments_from_srt_dicts(self):
        data = [
            {"start": 0.0, "end": 2.0, "text": "One"},
            {"start": 2.5, "end": 4.0, "text": "Two"},
        ]
        segs = segments_from_srt_dicts(data)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0].index, 1)
        self.assertEqual(segs[1].index, 2)


class TestValidateSegments(unittest.TestCase):
    def test_valid_timeline(self):
        segs = [
            SubtitleSegment(index=1, start=0.0, end=2.0, text="A"),
            SubtitleSegment(index=2, start=2.5, end=4.0, text="B"),
        ]
        self.assertEqual(validate_segments(segs), [])

    def test_disordered_timeline(self):
        segs = [
            SubtitleSegment(index=1, start=5.0, end=7.0, text="A"),
            SubtitleSegment(index=2, start=3.0, end=4.0, text="B"),
        ]
        errors = validate_segments(segs)
        self.assertTrue(any("disorder" in e for e in errors))


class TestArtifact(unittest.TestCase):
    def test_roundtrip(self):
        art = Artifact(kind="source_srt", path="subtitles/source.srt",
                       language="en", created_at="2026-06-13T12:00:00", size_bytes=1024)
        d = art.to_dict()
        restored = Artifact.from_dict(d)
        self.assertEqual(restored.kind, "source_srt")
        self.assertEqual(restored.path, "subtitles/source.srt")
        self.assertEqual(restored.language, "en")
        self.assertEqual(restored.size_bytes, 1024)

    def test_optional_fields_omitted(self):
        art = Artifact(kind="log", path="logs/build.log")
        d = art.to_dict()
        self.assertNotIn("language", d)
        self.assertNotIn("created_at", d)
        self.assertNotIn("size_bytes", d)


class TestTranslationConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = TranslationConfig()
        self.assertEqual(cfg.provider, "openai_compatible")
        self.assertEqual(cfg.quality_mode, "fast")

    def test_roundtrip(self):
        cfg = TranslationConfig(base_url="https://api.example.com/v1",
                                model="gpt-4o", quality_mode="quality")
        d = cfg.to_dict()
        restored = TranslationConfig.from_dict(d)
        self.assertEqual(restored.base_url, "https://api.example.com/v1")
        self.assertEqual(restored.quality_mode, "quality")

    def test_from_dict_ignores_unknown(self):
        d = {"provider": "openai_compatible", "extra_key": "value"}
        cfg = TranslationConfig.from_dict(d)
        self.assertEqual(cfg.provider, "openai_compatible")


class TestJobSpec(unittest.TestCase):
    def test_auto_uuid(self):
        spec = JobSpec(source="test.mp4", workspace_dir="/tmp/test")
        self.assertTrue(len(spec.job_id) > 0)

    def test_validate_subtitle_mode(self):
        spec = JobSpec(source="test.mp4", workspace_dir="/tmp/test")
        self.assertEqual(spec.validate(), [])

    def test_validate_translate_requires_target(self):
        spec = JobSpec(source="test.mp4", workspace_dir="/tmp/test",
                       mode="translate")
        errors = spec.validate()
        self.assertTrue(any("target_language" in e for e in errors))

    def test_validate_translate_requires_provider(self):
        spec = JobSpec(source="test.mp4", workspace_dir="/tmp/test",
                       mode="translate", target_language="zh-CN")
        errors = spec.validate()
        self.assertTrue(any("translation_provider" in e for e in errors))

    def test_validate_dub_requires_tts(self):
        spec = JobSpec(
            source="test.mp4", workspace_dir="/tmp/test",
            mode="dub", target_language="zh-CN",
            translation_provider=TranslationConfig(),
        )
        errors = spec.validate()
        self.assertTrue(any("tts_provider" in e for e in errors))

    def test_roundtrip(self):
        spec = JobSpec(
            source="https://youtube.com/watch?v=test",
            source_type="url",
            mode="translate",
            source_language="en",
            target_language="zh-CN",
            subtitle_mode="bilingual",
            burn_subtitles=True,
            translation_provider=TranslationConfig(model="gpt-4o"),
            subtitle_style=SubtitleStyle(preset="netflix"),
            workspace_dir="D:/output/test",
        )
        d = spec.to_dict()
        restored = JobSpec.from_dict(d)
        self.assertEqual(restored.source, spec.source)
        self.assertEqual(restored.mode, "translate")
        self.assertEqual(restored.target_language, "zh-CN")
        self.assertTrue(restored.burn_subtitles)
        self.assertEqual(restored.translation_provider.model, "gpt-4o")
        self.assertEqual(restored.subtitle_style.preset, "netflix")

    def test_from_dict_ignores_unknown(self):
        d = {
            "source": "test.mp4",
            "workspace_dir": "/tmp",
            "future_field": True,
        }
        spec = JobSpec.from_dict(d)
        self.assertEqual(spec.source, "test.mp4")


class TestTaskResult(unittest.TestCase):
    def test_defaults(self):
        result = TaskResult()
        self.assertEqual(result.status, "pending")
        self.assertEqual(result.progress, 0)

    def test_roundtrip(self):
        segs = [SubtitleSegment(index=1, start=0.0, end=2.0, text="Hi")]
        arts = [Artifact(kind="source_srt", path="subs.srt")]
        result = TaskResult(
            job_id="test-id",
            status="completed",
            stage="completed",
            progress=100,
            message="Done",
            detected_language="en",
            segments=segs,
            artifacts=arts,
        )
        d = result.to_dict()
        restored = TaskResult.from_dict(d)
        self.assertEqual(restored.job_id, "test-id")
        self.assertEqual(restored.status, "completed")
        self.assertEqual(len(restored.segments), 1)
        self.assertEqual(len(restored.artifacts), 1)

    def test_error_fields_omitted_when_none(self):
        result = TaskResult(job_id="x")
        d = result.to_dict()
        self.assertNotIn("error_code", d)
        self.assertNotIn("error_detail", d)

    def test_add_and_find_artifacts(self):
        result = TaskResult(job_id="x")
        result.add_artifact(Artifact(kind="source_srt", path="a.srt"))
        result.add_artifact(Artifact(kind="translated_srt", path="b.srt"))
        result.add_artifact(Artifact(kind="source_srt", path="c.srt"))
        srt_arts = result.find_artifacts("source_srt")
        self.assertEqual(len(srt_arts), 2)


class TestErrorCode(unittest.TestCase):
    def test_known_error_message(self):
        msg = error_message(ErrorCode.FFMPEG_NOT_FOUND)
        self.assertIn("FFmpeg", msg)

    def test_unknown_error_returns_code(self):
        msg = error_message("UNKNOWN_CODE")
        self.assertEqual(msg, "UNKNOWN_CODE")


class TestProcessingMode(unittest.TestCase):
    def test_values(self):
        self.assertEqual(ProcessingMode.SUBTITLE.value, "subtitle")
        self.assertEqual(ProcessingMode.TRANSLATE.value, "translate")
        self.assertEqual(ProcessingMode.DUB.value, "dub")


class TestSubtitleMode(unittest.TestCase):
    def test_values(self):
        self.assertEqual(SubtitleMode.SOURCE.value, "source")
        self.assertEqual(SubtitleMode.TRANSLATED.value, "translated")
        self.assertEqual(SubtitleMode.BILINGUAL.value, "bilingual")


if __name__ == "__main__":
    unittest.main()
