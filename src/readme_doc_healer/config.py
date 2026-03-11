"""Configuration -- loads .env and provides typed settings."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration loaded from .env with tool-arg overrides."""

    heal_mode: str = "sectioned"
    readme_api_key: Optional[str] = None
    readme_branch: str = "stable"
    spec_path: Optional[str] = None
    docs_path: Optional[str] = None
    glossary_path: str = str(_PROJECT_ROOT / "base_data" / "glossary.json")
    redact_patterns: str = ""
    redact_allowlist: str = ""
    allow_category_create: bool = False

    model_config = {
        "env_file": str(_PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def redact_pattern_list(self) -> list[re.Pattern[str]]:
        """Compile comma-separated regex patterns from config."""
        if not self.redact_patterns:
            return _DEFAULT_REDACT_PATTERNS
        return [re.compile(p.strip(), re.IGNORECASE) for p in self.redact_patterns.split(",") if p.strip()]

    @property
    def redact_allow_list(self) -> list[re.Pattern[str]]:
        if not self.redact_allowlist:
            return []
        return [re.compile(p.strip(), re.IGNORECASE) for p in self.redact_allowlist.split(",") if p.strip()]


# built-in redaction patterns -- api keys, tokens, emails, secrets
_DEFAULT_REDACT_PATTERNS = [
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b", re.IGNORECASE),          # base64 blobs
    re.compile(r"\b(?:sk|pk|api[_-]?key)[_-]?\w{16,}\b", re.IGNORECASE), # api keys
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),  # emails
    re.compile(r"\b(?:password|pwd|secret|token)\s*[:=]\s*\S+", re.IGNORECASE),       # key=value secrets
]


def get_settings(**overrides: str) -> Settings:
    """Create settings, applying any tool-arg overrides on top of .env."""
    return Settings(**{k: v for k, v in overrides.items() if v is not None})
