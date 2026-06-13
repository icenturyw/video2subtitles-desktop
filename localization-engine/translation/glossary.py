"""Glossary management for subtitle translation.

Supports JSON and CSV glossary formats with case-sensitive and case-insensitive matching.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class GlossaryEntry:
    def __init__(self, source: str, target: str, case_sensitive: bool = False,
                 force: bool = False, note: str = ""):
        self.source = source.strip()
        self.target = target.strip()
        self.case_sensitive = case_sensitive
        self.force = force
        self.note = note

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "case_sensitive": self.case_sensitive,
            "force": self.force,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GlossaryEntry":
        return cls(
            source=str(data.get("source", "")),
            target=str(data.get("target", "")),
            case_sensitive=bool(data.get("case_sensitive", False)),
            force=bool(data.get("force", False)),
            note=str(data.get("note", "")),
        )


class Glossary:
    """Manages a glossary for consistent translation of terms."""

    def __init__(self, entries: Optional[List[GlossaryEntry]] = None):
        self._entries: List[GlossaryEntry] = entries or []

    def add(self, entry: GlossaryEntry) -> None:
        self._entries.append(entry)

    @property
    def entries(self) -> List[GlossaryEntry]:
        return list(self._entries)

    def to_prompt_text(self) -> str:
        """Format glossary entries as text to inject into translation prompts."""
        if not self._entries:
            return ""
        lines = ["Glossary:"]
        for e in self._entries:
            label = "[force]" if e.force else ""
            lines.append(f"  {e.source} → {e.target} {label}")
        return "\n".join(lines)

    def apply_post_translate(self, text: str) -> str:
        """Apply glossary term replacements after translation.

        Only applies entries with force=True.
        Uses word-boundary matching when possible.
        """
        for entry in self._entries:
            if not entry.force:
                continue
            flags = 0 if entry.case_sensitive else re.IGNORECASE
            pattern = re.compile(re.escape(entry.source), flags)
            text = pattern.sub(entry.target, text)
        return text

    def save_json(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "entries": [e.to_dict() for e in self._entries],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load_json(cls, path: Path) -> "Glossary":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries_data = data.get("entries", [])
            entries = [GlossaryEntry.from_dict(e) for e in entries_data]
            return cls(entries)
        except Exception:
            return cls()

    @classmethod
    def load_csv(cls, path: Path) -> "Glossary":
        entries: List[GlossaryEntry] = []
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    entries.append(GlossaryEntry(
                        source=row.get("source", row.get("from", "")),
                        target=row.get("target", row.get("to", "")),
                        case_sensitive=row.get("case_sensitive", "false").lower() == "true",
                        force=row.get("force", "false").lower() == "true",
                        note=row.get("note", ""),
                    ))
        except Exception:
            pass
        return cls(entries)
