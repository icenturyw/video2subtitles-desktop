"""Structural and completeness validation for provider translations."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Mapping, Optional, Sequence


_SEMANTIC_RE = re.compile(r"[^\w\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+", re.UNICODE)


@dataclass(frozen=True)
class TranslationValidationIssue:
    code: str
    message: str
    segment_id: Optional[int] = None
    context: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.segment_id is not None:
            result["segment_id"] = self.segment_id
        if self.context:
            result["context"] = dict(self.context)
        return result


@dataclass(frozen=True)
class TranslationValidationResult:
    issues: Sequence[TranslationValidationIssue]

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def valid(self) -> bool:
        """Convenience alias for callers that prefer a short predicate."""
        return self.is_valid

    @property
    def issue_codes(self) -> set[str]:
        return {issue.code for issue in self.issues}

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.is_valid,
            "issue_count": len(self.issues),
            "issues": [issue.to_dict() for issue in self.issues],
        }


class TranslationValidator:
    """Validate translation count, IDs, non-empty text and source leakage."""

    STRUCTURAL_CODES = frozenset({
        "SEGMENT_COUNT_MISMATCH",
        "TRANSLATION_ID_INVALID",
        "TRANSLATION_ID_DUPLICATE",
        "TRANSLATION_ID_SEQUENCE_INVALID",
    })

    @staticmethod
    def _id(item: Any) -> Optional[int]:
        value = None
        if isinstance(item, Mapping):
            value = item.get("id", item.get("index"))
        else:
            value = getattr(item, "id", getattr(item, "index", None))
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _text(item: Any, *, translated: bool) -> str:
        if isinstance(item, Mapping):
            keys = (
                ("translation", "translated_text", "target_text", "text")
                if translated
                else ("text", "source_text")
            )
            for key in keys:
                if key in item:
                    return str(item.get(key) or "")
            return ""
        if translated:
            for key in ("translation", "translated_text", "target_text", "text"):
                if hasattr(item, key):
                    value = getattr(item, key)
                    if value is not None:
                        return str(value)
        else:
            for key in ("text", "source_text"):
                if hasattr(item, key):
                    return str(getattr(item, key) or "")
        return ""

    @staticmethod
    def _semantic(text: str) -> str:
        return _SEMANTIC_RE.sub("", text.casefold())

    def validate(
        self,
        source_segments: Iterable[Any],
        translated_segments: Iterable[Any],
        target_language: str = "",
    ) -> TranslationValidationResult:
        sources = list(source_segments)
        translations = list(translated_segments)
        issues: List[TranslationValidationIssue] = []

        if len(sources) != len(translations):
            issues.append(TranslationValidationIssue(
                "SEGMENT_COUNT_MISMATCH",
                f"Expected {len(sources)} translated segments, got {len(translations)}",
                context={"expected": len(sources), "actual": len(translations)},
            ))

        source_ids = [self._id(item) for item in sources]
        translation_ids = [self._id(item) for item in translations]
        invalid_positions = [i for i, item_id in enumerate(translation_ids) if item_id is None]
        for position in invalid_positions:
            issues.append(TranslationValidationIssue(
                "TRANSLATION_ID_INVALID",
                f"Translation at position {position} has no valid integer ID",
                context={"position": position},
            ))

        valid_ids = [item_id for item_id in translation_ids if item_id is not None]
        duplicate_ids = sorted({item_id for item_id in valid_ids if valid_ids.count(item_id) > 1})
        for item_id in duplicate_ids:
            issues.append(TranslationValidationIssue(
                "TRANSLATION_ID_DUPLICATE",
                f"Translation ID {item_id} occurs more than once",
                segment_id=item_id,
            ))

        if source_ids != translation_ids:
            issues.append(TranslationValidationIssue(
                "TRANSLATION_ID_SEQUENCE_INVALID",
                "Translation IDs do not match the source segment sequence",
                context={"expected": source_ids, "actual": translation_ids},
            ))

        source_by_id = {
            item_id: self._text(item, translated=False)
            for item_id, item in zip(source_ids, sources)
            if item_id is not None
        }
        for position, (item_id, item) in enumerate(zip(translation_ids, translations)):
            translated_text = self._text(item, translated=True).strip()
            if not translated_text:
                issues.append(TranslationValidationIssue(
                    "EMPTY_TRANSLATION",
                    f"Translation for segment {item_id if item_id is not None else position} is empty",
                    segment_id=item_id,
                    context={"position": position},
                ))
                continue

            source_text = source_by_id.get(item_id, "").strip()
            source_semantic = self._semantic(source_text)
            translated_semantic = self._semantic(translated_text)
            if source_semantic and source_semantic == translated_semantic:
                issues.append(TranslationValidationIssue(
                    "SOURCE_TEXT_REMAINS",
                    f"Translation for segment {item_id} is unchanged from the source",
                    segment_id=item_id,
                    context={
                        "source": source_text[:120],
                        "translation": translated_text[:120],
                        "target_language": str(target_language or ""),
                    },
                ))

        return TranslationValidationResult(tuple(issues))

