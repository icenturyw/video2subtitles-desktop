"""Tests for TTS voice profile, chunking, and audio normalization.

Run with:
    pytest tests/test_tts_voice_profile.py -v
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Add localization-engine to path
_LOCALIZATION_ENGINE = str(Path(__file__).resolve().parent.parent / "localization-engine")
if _LOCALIZATION_ENGINE not in sys.path:
    sys.path.insert(0, _LOCALIZATION_ENGINE)

from tts.voice_profile import TtsVoiceProfile, voice_profile_hash, profile_to_log_dict
from tts.chunking import build_tts_chunks, TtsChunk, DEFAULT_CHUNK_OPTIONS


# ---------------------------------------------------------------------------
# Voice Profile Hash Tests
# ---------------------------------------------------------------------------

class TestTtsVoiceProfileHash:
    def test_same_profile_same_hash(self):
        p1 = TtsVoiceProfile(provider="qwen", model="CustomVoice", voice="Vivian",
                              seed=42, consistency_mode="stable")
        p2 = TtsVoiceProfile(provider="qwen", model="CustomVoice", voice="Vivian",
                              seed=42, consistency_mode="stable")
        assert voice_profile_hash(p1) == voice_profile_hash(p2)

    def test_different_voice_different_hash(self):
        p1 = TtsVoiceProfile(voice="Vivian", seed=42)
        p2 = TtsVoiceProfile(voice="Serena", seed=42)
        assert voice_profile_hash(p1) != voice_profile_hash(p2)

    def test_different_model_different_hash(self):
        p1 = TtsVoiceProfile(model="CustomVoice")
        p2 = TtsVoiceProfile(model="VoiceDesign")
        assert voice_profile_hash(p1) != voice_profile_hash(p2)

    def test_different_seed_different_hash(self):
        p1 = TtsVoiceProfile(seed=42)
        p2 = TtsVoiceProfile(seed=7)
        assert voice_profile_hash(p1) != voice_profile_hash(p2)

    def test_different_prompt_audio_different_hash(self):
        p1 = TtsVoiceProfile(prompt_audio_hash="abc123")
        p2 = TtsVoiceProfile(prompt_audio_hash="def456")
        assert voice_profile_hash(p1) != voice_profile_hash(p2)

    def test_different_temperature_different_hash(self):
        p1 = TtsVoiceProfile(temperature=0.6)
        p2 = TtsVoiceProfile(temperature=0.8)
        assert voice_profile_hash(p1) != voice_profile_hash(p2)

    def test_different_top_p_different_hash(self):
        p1 = TtsVoiceProfile(top_p=0.9)
        p2 = TtsVoiceProfile(top_p=0.95)
        assert voice_profile_hash(p1) != voice_profile_hash(p2)

    def test_different_consistency_mode_different_hash(self):
        p1 = TtsVoiceProfile(consistency_mode="fast")
        p2 = TtsVoiceProfile(consistency_mode="stable")
        assert voice_profile_hash(p1) != voice_profile_hash(p2)

    def test_hash_is_deterministic(self):
        p = TtsVoiceProfile(provider="qwen", model="CustomVoice", voice="Vivian",
                              seed=42, temperature=0.6, top_p=0.9,
                              consistency_mode="stable")
        h1 = voice_profile_hash(p)
        h2 = voice_profile_hash(p)
        assert h1 == h2
        assert len(h1) == 16  # sha256 prefix

    def test_hash_with_all_fields(self):
        p = TtsVoiceProfile(
            provider="qwen",
            model="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
            voice="Vivian",
            prompt_audio_path="/tmp/ref.wav",
            prompt_audio_hash="abc123def456",
            prompt_asset_id="asset_001",
            language="zh",
            style="warm narrator",
            speed=1.0,
            sample_rate=24000,
            seed=42,
            temperature=0.6,
            top_p=0.9,
            consistency_mode="stable",
        )
        h = voice_profile_hash(p)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_profile_to_log_dict_contains_all_keys(self):
        p = TtsVoiceProfile(provider="qwen", model="CustomVoice", voice="Vivian",
                              seed=42, consistency_mode="stable")
        h = voice_profile_hash(p)
        d = profile_to_log_dict(p, h)
        assert d["voice_profile_hash"] == h
        assert d["provider"] == "qwen"
        assert d["model"] == "CustomVoice"
        assert d["voice"] == "Vivian"
        assert d["seed"] == 42
        assert d["consistency_mode"] == "stable"


# ---------------------------------------------------------------------------
# Chunking Tests
# ---------------------------------------------------------------------------

class MockSegment:
    """Minimal mock for SubtitleSegment-like objects."""
    def __init__(self, index: int, text: str, start: float, end: float,
                 translation: str = ""):
        self.index = index
        self.text = text
        self.start = start
        self.end = end
        self.translation = translation


class TestBuildTtsChunks:
    def test_single_segment_unchanged(self):
        segs = [MockSegment(1, "Hello world", 0.0, 2.0)]
        chunks = build_tts_chunks(segs)
        assert len(chunks) == 1
        assert chunks[0].text == "Hello world"
        assert chunks[0].segment_indexes == [1]
        assert chunks[0].start_time == 0.0
        assert chunks[0].end_time == 2.0

    def test_short_segments_merged(self):
        segs = [
            MockSegment(1, "Hello", 0.0, 1.0),
            MockSegment(2, "world", 1.0, 2.0),
            MockSegment(3, "this is a test", 2.0, 4.0),
        ]
        chunks = build_tts_chunks(segs)
        assert len(chunks) == 1
        assert chunks[0].segment_indexes == [1, 2, 3]
        assert "Hello" in chunks[0].text
        assert "world" in chunks[0].text

    def test_empty_text_skipped(self):
        segs = [
            MockSegment(1, "Hello", 0.0, 1.0),
            MockSegment(2, "", 1.0, 2.0),
            MockSegment(3, "world", 2.0, 3.0),
        ]
        chunks = build_tts_chunks(segs)
        assert len(chunks) == 2
        assert chunks[0].segment_indexes == [1]
        assert chunks[1].segment_indexes == [3]

    def test_max_chars_respected(self):
        segs = [
            MockSegment(1, "A" * 200, 0.0, 5.0),
            MockSegment(2, "B" * 200, 5.0, 10.0),
            MockSegment(3, "C" * 200, 10.0, 15.0),
        ]
        chunks = build_tts_chunks(segs, {"max_chars": 300, "min_chars": 1})
        assert len(chunks) >= 2  # should split because 200+200 > 300

    def test_sentence_end_splits(self):
        segs = [
            MockSegment(1, "First sentence.", 0.0, 2.0),
            MockSegment(2, "Second sentence.", 2.0, 4.0),
            MockSegment(3, "Third sentence!", 4.0, 6.0),
        ]
        chunks = build_tts_chunks(segs, {"min_chars": 5})
        assert len(chunks) >= 1
        all_indexes = []
        for c in chunks:
            all_indexes.extend(c.segment_indexes)
        assert all_indexes == [1, 2, 3]

    def test_timing_mapping_preserved(self):
        segs = [
            MockSegment(5, "Segment five", 10.0, 12.0),
            MockSegment(6, "Segment six", 12.0, 14.0),
        ]
        chunks = build_tts_chunks(segs)
        assert len(chunks) == 1
        assert chunks[0].start_time == 10.0
        assert chunks[0].end_time == 14.0
        assert chunks[0].segment_indexes == [5, 6]

    def test_translation_preferred_over_text(self):
        segs = [
            MockSegment(1, "Source text", 0.0, 1.0, translation="Translated text"),
        ]
        chunks = build_tts_chunks(segs)
        assert "Translated text" in chunks[0].text

    def test_separate_chunks_when_gap_too_large(self):
        segs = [
            MockSegment(1, "First", 0.0, 1.0),
            MockSegment(2, "Second", 100.0, 101.0),
        ]
        chunks = build_tts_chunks(segs)
        assert len(chunks) == 2
        assert chunks[0].segment_indexes == [1]
        assert chunks[1].segment_indexes == [2]

    def test_short_gap_can_still_merge(self):
        segs = [
            MockSegment(1, "Hello", 0.0, 1.0),
            MockSegment(2, "world", 1.1, 2.0),
        ]
        chunks = build_tts_chunks(segs, {"max_gap_sec": 0.8})
        assert len(chunks) == 1
        assert chunks[0].segment_indexes == [1, 2]

    def test_timeline_span_limit_splits_chunks(self):
        segs = [
            MockSegment(1, "A", 0.0, 1.0),
            MockSegment(2, "B", 8.0, 9.0),
            MockSegment(3, "C", 16.0, 17.0),
        ]
        chunks = build_tts_chunks(segs, {
            "max_gap_sec": 20.0,
            "max_timeline_span_sec": 12.0,
        })
        assert len(chunks) >= 2

    def test_empty_segments_list(self):
        chunks = build_tts_chunks([])
        assert chunks == []

    def test_all_empty_text_returns_empty(self):
        segs = [
            MockSegment(1, "", 0.0, 1.0),
            MockSegment(2, "", 2.0, 3.0),
        ]
        chunks = build_tts_chunks(segs)
        assert chunks == []

    def test_large_text_respects_multiple_chunks(self):
        segs = [
            MockSegment(i, f"Sentence {i}.", float(i), float(i) + 0.5)
            for i in range(1, 21)
        ]
        chunks = build_tts_chunks(segs, {"max_chars": 50, "min_chars": 1})
        assert len(chunks) >= 2


# ---------------------------------------------------------------------------
# Integration: profile hash + chunking
# ---------------------------------------------------------------------------

class TestProfileAndChunking:
    def test_chunks_use_same_profile_hash(self):
        """Verify that profile hash stays stable across multiple chunks."""
        profile = TtsVoiceProfile(provider="qwen", model="CustomVoice",
                                    voice="Vivian", seed=42,
                                    consistency_mode="stable")
        h = voice_profile_hash(profile)
        # Simulate two chunks using same profile
        assert voice_profile_hash(profile) == h
        profile2 = TtsVoiceProfile(provider="qwen", model="CustomVoice",
                                     voice="Vivian", seed=42,
                                     consistency_mode="stable")
        assert voice_profile_hash(profile2) == h

    def test_fast_vs_stable_profile_differs(self):
        fast = TtsVoiceProfile(consistency_mode="fast")
        stable = TtsVoiceProfile(consistency_mode="stable")
        assert voice_profile_hash(fast) != voice_profile_hash(stable)
