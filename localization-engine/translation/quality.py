"""Target-language quality checks for subtitle translation output.

These checks are deliberately lightweight and dependency-free.  They are not
meant to grade translation accuracy; they catch high-confidence failures that
should not be burned into a dubbed video, such as Japanese source text leaking
into Simplified Chinese output or punctuation-only target captions being sent to
TTS.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional


_HIRAGANA_RE = re.compile(r"[\u3040-\u309f]")
_KATAKANA_RE = re.compile(r"[\u30a0-\u30ff\u31f0-\u31ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# Japanese grammatical fragments that often remain when a Japanese subtitle was
# copied or only half-translated into Chinese.  We only use this for zh targets.
_JA_FRAGMENT_RE = re.compile(
    r"(?:です|ます|でしょう|ました|ません|では|には|から|まで|として|について|"
    r"による|により|となって|となり|ています|しています|あります|おり|あす|"
    r"あさって|かけて|ミリ|台風)"
)

_ZH_BAD_FRAGMENT_RE = re.compile(
    # Common half-translated artifacts seen with Japanese weather/news captions,
    # e.g. "25及" from "250ミリ" and "有性" from "可能性".
    # The 及 pattern uses a negative lookahead to avoid matching legitimate
    # Chinese phrases like "25及以上" (25 and above).
    r"(?:\d+\s*及(?![\u4e00-\u9fff])|有性|可能\s*有性|毫米有性|新药剂量|公主成分)"
)

_PUNCT_ONLY_RE = re.compile(
    r"^[\s\u3000\.,!?;:'\"`~\-—–_()\[\]{}<>/\\|@#$%^&*+=，。！？；：、“”‘’（）【】《》…·￥]+$"
)

_ZH_TARGET_PREFIXES = ("zh", "cmn", "chinese", "simplified chinese", "简体", "中文")
_JA_TARGET_PREFIXES = ("ja", "jp", "japanese", "日语", "日文")
_KO_TARGET_PREFIXES = ("ko", "kor", "korean", "韩语", "韩文")
_EN_TARGET_PREFIXES = ("en", "eng", "english", "英语", "英文")


@dataclass(frozen=True)
class TranslationQualityIssue:
    """A high-confidence translation quality problem."""

    code: str
    message: str
    severity: str = "warning"
    context: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        data = {"code": self.code, "message": self.message, "severity": self.severity}
        if self.context:
            data["context"] = self.context
        return data


def normalize_language_code(language: Any) -> str:
    return str(language or "").strip().lower().replace("_", "-")


def is_zh_target(language: Any) -> bool:
    lang = normalize_language_code(language)
    return lang.startswith(_ZH_TARGET_PREFIXES)


def is_ja_target(language: Any) -> bool:
    lang = normalize_language_code(language)
    return lang.startswith(_JA_TARGET_PREFIXES)


def is_ko_target(language: Any) -> bool:
    lang = normalize_language_code(language)
    return lang.startswith(_KO_TARGET_PREFIXES)


def is_en_target(language: Any) -> bool:
    lang = normalize_language_code(language)
    return lang.startswith(_EN_TARGET_PREFIXES)


def _count(pattern: re.Pattern[str], text: str) -> int:
    return len(pattern.findall(text or ""))


def punctuation_only(text: Any) -> bool:
    value = str(text or "").strip()
    return bool(value) and bool(_PUNCT_ONLY_RE.fullmatch(value))


def contains_japanese_kana(text: Any) -> bool:
    value = str(text or "")
    return bool(_HIRAGANA_RE.search(value) or _KATAKANA_RE.search(value))


def contains_hangul(text: Any) -> bool:
    return bool(_HANGUL_RE.search(str(text or "")))


def _semantic_len(text: str) -> int:
    # Counts letters and CJK-ish characters, ignoring punctuation/space.
    return sum(1 for ch in text if ch.isalnum() or "\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff" or "\uac00" <= ch <= "\ud7af")


def zh_language_issues(text: Any, *, source_text: Any = "", index: Any = None) -> List[TranslationQualityIssue]:
    """Return high-confidence issues for Simplified Chinese target output."""
    value = str(text or "").strip()
    source = str(source_text or "").strip()
    issues: List[TranslationQualityIssue] = []
    context = {"index": index, "text": value[:120], "source": source[:120]}

    if not value:
        issues.append(TranslationQualityIssue("EMPTY_TRANSLATION", "translation is empty", "error", context))
        return issues

    if punctuation_only(value):
        issues.append(TranslationQualityIssue("PUNCTUATION_ONLY_TRANSLATION", "translation only contains punctuation", "error", context))
        return issues

    kana_count = _count(_HIRAGANA_RE, value) + _count(_KATAKANA_RE, value)
    semantic_len = max(1, _semantic_len(value))
    if kana_count >= 1 and (kana_count >= 2 or kana_count / semantic_len >= 0.03):
        issues.append(TranslationQualityIssue(
            "TARGET_LANGUAGE_LEAK_JA",
            "Japanese kana remains in Simplified Chinese translation",
            "error",
            {**context, "kana_count": kana_count, "semantic_len": semantic_len},
        ))

    # Catch Japanese written mostly with kanji plus grammar particles.
    if _JA_FRAGMENT_RE.search(value):
        issues.append(TranslationQualityIssue(
            "TARGET_LANGUAGE_LEAK_JA_FRAGMENT",
            "Japanese fragments remain in Simplified Chinese translation",
            "error",
            context,
        ))

    if contains_hangul(value):
        issues.append(TranslationQualityIssue(
            "TARGET_LANGUAGE_LEAK_KO",
            "Korean text remains in Simplified Chinese translation",
            "error",
            context,
        ))

    if source and value == source and (contains_japanese_kana(source) or contains_hangul(source)):
        issues.append(TranslationQualityIssue(
            "UNTRANSLATED_SOURCE_COPY",
            "translation is identical to non-Chinese source text",
            "error",
            context,
        ))

    if _ZH_BAD_FRAGMENT_RE.search(value):
        issues.append(TranslationQualityIssue(
            "SUSPICIOUS_TRANSLATION_ARTIFACT",
            "translation contains a known half-translated artifact",
            "error",
            context,
        ))

    return issues


def target_language_issues(text: Any, target_language: Any, *, source_text: Any = "", index: Any = None) -> List[TranslationQualityIssue]:
    """Return high-confidence target-language issues for one translated line."""
    lang = normalize_language_code(target_language)
    value = str(text or "").strip()
    context = {"index": index, "target_language": lang, "text": value[:120]}

    if is_zh_target(lang):
        return zh_language_issues(value, source_text=source_text, index=index)

    if not value:
        return [TranslationQualityIssue("EMPTY_TRANSLATION", "translation is empty", "error", context)]

    if punctuation_only(value):
        return [TranslationQualityIssue("PUNCTUATION_ONLY_TRANSLATION", "translation only contains punctuation", "error", context)]

    # Conservative checks for other common targets.
    if is_en_target(lang):
        cjk_count = _count(_CJK_RE, value)
        kana_count = _count(_HIRAGANA_RE, value) + _count(_KATAKANA_RE, value)
        hangul_count = _count(_HANGUL_RE, value)
        latin_count = _count(_LATIN_RE, value)
        if (cjk_count + kana_count + hangul_count) > max(2, latin_count):
            return [TranslationQualityIssue("TARGET_LANGUAGE_LEAK_NON_LATIN", "non-English text dominates English translation", "error", context)]
    elif is_ja_target(lang):
        if contains_hangul(value):
            return [TranslationQualityIssue("TARGET_LANGUAGE_LEAK_KO", "Korean text remains in Japanese translation", "error", context)]
    elif is_ko_target(lang):
        if contains_japanese_kana(value):
            return [TranslationQualityIssue("TARGET_LANGUAGE_LEAK_JA", "Japanese text remains in Korean translation", "error", context)]

    return []


def has_blocking_issues(issues: Iterable[TranslationQualityIssue]) -> bool:
    return any(issue.severity == "error" for issue in issues)


def validate_translation_items(
    items: Iterable[Mapping[str, Any]],
    source_by_id: Mapping[int, str],
    target_language: Any,
) -> dict[int, list[TranslationQualityIssue]]:
    """Validate raw provider items keyed by segment id."""
    result: dict[int, list[TranslationQualityIssue]] = {}
    for item in items:
        try:
            item_id = int(item.get("id"))
        except Exception:
            continue
        text = str(item.get("text", "") or "")
        issues = target_language_issues(
            text,
            target_language,
            source_text=source_by_id.get(item_id, ""),
            index=item_id,
        )
        if issues:
            result[item_id] = issues
    return result
