"""Parse and validate translation API responses."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


def parse_translation_response(response_text: str,
                                expected_ids: List[int]) -> Tuple[List[Dict], List[str]]:
    """Parse a translation API response and validate against expected IDs.

    Args:
        response_text: Raw text from the API response.
        expected_ids: List of segment IDs expected in order.

    Returns:
        Tuple of (parsed_translations, errors) where parsed_translations is
        a list of {"id": int, "text": str} and errors is a list of error messages.
    """
    errors: List[str] = []
    cleaned = _clean_response(response_text)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to extract JSON array from markdown code block
        extracted = _extract_json_array(response_text)
        if extracted is not None:
            try:
                data = json.loads(extracted)
            except json.JSONDecodeError as e:
                return [], [f"Failed to parse JSON after extraction: {e}"]
        else:
            return [], [f"Invalid JSON response: {cleaned[:200]}"]

    if not isinstance(data, list):
        return [], [f"Expected JSON array, got {type(data).__name__}"]

    # Validate structure
    translations: List[Dict] = []
    for item in data:
        if not isinstance(item, dict):
            errors.append(f"Item is not a dict: {item}")
            continue
        item_id = item.get("id")
        item_text = item.get("text", "")
        if item_id is None:
            errors.append(f"Item missing 'id': {item}")
            continue
        try:
            item_id = int(item_id)
        except (ValueError, TypeError):
            errors.append(f"Item 'id' not an integer: {item_id}")
            continue
        translations.append({"id": item_id, "text": str(item_text)})

    if not translations:
        return [], ["No valid translations found in response"]

    # Check for missing/extra IDs
    found_ids = {t["id"] for t in translations}
    expected_set = set(expected_ids)
    missing = expected_set - found_ids
    extra = found_ids - expected_set
    if missing:
        errors.append(f"Missing translations for IDs: {sorted(missing)}")
    if extra:
        errors.append(f"Unexpected IDs not in request: {sorted(extra)}")

    # Check for empty translations
    empty = [t["id"] for t in translations if not t["text"].strip()]
    if empty:
        errors.append(f"Empty translations for IDs: {empty}")

    return translations, errors


def _clean_response(text: str) -> str:
    """Remove markdown code fences and leading/trailing whitespace."""
    cleaned = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned)
    return cleaned.strip()


def _extract_json_array(text: str) -> Optional[str]:
    """Try to extract a JSON array from text using regex."""
    match = re.search(r'(\[\s*\{.*\}\s*\])', text, re.DOTALL)
    return match.group(1) if match else None


def check_response_safety(response_text: str) -> List[str]:
    """Check response for sensitive data that should not be logged.

    Returns:
        List of safety warnings (empty if safe).
    """
    warnings: List[str] = []
    lower = response_text.lower()
    _SENSITIVE_PATTERNS = [
        "sk-", "api_key", "apikey", "secret", "token",
        "authorization", "bearer ", "x-api-key",
    ]
    for pattern in _SENSITIVE_PATTERNS:
        if pattern in lower:
            warnings.append(f"Response may contain sensitive data (pattern: {pattern})")
            break
    return warnings


def sanitize_for_log(response_text: str, max_length: int = 500) -> str:
    """Sanitize response text for logging (truncate and remove sensitive data)."""
    sanitized = re.sub(
        r'(sk-[A-Za-z0-9]{10,})|(api[_-]?key["\']?\s*:\s*["\'][^"\']+["\'])',
        '[REDACTED]',
        response_text,
        flags=re.IGNORECASE,
    )
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + "..."
    return sanitized
