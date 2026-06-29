"""Translation prompt templates for LLM-based translators."""
from __future__ import annotations

from typing import Optional


SYSTEM_PROMPT = """You are a professional subtitle translator. Translate the given subtitle segments from {source_lang} to {target_lang}.

Requirements:
1. Return ONLY a valid JSON array, no markdown, no explanation.
2. Each element must have "id" (integer) and "text" (string) in {target_lang}.
3. Preserve meaning, line breaks (\\n), and segment count — do not add, remove, or reorder.
4. For Chinese output, use Simplified Chinese characters only. Do NOT leave Japanese kana (hiragana/katakana), Korean Hangul, or source-language grammar in the result.
5. Translate weather/news terms (rainfall, typhoon, prefecture/city names, warnings, measurements) naturally in {target_lang}; keep proper nouns in original form only when natural.
6. Copy every "id" exactly as an integer.
7. Skip punctuation-only subtitles — attach to previous meaningful line if possible, otherwise output empty string.
8. When translating Japanese to Chinese: translate terms like ミリ→毫米, 可能性→可能性, and do NOT output half-translated fragments such as "及" or "有性" as standalone text."""

TRANSLATE_PROMPT = """Translate the following subtitle segments from {source_lang} to {target_lang}.

Return a JSON array with exactly {count} elements, each with "id" and "text". Every "text" must be in {target_lang}, not a copy of the source language:

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


SYSTEM_PROMPT_COMPACT = """You are a professional subtitle translator. Translate the given subtitle segments from {source_lang} to {target_lang}.

Input: TSV lines (id[TAB]source_text), one per segment.
Output: One translated line per segment, in exact input order, NO ids, NO JSON, NO markdown.

Requirements:
- Preserve meaning and line breaks (\\n); do not add/remove/reorder lines.
- For Chinese output, use Simplified Chinese only — NO Japanese kana, NO Korean Hangul, NO source-language grammar.
- Translate weather/news terms (rainfall, typhoon, measurements) naturally in {target_lang}.
- Skip punctuation-only lines (output empty line as placeholder).
- When translating Japanese to Chinese: fully translate terms like ミリ→毫米, 可能性→可能性; do NOT output half-translated fragments such as "及" or "有性"."""

TRANSLATE_PROMPT_COMPACT = """Translate {count} subtitle segments from {source_lang} to {target_lang}.

Output exactly {count} lines — one translation per line, same order as input. NO JSON, NO markdown, NO ids. Every line must be in {target_lang}, not a copy of the source language:

{segments_tsv}

Glossary terms to apply (use these translations for matching terms):
{glossary_text}"""


def build_translate_prompt_compact(segments_tsv: str, source_lang: str, target_lang: str,
                                   count: int, glossary_text: str = "") -> str:
    return TRANSLATE_PROMPT_COMPACT.format(
        segments_tsv=segments_tsv,
        source_lang=source_lang,
        target_lang=target_lang,
        count=count,
        glossary_text=glossary_text or "(none)",
    )


def build_system_prompt_compact(source_lang: str, target_lang: str) -> str:
    return SYSTEM_PROMPT_COMPACT.format(source_lang=source_lang, target_lang=target_lang)
