"""Redaction -- strips sensitive values from text before returning to host LLM."""

from __future__ import annotations

import re

from .config import Settings


_REPLACEMENT = "***REDACTED***"


def redact_text(text: str, settings: Settings) -> tuple[str, bool]:
    """Apply redaction patterns to text. Returns (cleaned_text, was_redacted)."""
    if not text:
        return text, False

    allowlist = settings.redact_allow_list
    patterns = settings.redact_pattern_list
    redacted = False

    for pattern in patterns:
        for match in pattern.finditer(text):
            value = match.group()
            # skip if allowlisted
            if any(a.search(value) for a in allowlist):
                continue
            text = text.replace(value, _REPLACEMENT, 1)
            redacted = True

    return text, redacted


def redact_dict(d: dict, settings: Settings, fields: tuple[str, ...] = ("doc_snippet", "spec_value")) -> dict:
    """Redact string values in specific dict fields. Returns a copy."""
    result = dict(d)
    any_redacted = False
    for field in fields:
        if field in result and isinstance(result[field], str):
            result[field], was_redacted = redact_text(result[field], settings)
            if was_redacted:
                any_redacted = True
    if any_redacted:
        result["redacted"] = True
    return result
