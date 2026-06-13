"""Translation prompt templates for LLM-based translators."""
from __future__ import annotations

from typing import Optional


SYSTEM_PROMPT = """You are a professional subtitle translator. Translate the given subtitle segments from {source_lang} to {target_lang}.

Requirements:
1. Return ONLY a valid JSON array, no markdown, no explanation.
2. Each element must have "id" (integer) and "text" (string).
3. Preserve the original meaning while making it natural in {target_lang}.
4. Keep line breaks (\\n) if present.
5. Do not add, remove, or reorder segments.
6. Adapt idioms and cultural references appropriately for {target_lang} audience.
7. For Chinese output, use Simplified Chinese characters.
8. Keep character names and proper nouns in their original form unless a well-known translation exists."""

TRANSLATE_PROMPT = """Translate the following subtitle segments from {source_lang} to {target_lang}.

Return a JSON array with exactly {count} elements, each with "id" and "text":

{segments_json}

Glossary terms to apply (use these translations for matching terms):
{glossary_text}"""

REFLECT_PROMPT = """Review the following subtitle translation. Check for:
1. Accuracy: Does it preserve the original meaning?
2. Naturalness: Does it sound natural in {target_lang}?
3. Consistency: Are terms translated consistently?

Original ({source_lang}): {source_text}
Current translation ({target_lang}): {translated_text}

If the translation is correct and natural, return the SAME text unchanged.
If improvements are needed, provide an improved version.
Return a JSON array with "id" and "text" elements."""

ADAPT_PROMPT = """Adapt the following subtitle translations to better fit the constraints of subtitles:
- Keep each line short enough to read comfortably (max ~20 characters for Chinese, ~40 for English)
- Preserve meaning while being concise
- Ensure reading speed allows viewers to follow along
- If text is already appropriate, return it unchanged

Original text: {source_text}
Current translation: {translated_text}

Return a JSON array with "id" and "text" elements."""


def build_translate_prompt(segments_json: str, source_lang: str, target_lang: str,
                           count: int, glossary_text: str = "") -> str:
    """Build the translate prompt with dynamic parameters."""
    return TRANSLATE_PROMPT.format(
        segments_json=segments_json,
        source_lang=source_lang,
        target_lang=target_lang,
        count=count,
        glossary_text=glossary_text or "(none)",
    )


def build_system_prompt(source_lang: str, target_lang: str) -> str:
    return SYSTEM_PROMPT.format(source_lang=source_lang, target_lang=target_lang)
