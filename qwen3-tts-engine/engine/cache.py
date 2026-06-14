from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class TTSCache:
    def __init__(self, cache_dir: Path):
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._segments_dir = cache_dir / "segments"
        self._segments_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, text: str, speaker: str, language: str,
                   instruct: str = "", model_id: str = "") -> str:
        raw = f"{model_id}|{language}|{speaker}|{instruct}|{text}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]

    def get(self, text: str, speaker: str, language: str,
            instruct: str = "", model_id: str = "") -> Optional[Path]:
        key = self._cache_key(text, speaker, language, instruct, model_id)
        path = self._segments_dir / f"{key}.wav"
        return path if path.exists() else None

    def put(self, text: str, speaker: str, language: str,
            audio_path: Path, instruct: str = "",
            model_id: str = "") -> Path:
        key = self._cache_key(text, speaker, language, instruct, model_id)
        dst = self._segments_dir / f"{key}.wav"
        if not dst.exists():
            shutil.copy2(str(audio_path), str(dst))
        return dst

    def clear(self):
        for f in self._segments_dir.glob("*.wav"):
            f.unlink(missing_ok=True)

    def get_segment_info(self, segment_id: str) -> Optional[Dict]:
        info_file = self._cache_dir / "index.json"
        if info_file.exists():
            data = json.loads(info_file.read_text("utf-8"))
            return data.get(segment_id)
        return None

    def save_segment_info(self, segment_id: str, info: Dict):
        info_file = self._cache_dir / "index.json"
        if info_file.exists():
            data = json.loads(info_file.read_text("utf-8"))
        else:
            data = {}
        data[segment_id] = info
        info_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
