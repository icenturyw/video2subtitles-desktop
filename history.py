"""History manager for tracking processed videos and subtitles"""
import json
import time
from pathlib import Path

from subtitle_utils import parse_srt_file


class HistoryManager:
    def __init__(self, history_path=None):
        self.history_path = Path(history_path) if history_path else Path.cwd() / "history.json"
        self._cache = None
        self._dirty = False

    @property
    def _data(self):
        if self._cache is None:
            self._load()
        return self._cache

    def _load(self):
        if self.history_path.exists():
            try:
                self._cache = json.loads(self.history_path.read_text(encoding="utf-8"))
            except Exception:
                self._cache = {}
        else:
            self._cache = {}

    def _save(self):
        if not self._dirty:
            return
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._dirty = False

    def get(self, key):
        return self._data.get(key)

    def put(self, key, entry):
        self._data[key] = entry
        self._dirty = True
        self._save()

    def remove(self, key):
        self._data.pop(key, None)
        self._dirty = True
        self._save()

    def exists(self, key):
        return key in self._data

    def get_subtitles(self, key):
        entry = self.get(key)
        if not entry:
            return None
        srt_path = entry.get("srt_path")
        if srt_path and Path(srt_path).exists():
            return self._parse_srt(srt_path)
        subs = entry.get("subtitles")
        if subs:
            return subs
        return None

    def get_output_dir(self, key):
        entry = self.get(key)
        if entry:
            return entry.get("output_dir")
        return None

    def all_entries(self):
        return dict(self._data)

    def clear(self):
        self._cache = {}
        self._dirty = True
        self._save()

    @staticmethod
    def _parse_srt(srt_path):
        return parse_srt_file(srt_path)

    def make_entry(self, subtitles, language, srt_path, output_dir, is_url=False, title=""):
        return {
            "language": language,
            "subtitle_count": len(subtitles) if subtitles else 0,
            "srt_path": str(srt_path) if srt_path else "",
            "output_dir": str(output_dir) if output_dir else "",
            "is_url": is_url,
            "title": title,
            "timestamp": time.time(),
        }

    def make_entry_v2(
        self,
        job_id,
        title,
        output_dir,
        source="",
        source_type="local",
        mode="subtitle",
        source_language="auto",
        target_language="",
        language="unknown",
        subtitle_count=0,
        srt_path="",
        is_url=False,
    ):
        """Create a v2-compatible history entry.

        Preserves all v1 fields for backward compatibility while adding
        v2-specific metadata.
        """
        return {
            "schema_version": 2,
            "job_id": str(job_id),
            "language": language,
            "subtitle_count": subtitle_count,
            "srt_path": str(srt_path) if srt_path else "",
            "output_dir": str(output_dir) if output_dir else "",
            "is_url": is_url,
            "title": title,
            "source": source,
            "source_type": source_type,
            "mode": mode,
            "source_language": source_language,
            "target_language": target_language,
            "timestamp": time.time(),
        }

    def get_entry_mode(self, key):
        """Return the processing mode for a history entry ('subtitle' for v1)."""
        entry = self.get(key)
        if not entry:
            return "subtitle"
        return entry.get("mode", "subtitle")

    def get_job_id(self, key):
        """Return the job_id for a history entry (empty for v1)."""
        entry = self.get(key)
        if not entry:
            return ""
        return entry.get("job_id", "")
