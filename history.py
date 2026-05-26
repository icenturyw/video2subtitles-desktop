"""History manager for tracking processed videos and subtitles"""
import json
import time
from pathlib import Path


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
        import re
        subtitles = []
        try:
            text = Path(srt_path).read_text(encoding="utf-8")
            blocks = re.split(r"\n\s*\n", text.strip())
            for block in blocks:
                lines = block.strip().split("\n")
                if len(lines) < 3:
                    continue
                time_line = lines[1] if len(lines) > 1 else ""
                m = re.match(
                    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)",
                    time_line,
                )
                if not m:
                    continue
                start = (
                    int(m.group(1)) * 3600
                    + int(m.group(2)) * 60
                    + int(m.group(3))
                    + int(m.group(4)) / 1000
                )
                end = (
                    int(m.group(5)) * 3600
                    + int(m.group(6)) * 60
                    + int(m.group(7))
                    + int(m.group(8)) / 1000
                )
                text_content = "\n".join(lines[2:])
                subtitles.append({
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "text": text_content,
                })
        except Exception:
            pass
        return subtitles

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
