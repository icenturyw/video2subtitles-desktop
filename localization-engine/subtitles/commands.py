"""Subtitle edit commands with undo/redo state management."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .document import SubtitleCue, SubtitleDocument, new_cue_id


class SubtitleCommand(Protocol):
    def apply(self, document: SubtitleDocument) -> SubtitleDocument: ...


@dataclass(frozen=True)
class UpdateCue:
    cue_id: str
    changes: dict

    def apply(self, document: SubtitleDocument) -> SubtitleDocument:
        allowed = {"start_ms", "end_ms", "source_text", "translated_text", "metadata"}
        if set(self.changes) - allowed:
            raise ValueError("Unsupported cue update")
        found = False
        cues = []
        for cue in document.cues:
            if cue.cue_id == self.cue_id:
                cue = cue.updated(**self.changes)
                found = True
            cues.append(cue)
        if not found:
            raise KeyError(self.cue_id)
        return document.clone(cues=cues)


@dataclass(frozen=True)
class InsertCue:
    start_ms: int
    end_ms: int
    source_text: str = ""
    translated_text: str = ""
    after_cue_id: str = ""
    cue_id: str = ""

    def apply(self, document: SubtitleDocument) -> SubtitleDocument:
        cue = SubtitleCue(
            self.cue_id or new_cue_id(), self.start_ms, self.end_ms,
            self.source_text, self.translated_text,
        )
        cues = list(document.cues)
        if self.after_cue_id:
            index = _index(cues, self.after_cue_id) + 1
            cues.insert(index, cue)
        else:
            cues.append(cue)
            cues.sort(key=lambda item: (item.start_ms, item.end_ms, item.cue_id))
        return document.clone(cues=cues)


@dataclass(frozen=True)
class DeleteCue:
    cue_id: str

    def apply(self, document: SubtitleDocument) -> SubtitleDocument:
        index = _index(document.cues, self.cue_id)
        cues = list(document.cues)
        cues.pop(index)
        return document.clone(cues=cues)


@dataclass(frozen=True)
class SplitCue:
    cue_id: str
    character_index: int
    split_ms: int | None = None

    def apply(self, document: SubtitleDocument) -> SubtitleDocument:
        index = _index(document.cues, self.cue_id)
        cue = document.cues[index]
        if self.character_index <= 0 or self.character_index >= len(cue.source_text):
            raise ValueError("Split position must be inside source text")
        ratio = self.character_index / max(1, len(cue.source_text))
        boundary = self.split_ms if self.split_ms is not None else round(
            cue.start_ms + (cue.end_ms - cue.start_ms) * ratio
        )
        if not cue.start_ms < boundary < cue.end_ms:
            raise ValueError("Split time must be inside the cue")
        translation_index = round(len(cue.translated_text) * ratio)
        first = cue.updated(
            end_ms=boundary,
            source_text=cue.source_text[:self.character_index].rstrip(),
            translated_text=cue.translated_text[:translation_index].rstrip(),
        )
        second = SubtitleCue(
            new_cue_id(), boundary, cue.end_ms,
            cue.source_text[self.character_index:].lstrip(),
            cue.translated_text[translation_index:].lstrip(),
        )
        cues = list(document.cues)
        cues[index:index + 1] = [first, second]
        return document.clone(cues=cues)


@dataclass(frozen=True)
class MergeCues:
    first_cue_id: str
    second_cue_id: str
    separator: str = " "

    def apply(self, document: SubtitleDocument) -> SubtitleDocument:
        first = _index(document.cues, self.first_cue_id)
        second = _index(document.cues, self.second_cue_id)
        if second != first + 1:
            raise ValueError("Only adjacent cues can be merged")
        left, right = document.cues[first], document.cues[second]
        merged = left.updated(
            end_ms=max(left.end_ms, right.end_ms),
            source_text=_join(left.source_text, right.source_text, self.separator),
            translated_text=_join(left.translated_text, right.translated_text, self.separator),
        )
        cues = list(document.cues)
        cues[first:second + 1] = [merged]
        return document.clone(cues=cues)


@dataclass(frozen=True)
class ShiftCues:
    offset_ms: int
    cue_ids: tuple[str, ...] = ()

    def apply(self, document: SubtitleDocument) -> SubtitleDocument:
        selected = set(self.cue_ids)
        if selected:
            missing = selected - {cue.cue_id for cue in document.cues}
            if missing:
                raise KeyError(next(iter(missing)))
        cues = [
            cue.updated(start_ms=cue.start_ms + self.offset_ms, end_ms=cue.end_ms + self.offset_ms)
            if not selected or cue.cue_id in selected else cue
            for cue in document.cues
        ]
        return document.clone(cues=cues)


@dataclass(frozen=True)
class FindReplace:
    find: str
    replace: str
    field: str = "both"
    case_sensitive: bool = True

    def apply(self, document: SubtitleDocument) -> SubtitleDocument:
        if not self.find:
            raise ValueError("Find text is required")
        if self.field not in {"source", "translated", "both"}:
            raise ValueError("Unsupported find/replace field")
        cues = []
        for cue in document.cues:
            changes = {}
            if self.field in {"source", "both"}:
                changes["source_text"] = _replace(cue.source_text, self.find, self.replace, self.case_sensitive)
            if self.field in {"translated", "both"}:
                changes["translated_text"] = _replace(cue.translated_text, self.find, self.replace, self.case_sensitive)
            cues.append(cue.updated(**changes))
        return document.clone(cues=cues)


class SubtitleEditor:
    def __init__(self, document: SubtitleDocument, maximum_history: int = 100) -> None:
        self.document = document.clone()
        self.maximum_history = max(1, int(maximum_history))
        self._undo: list[SubtitleDocument] = []
        self._redo: list[SubtitleDocument] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def execute(self, command: SubtitleCommand) -> SubtitleDocument:
        previous = self.document
        updated = command.apply(previous)
        self._undo.append(previous)
        if len(self._undo) > self.maximum_history:
            self._undo.pop(0)
        self._redo.clear()
        self.document = updated
        return updated

    def undo(self) -> SubtitleDocument:
        if not self._undo:
            return self.document
        self._redo.append(self.document)
        self.document = self._undo.pop()
        return self.document

    def redo(self) -> SubtitleDocument:
        if not self._redo:
            return self.document
        self._undo.append(self.document)
        self.document = self._redo.pop()
        return self.document


def _index(cues: list[SubtitleCue], cue_id: str) -> int:
    for index, cue in enumerate(cues):
        if cue.cue_id == cue_id:
            return index
    raise KeyError(cue_id)


def _join(left: str, right: str, separator: str) -> str:
    return separator.join(part for part in (left.strip(), right.strip()) if part)


def _replace(value: str, find: str, replacement: str, case_sensitive: bool) -> str:
    if case_sensitive:
        return value.replace(find, replacement)
    import re
    return re.sub(re.escape(find), lambda _match: replacement, value, flags=re.IGNORECASE)
