from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TtsChunk:
    chunk_index: int
    segment_indexes: List[int]
    text: str
    start_time: float
    end_time: float


DEFAULT_CHUNK_OPTIONS: Dict[str, Any] = {
    "max_chars": 500,
    "max_duration_sec": 45,
    "prefer_sentence_end": True,
    "min_chars": 40,
}

_SENTENCE_END_CHARS = {".", "!", "?", "。", "！", "？", "\n", ";"}


def build_tts_chunks(
    segments: List[Any],
    options: Optional[Dict[str, Any]] = None,
) -> List[TtsChunk]:
    """Merge short subtitle segments into longer TTS chunks.

    Args:
        segments: List of objects with .text, .translation, .start, .end, .index
        options: Override keys for max_chars, max_duration_sec, min_chars,
                 prefer_sentence_end.

    Returns:
        List of TtsChunk with merged text and segment index mapping.
    """
    opts = dict(DEFAULT_CHUNK_OPTIONS)
    if options:
        opts.update(options)

    max_chars = int(opts["max_chars"])
    max_duration_sec = float(opts["max_duration_sec"])
    min_chars = int(opts["min_chars"])
    prefer_sentence_end = bool(opts["prefer_sentence_end"])

    candidates = [
        seg for seg in segments
        if (seg.translation or seg.text or "").strip()
    ]
    candidates.sort(key=lambda seg: (seg.start, seg.index))

    chunks: List[TtsChunk] = []
    current_segments: List[Any] = []
    current_text_parts: List[str] = []
    current_chars = 0
    current_start = 0.0
    current_end = 0.0

    def _flush():
        nonlocal current_segments, current_text_parts, current_chars
        if not current_segments:
            return
        text = " ".join(part for part in current_text_parts if part.strip())
        if not text.strip():
            current_segments = []
            current_text_parts = []
            current_chars = 0
            return
        chunks.append(TtsChunk(
            chunk_index=len(chunks),
            segment_indexes=[s.index for s in current_segments],
            text=text.strip(),
            start_time=current_start,
            end_time=current_end,
        ))
        current_segments = []
        current_text_parts = []
        current_chars = 0

    for seg in candidates:
        text = seg.translation or seg.text
        if not text.strip():
            continue
        text = text.strip()
        seg_chars = len(text)
        seg_duration = seg.end - seg.start

        if (current_chars + seg_chars > max_chars or
            (current_end > 0 and current_end - current_start + seg_duration > max_duration_sec)):
            _flush()

        if not current_segments:
            current_start = seg.start

        current_segments.append(seg)
        current_text_parts.append(text)
        current_chars += seg_chars
        current_end = seg.end

        if prefer_sentence_end and text and text[-1] in _SENTENCE_END_CHARS:
            if current_chars >= min_chars:
                _flush()

    _flush()

    if not chunks:
        return chunks

    final: List[TtsChunk] = []
    for c in chunks:
        if len(c.text) < min_chars and final:
            prev = final[-1]
            merged_segments = prev.segment_indexes + c.segment_indexes
            merged_text = prev.text + " " + c.text
            final[-1] = TtsChunk(
                chunk_index=prev.chunk_index,
                segment_indexes=merged_segments,
                text=merged_text.strip(),
                start_time=prev.start_time,
                end_time=c.end_time,
            )
        else:
            final.append(c)

    for i, c in enumerate(final):
        c.chunk_index = i

    return final
