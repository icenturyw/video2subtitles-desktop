"""Subtitle batching for efficient translation requests.

Groups segments into batches respecting character limits while preserving segment order.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from job_models import SubtitleSegment


def batch_segments(segments: List[SubtitleSegment],
                   max_chars: int = 16000,
                   min_items: int = 1,
                   max_items: int = 50) -> List[List[SubtitleSegment]]:
    """Split segments into translation batches.

    Args:
        segments: Full list of segments to batch.
        max_chars: Maximum total characters per batch (including JSON overhead).
        min_items: Minimum segments per batch.
        max_items: Maximum segments per batch.

    Returns:
        List of batches, where each batch is a list of SubtitleSegment.
    """
    if not segments:
        return []

    if len(segments) <= max_items:
        char_count = sum(len(s.text) for s in segments)
        if char_count <= max_chars:
            return [segments]

    batches: List[List[SubtitleSegment]] = []
    current: List[SubtitleSegment] = []
    current_chars = 0

    for seg in segments:
        seg_len = len(seg.text)
        would_exceed_max = (seg_len + current_chars > max_chars
                            and len(current) >= min_items)
        would_exceed_count = len(current) >= max_items

        if (would_exceed_max or would_exceed_count) and current:
            batches.append(current)
            current = []
            current_chars = 0

        current.append(seg)
        current_chars += seg_len

    if current:
        batches.append(current)

    return batches


def batch_to_request(batch: List[SubtitleSegment]) -> List[Dict]:
    """Convert a batch of SubtitleSegments to API request format."""
    return [{"id": seg.index, "text": seg.text} for seg in batch]


def batch_to_tsv(batch: List[SubtitleSegment]) -> str:
    """Convert a batch of SubtitleSegments to tab-separated input format.

    Returns a multi-line string where each line is ``id<TAB>text``.
    """
    return "\n".join(f"{seg.index}\t{seg.text}" for seg in batch)


class CheckpointManager:
    """Manages translation checkpoint files for resumable translation."""

    def __init__(self, checkpoint_dir: Path):
        self._dir = Path(checkpoint_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._completed: Set[int] = set()
        self._load()

    def _load(self) -> None:
        checkpoint_file = self._dir / "completed_ids.json"
        if checkpoint_file.exists():
            try:
                data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
                self._completed = set(data.get("completed_ids", []))
            except Exception:
                self._completed = set()

    def save(self) -> None:
        checkpoint_file = self._dir / "completed_ids.json"
        data = {"completed_ids": sorted(self._completed)}
        self._dir.mkdir(parents=True, exist_ok=True)
        # Atomic write via temp file
        tmp = checkpoint_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        if checkpoint_file.exists():
            import os
            os.replace(str(tmp), str(checkpoint_file))
        else:
            tmp.rename(checkpoint_file)

    def mark_completed(self, segment_ids: List[int]) -> None:
        self._completed.update(segment_ids)
        self.save()

    def is_completed(self, segment_id: int) -> bool:
        return segment_id in self._completed

    def get_pending_segments(self, segments: List[SubtitleSegment]) -> List[SubtitleSegment]:
        """Return segments that haven't been translated yet."""
        return [s for s in segments if s.index not in self._completed]

    def clear(self) -> None:
        self._completed.clear()
        self.save()
