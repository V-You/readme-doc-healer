"""Vagueness detection -- rule-based heuristics for endpoint/parameter quality."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .spec_parser import Operation, Parameter


# placeholder patterns that indicate no real description was written
_PLACEHOLDER_PATTERNS = re.compile(
    r"^(?:todo|tbd|n/a|description|value|string|test|param|"
    r"the\s+\w+\.?|a\s+\w+\.?|an\s+\w+\.?)$",
    re.IGNORECASE,
)

# technical-only patterns -- descriptions that just echo the type
_TECHNICAL_ONLY = re.compile(
    r"^(?:string|integer|int|boolean|bool|number|float|double|"
    r"object|array|enum|uuid|id|date|datetime|timestamp|uri|url|"
    r"required|optional|default)\.?$",
    re.IGNORECASE,
)

_MIN_MEANINGFUL_LENGTH = 25


@dataclass
class VaguenessResult:
    """Result of a single vagueness check."""
    gap_type: str
    heuristic_reason: str
    needs_llm_review: bool


def check_endpoint_description(op: Operation) -> VaguenessResult | None:
    """Check if an endpoint's description is missing, vague, or lacks business context."""
    desc = op.description.strip()

    if not desc:
        return VaguenessResult(
            gap_type="missing_description",
            heuristic_reason="endpoint has no description",
            needs_llm_review=False,
        )

    if _PLACEHOLDER_PATTERNS.match(desc):
        return VaguenessResult(
            gap_type="vague_description",
            heuristic_reason=f"description matches placeholder pattern: '{desc}'",
            needs_llm_review=False,
        )

    if len(desc) < _MIN_MEANINGFUL_LENGTH:
        return VaguenessResult(
            gap_type="vague_description",
            heuristic_reason=f"description shorter than {_MIN_MEANINGFUL_LENGTH} chars ({len(desc)})",
            needs_llm_review=True,
        )

    if _TECHNICAL_ONLY.match(desc):
        return VaguenessResult(
            gap_type="no_business_context",
            heuristic_reason=f"description only contains technical type language: '{desc}'",
            needs_llm_review=False,
        )

    return None


def check_parameter_description(param: Parameter, op: Operation) -> VaguenessResult | None:
    """Check if a parameter's description is missing, vague, or lacks business context."""
    desc = param.description.strip()

    if not desc:
        return VaguenessResult(
            gap_type="missing_description",
            heuristic_reason=f"parameter '{param.name}' has no description",
            needs_llm_review=False,
        )

    if _PLACEHOLDER_PATTERNS.match(desc):
        return VaguenessResult(
            gap_type="vague_description",
            heuristic_reason=f"parameter '{param.name}' description matches placeholder: '{desc}'",
            needs_llm_review=False,
        )

    if len(desc) < _MIN_MEANINGFUL_LENGTH:
        # shorter threshold is ok for params, but still flag
        return VaguenessResult(
            gap_type="vague_description",
            heuristic_reason=f"parameter '{param.name}' description shorter than {_MIN_MEANINGFUL_LENGTH} chars ({len(desc)})",
            needs_llm_review=True,
        )

    if _TECHNICAL_ONLY.match(desc):
        return VaguenessResult(
            gap_type="no_business_context",
            heuristic_reason=f"parameter '{param.name}' only has technical type language: '{desc}'",
            needs_llm_review=False,
        )

    return None


def check_request_body_property(prop_name: str, prop_schema: dict, op: Operation) -> VaguenessResult | None:
    """Check if a request body property's description is missing or vague."""
    desc = prop_schema.get("description", "").strip()

    if not desc:
        return VaguenessResult(
            gap_type="missing_description",
            heuristic_reason=f"request body property '{prop_name}' has no description",
            needs_llm_review=False,
        )

    if _PLACEHOLDER_PATTERNS.match(desc):
        return VaguenessResult(
            gap_type="vague_description",
            heuristic_reason=f"request body property '{prop_name}' description matches placeholder: '{desc}'",
            needs_llm_review=False,
        )

    if len(desc) < _MIN_MEANINGFUL_LENGTH:
        return VaguenessResult(
            gap_type="vague_description",
            heuristic_reason=f"request body property '{prop_name}' description shorter than {_MIN_MEANINGFUL_LENGTH} chars ({len(desc)})",
            needs_llm_review=True,
        )

    return None
