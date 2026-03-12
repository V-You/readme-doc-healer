"""Configuration -- loads .env and provides typed settings."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Optional

from pydantic_settings import BaseSettings


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration loaded from .env with tool-arg overrides."""

    project_name: Optional[str] = None
    project_dir: Optional[str] = None
    heal_mode: str = "sectioned"
    readme_api_key: Optional[str] = None
    readme_branch: str = "stable"
    spec_path: Optional[str] = None
    docs_path: Optional[str] = None
    glossary_path: Optional[str] = None
    audit_fixture_path: Optional[str] = None
    recipes_path: Optional[str] = None
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

    @property
    def base_data_dir(self) -> Path:
        """Base directory for bundled and project-scoped demo data."""
        return _PROJECT_ROOT / "base_data"

    @property
    def data_dir_name(self) -> str | None:
        """Folder name under base_data for the active local project."""
        return self.project_dir or self.project_name

    @property
    def project_data_dir(self) -> Path | None:
        """Project-specific data directory under base_data, if configured."""
        if not self.data_dir_name:
            return None
        return self.base_data_dir / self.data_dir_name

    @property
    def data_search_roots(self) -> list[Path]:
        """Search project data first, then fall back to the legacy flat layout."""
        roots: list[Path] = []
        if self.project_data_dir and self.project_data_dir.exists():
            roots.append(self.project_data_dir)
        roots.append(self.base_data_dir)
        return roots

    @property
    def resolved_spec_path(self) -> str | None:
        """Resolved spec path from explicit config or the active project folder."""
        if self.spec_path:
            return self.spec_path
        return _find_spec_path(self.data_search_roots)

    @property
    def resolved_docs_path(self) -> str | None:
        """Resolved legacy docs directory from explicit config or the active project folder."""
        if self.docs_path:
            return self.docs_path
        return _find_docs_path(self.data_search_roots)

    @property
    def resolved_glossary_path(self) -> str | None:
        """Resolved glossary path from explicit config or the active project folder."""
        if self.glossary_path:
            return self.glossary_path
        return _find_named_file(self.data_search_roots, "glossary.json") or _default_named_file(
            self.data_search_roots,
            "glossary.json",
        )

    @property
    def resolved_audit_fixture_path(self) -> str | None:
        """Resolved offline audit fixture path from explicit config or the active project folder."""
        if self.audit_fixture_path:
            return self.audit_fixture_path
        return _find_named_file(self.data_search_roots, "audit-fixture.json") or _default_named_file(
            self.data_search_roots,
            "audit-fixture.json",
        )

    @property
    def resolved_recipes_path(self) -> str | None:
        """Resolved recipes path from explicit config or the active project folder."""
        if self.recipes_path:
            return self.recipes_path
        return _find_named_file(self.data_search_roots, "settings_recipes.json")


# built-in redaction patterns -- api keys, tokens, emails, secrets
_DEFAULT_REDACT_PATTERNS = [
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b", re.IGNORECASE),          # base64 blobs
    re.compile(r"\b(?:sk|pk|api[_-]?key)[_-]?\w{16,}\b", re.IGNORECASE), # api keys
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),  # emails
    re.compile(r"\b(?:password|pwd|secret|token)\s*[:=]\s*\S+", re.IGNORECASE),       # key=value secrets
]

_SPEC_PATTERNS = (
    "*.best.openapi.yaml",
    "*.best.openapi.yml",
    "*.best.openapi.json",
    "*.openapi.yaml",
    "*.openapi.yml",
    "*.openapi.json",
)


def _find_spec_path(roots: Iterable[Path]) -> str | None:
    """Find the first likely OpenAPI file in the active project data directories."""
    for root in roots:
        for pattern in _SPEC_PATTERNS:
            candidates = sorted(root.glob(pattern))
            if candidates:
                return str(candidates[0])
    return None


def _find_docs_path(roots: Iterable[Path]) -> str | None:
    """Find the legacy documentation directory in the active project data directories."""
    for root in roots:
        candidate = root / "Legacy-Documentation"
        if candidate.is_dir():
            return str(candidate)
    return None


def _find_named_file(roots: Iterable[Path], filename: str) -> str | None:
    """Find a named file in the active project data directories."""
    for root in roots:
        candidate = root / filename
        if candidate.is_file():
            return str(candidate)
    return None


def _default_named_file(roots: Iterable[Path], filename: str) -> str | None:
    """Return the first default location for a named file, even if it does not exist."""
    for root in roots:
        return str(root / filename)
    return None


def get_settings(**overrides: Any) -> Settings:
    """Create settings, applying any tool-arg overrides on top of .env."""
    return Settings(**{k: v for k, v in overrides.items() if v is not None})
