from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT_DIR / "localization-engine"
for path in (ROOT_DIR, ENGINE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from job_models import SubtitleSegment
from subtitles.srt_writer import segments_to_srt
from subtitle_ass import segments_to_ass
from translation.quality import (
    has_blocking_issues,
    punctuation_only,
    target_language_issues,
)
from subtitles.validate import validate_translation
from engine.pipeline import PipelineRunner
from engine.progress import ProgressTracker
from engine.task_store import TaskStore


def test_zh_target_flags_japanese_leak_and_half_translated_artifacts():
    ja = "九州では活発な梅雨前線の影響で発達した雨雲がかかり続けています。"
    issues = target_language_issues(ja, "zh-CN", source_text=ja, index=1)
    codes = {issue.code for issue in issues}
    assert "TARGET_LANGUAGE_LEAK_JA" in codes or "TARGET_LANGUAGE_LEAK_JA_FRAGMENT" in codes
    assert has_blocking_issues(issues)

    bad = target_language_issues("日曜日早上接近西日本的可能有性。", "zh-CN", index=2)
    assert any(issue.code == "SUSPICIOUS_TRANSLATION_ARTIFACT" for issue in bad)
    assert has_blocking_issues(bad)


def test_zh_target_accepts_clean_simplified_chinese_weather_translation():
    text = "受活跃梅雨锋影响，九州地区持续被发展旺盛的雨云覆盖。"
    assert target_language_issues(text, "zh-CN", source_text="九州では活発な梅雨前線") == []


def test_punctuation_only_translation_is_blocking_and_can_be_merged(tmp_path):
    assert punctuation_only("。")
    segments = [
        SubtitleSegment(index=1, start=0, end=1, text="A", translation="请注意最新信息"),
        SubtitleSegment(index=2, start=1, end=1.2, text="。", translation="。"),
    ]
    runner = PipelineRunner(TaskStore(tmp_path / "tasks"), ProgressTracker())
    runner._normalize_translation_segments(tmp_path, segments, "zh-CN")
    assert segments[0].translation == "请注意最新信息。"
    assert segments[1].translation == ""


def test_translated_exports_do_not_fallback_to_source_when_translation_missing():
    segments = [
        SubtitleSegment(index=1, start=0, end=1, text="九州では雨です", translation="九州正在下雨"),
        SubtitleSegment(index=2, start=1, end=2, text="あすも注意", translation=""),
    ]

    srt = segments_to_srt(segments, mode="translated")
    assert "九州正在下雨" in srt
    assert "あすも注意" not in srt

    # ASS target-only output should also avoid burning source text into video.
    from job_models import SubtitleStyle
    ass = segments_to_ass(segments, SubtitleStyle(), mode="translated")
    assert "九州正在下雨" in ass
    assert "あすも注意" not in ass


def test_validate_translation_reports_target_language_issues():
    segments = [
        SubtitleSegment(index=1, start=0, end=1, text="あすも注意", translation="あすも注意"),
    ]
    warnings = validate_translation(segments, "zh-CN")
    codes = {code for code, _message, _context in warnings}
    assert "TARGET_LANGUAGE_LEAK_JA" in codes or "TARGET_LANGUAGE_LEAK_JA_FRAGMENT" in codes
