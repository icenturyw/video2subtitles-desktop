from __future__ import annotations

from typing import Dict, Optional

# Map ISO 639-1 codes to Qwen3-TTS full language names
ISO_TO_QWEN = {
    "zh": "chinese",
    "zh-cn": "chinese",
    "zh-tw": "chinese",
    "en": "english",
    "ja": "japanese",
    "ko": "korean",
    "de": "german",
    "fr": "french",
    "ru": "russian",
    "pt": "portuguese",
    "es": "spanish",
    "it": "italian",
}

# Reverse map for display
QWEN_LANGUAGES = sorted(set(ISO_TO_QWEN.values()))


def normalize_language(language: Optional[str]) -> Optional[str]:
    if not language:
        return None
    lower = language.lower().strip()
    if lower in ISO_TO_QWEN:
        return ISO_TO_QWEN[lower]
    if lower in QWEN_LANGUAGES:
        return lower
    if lower == "auto":
        return None
    return lower
