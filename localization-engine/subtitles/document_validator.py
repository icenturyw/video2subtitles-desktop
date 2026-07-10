"""Actionable validation for editable subtitle documents."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .document import SubtitleDocument


@dataclass(frozen=True)
class SubtitleValidationIssue:
    cue_id: str
    severity: str
    code: str
    message: str
    suggestion: str

    def to_dict(self) -> dict:
        return asdict(self)


class SubtitleValidator:
    def __init__(
        self,
        *,
        minimum_duration_ms: int = 300,
        maximum_duration_ms: int = 10000,
        maximum_characters: int = 84,
        maximum_characters_per_second: float = 25.0,
    ) -> None:
        self.minimum_duration_ms = minimum_duration_ms
        self.maximum_duration_ms = maximum_duration_ms
        self.maximum_characters = maximum_characters
        self.maximum_characters_per_second = maximum_characters_per_second

    def validate(self, document: SubtitleDocument) -> list[SubtitleValidationIssue]:
        issues: list[SubtitleValidationIssue] = []
        seen: set[str] = set()
        previous = None
        translated_expected = bool(
            document.target_language
            and document.target_language.lower() != document.source_language.lower()
        )
        for cue in document.cues:
            if cue.cue_id in seen:
                issues.append(self._issue(cue.cue_id, "error", "CUE_ID_DUPLICATE", "Cue ID is duplicated", "Assign a new stable cue ID"))
            seen.add(cue.cue_id)
            if cue.start_ms < 0 or cue.end_ms < 0:
                issues.append(self._issue(cue.cue_id, "error", "SUBTITLE_NEGATIVE_TIME", "Cue contains a negative timestamp", "Move the cue to zero or later"))
            if cue.end_ms <= cue.start_ms:
                issues.append(self._issue(cue.cue_id, "error", "SUBTITLE_END_BEFORE_START", "Cue end must be later than its start", "Adjust the cue boundaries"))
            text = max(
                (cue.source_text.strip(), cue.translated_text.strip()),
                key=len,
            )
            if not cue.source_text.strip() and not cue.translated_text.strip():
                issues.append(self._issue(cue.cue_id, "error", "SUBTITLE_EMPTY", "Cue has no text", "Enter text or delete the cue"))
            duration = cue.end_ms - cue.start_ms
            if duration > 0 and duration < self.minimum_duration_ms:
                issues.append(self._issue(cue.cue_id, "warning", "SUBTITLE_DURATION_TOO_SHORT", "Cue display time is very short", "Lengthen the cue or shorten its text"))
            if duration > self.maximum_duration_ms:
                issues.append(self._issue(cue.cue_id, "warning", "SUBTITLE_DURATION_TOO_LONG", "Cue display time is very long", "Split the cue"))
            if len(text) > self.maximum_characters:
                issues.append(self._issue(cue.cue_id, "warning", "SUBTITLE_TOO_MANY_CHARACTERS", "Cue contains too many characters", "Split or shorten the cue"))
            if duration > 0 and len(text) / (duration / 1000.0) > self.maximum_characters_per_second:
                issues.append(self._issue(cue.cue_id, "warning", "SUBTITLE_READING_SPEED_HIGH", "Cue reading speed is too high", "Lengthen or split the cue"))
            if previous is not None:
                if cue.start_ms < previous.start_ms:
                    issues.append(self._issue(cue.cue_id, "error", "SUBTITLE_TIME_ORDER", "Cue starts before the preceding cue", "Sort or retime the cues"))
                if cue.start_ms < previous.end_ms:
                    issues.append(self._issue(cue.cue_id, "warning", "SUBTITLE_OVERLAP", "Cue overlaps the preceding cue", "Move or trim one of the cues"))
            if translated_expected and cue.source_text.strip():
                source = _normalized(cue.source_text)
                translated = _normalized(cue.translated_text)
                if not translated or translated == source:
                    issues.append(self._issue(cue.cue_id, "warning", "SUBTITLE_SUSPECT_UNTRANSLATED", "Translation appears to be missing or unchanged", "Review and translate this cue"))
            previous = cue
        return issues

    @staticmethod
    def _issue(cue_id: str, severity: str, code: str, message: str, suggestion: str) -> SubtitleValidationIssue:
        return SubtitleValidationIssue(cue_id, severity, code, message, suggestion)


def _normalized(value: str) -> str:
    return "".join(str(value or "").casefold().split())
