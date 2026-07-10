from __future__ import annotations

import logging
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from engine.model_manager import ModelManager
from engine.cache import TTSCache
from engine.schemas import TaskProgress, TaskStatus
from engine.languages import normalize_language

logger = logging.getLogger("engine.synthesis")

_SAMPLE_RATE = 24000


def _save_audio(audio: np.ndarray, sr: int, output_path: Path) -> float:
    import soundfile as sf
    sf.write(str(output_path), audio, sr)
    duration = len(audio) / sr
    return duration


class Synthesizer:
    def __init__(self, cache: Optional[TTSCache] = None):
        self._manager = ModelManager()
        self._cache = cache
        self._synthesis_lock = threading.Lock()

    def synthesize_custom_voice(
        self,
        text: str,
        speaker: str,
        language: Optional[str] = None,
        instruct: Optional[str] = None,
        output_path: Optional[Path] = None,
        **kwargs,
    ) -> Tuple[float, Path]:
        if output_path is None:
            output_path = Path(tempfile.mktemp(suffix=".wav"))

        lang = normalize_language(language)
        with self._manager.lease() as model:
            with self._synthesis_lock:
                audios, sr = model.generate_custom_voice(
                    text=text,
                    speaker=speaker,
                    language=lang,
                    instruct=instruct,
                    non_streaming_mode=True,
                    **kwargs,
                )
        if not audios:
            raise RuntimeError("Synthesis returned empty audio")

        duration = _save_audio(audios[0], sr, output_path)
        return duration, output_path

    def synthesize_voice_clone(
        self,
        text: str,
        language: Optional[str] = None,
        ref_audio: Optional[str] = None,
        ref_text: Optional[str] = None,
        x_vector_only_mode: bool = False,
        voice_clone_prompt: Optional[list] = None,
        output_path: Optional[Path] = None,
        **kwargs,
    ) -> Tuple[float, Path]:
        if output_path is None:
            output_path = Path(tempfile.mktemp(suffix=".wav"))

        lang = normalize_language(language)
        with self._manager.lease() as model:
            with self._synthesis_lock:
                audios, sr = model.generate_voice_clone(
                    text=text,
                    language=lang,
                    ref_audio=ref_audio,
                    ref_text=ref_text,
                    x_vector_only_mode=x_vector_only_mode,
                    voice_clone_prompt=voice_clone_prompt,
                    non_streaming_mode=True,
                    **kwargs,
                )
        if not audios:
            raise RuntimeError("Voice clone returned empty audio")

        duration = _save_audio(audios[0], sr, output_path)
        return duration, output_path

    def synthesize_voice_design(
        self,
        text: str,
        instruct: str,
        language: Optional[str] = None,
        output_path: Optional[Path] = None,
        **kwargs,
    ) -> Tuple[float, Path]:
        if output_path is None:
            output_path = Path(tempfile.mktemp(suffix=".wav"))

        lang = normalize_language(language)
        with self._manager.lease() as model:
            with self._synthesis_lock:
                audios, sr = model.generate_voice_design(
                    text=text,
                    instruct=instruct,
                    language=lang,
                    non_streaming_mode=True,
                    **kwargs,
                )
        if not audios:
            raise RuntimeError("Voice design returned empty audio")

        duration = _save_audio(audios[0], sr, output_path)
        return duration, output_path

    def create_voice_clone_prompt(
        self,
        ref_audio: str,
        ref_text: Optional[str] = None,
        x_vector_only_mode: bool = False,
    ) -> list:
        with self._manager.lease() as model:
            with self._synthesis_lock:
                return model.create_voice_clone_prompt(
                    ref_audio=ref_audio,
                    ref_text=ref_text,
                    x_vector_only_mode=x_vector_only_mode,
                )

    def synthesize_segments(
        self,
        segments: List[Dict],
        mode: str = "custom_voice",
        task_id: str = "",
    ) -> List[Dict]:
        """Synthesize multiple subtitle segments and return results.

        Each segment dict should have:
            - id: str/int
            - text: str
            - speaker: str (for custom_voice)
            - language: str
            - instruct: str (optional)
        """
        results = []
        total = len(segments)

        for i, seg in enumerate(segments):
            seg_id = seg.get("id", i)
            text = seg.get("text", "")
            if not text.strip():
                results.append({
                    "id": seg_id,
                    "status": "skipped",
                    "duration": 0,
                    "path": None,
                    "error": "empty text",
                })
                continue
            out_path = Path(tempfile.mktemp(suffix=".wav"))
            try:
                cache_key = (text, seg.get("speaker", ""),
                             seg.get("language", ""),
                             seg.get("instruct", ""),
                             self._manager.loaded_model_id or "")
                cached = self._cache.get(*cache_key) if self._cache else None
                if cached:
                    import shutil
                    shutil.copy2(str(cached), str(out_path))
                    duration = _get_wav_duration(out_path)
                else:
                    duration, _ = self.synthesize_custom_voice(
                        text=text,
                        speaker=seg.get("speaker", "Vivian"),
                        language=seg.get("language"),
                        instruct=seg.get("instruct"),
                        output_path=out_path,
                    )
                    if self._cache:
                        self._cache.put(*cache_key, out_path)

                results.append({
                    "id": seg_id,
                    "status": "completed",
                    "duration": duration,
                    "path": str(out_path),
                })
            except Exception as e:
                logger.error("Segment %s synthesis failed: %s", seg_id, e)
                results.append({
                    "id": seg_id,
                    "status": "failed",
                    "duration": 0,
                    "path": None,
                    "error": str(e),
                })

        return results


def _get_wav_duration(path: Path) -> float:
    try:
        import soundfile as sf
        info = sf.info(str(path))
        return info.duration
    except Exception:
        return 0.0
