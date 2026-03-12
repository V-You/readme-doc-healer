"""Config profile helpers -- load RiRo lookup data and derive config quality signals."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .gap_report import ConfigQualitySummary
from .spec_parser import Operation


_LOOKUP_FILENAME = "riro_consolidated_lookup.json"
_CONFIG_DOC_GLOB = "Keys-for-configuring-RiRo-settings_*.html"
_VERBOSE_DEFAULT_RE = re.compile(r"\bdefaults?\s+to\b", re.IGNORECASE)
_SAMPLE_LIMIT = 5


@dataclass
class ConfigLookupEntry:
    key: str
    id: int = 0
    value_type: str = ""
    path: str = ""
    default: str = ""
    comment: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ConfigLookupEntry":
        return cls(
            key=str(raw.get("key", "")).strip(),
            id=int(raw.get("id", 0)),
            value_type=str(raw.get("type", "")).strip(),
            path=str(raw.get("path", "")).strip(),
            default=str(raw.get("default", "")).strip(),
            comment=str(raw.get("comment", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for outward payloads – excludes id (internal join key only)."""
        d = asdict(self)
        d.pop("id", None)
        return d


@dataclass
class ConfigProfile:
    summary: ConfigQualitySummary = field(default_factory=ConfigQualitySummary)
    entries: list[ConfigLookupEntry] = field(default_factory=list)

    def to_heal_context(self, sample_limit: int = 10) -> dict[str, Any]:
        if not self.summary.enabled:
            return {}

        missing_default_examples = [
            entry.to_dict()
            for entry in self.entries
            if not entry.default
        ][:sample_limit]

        brittle_ui_path_examples = [
            entry.to_dict()
            for entry in self.entries
            if entry.path
        ][:sample_limit]

        sample_entries = [entry.to_dict() for entry in self.entries[:sample_limit]]

        return {
            "summary": asdict(self.summary),
            "sample_entries": sample_entries,
            "missing_default_examples": missing_default_examples,
            "brittle_ui_path_examples": brittle_ui_path_examples,
        }


def is_config_operation(operation: Operation) -> bool:
    """Return true for RiRo-style configuration endpoints."""
    path = operation.path.lower()
    tags = {tag.lower() for tag in operation.tags}
    text = " ".join(
        part for part in (operation.summary, operation.description, operation.operation_id or "") if part
    ).lower()
    return "/setting" in path or "settings operations" in tags or "riro" in text


def load_config_profile(docs_path: str | Path) -> ConfigProfile:
    """Load the RiRo config lookup and derive config-quality metrics."""
    docs_dir = Path(docs_path)
    data_root = docs_dir.parent if docs_dir.name == "Legacy-Documentation" else docs_dir
    lookup_path = data_root / _LOOKUP_FILENAME
    config_doc_path = _find_config_doc(docs_dir if docs_dir.is_dir() else data_root / "Legacy-Documentation")

    if not lookup_path.is_file():
        return ConfigProfile()

    try:
        raw = json.loads(lookup_path.read_text(encoding="utf-8"))
    except Exception:
        return ConfigProfile()

    entries = [
        ConfigLookupEntry.from_dict(entry)
        for entry in raw.get("entries", [])
        if str(entry.get("key", "")).strip()
    ]

    missing_default_entries = [entry for entry in entries if not entry.default]
    brittle_ui_path_entries = [entry for entry in entries if entry.path]
    verbose_default_count, verbose_samples = _scan_verbose_default_phrases(config_doc_path)

    summary = ConfigQualitySummary(
        enabled=True,
        lookup_path=str(lookup_path),
        config_doc_source=config_doc_path.name if config_doc_path else "",
        lookup_entry_count=len(entries),
        with_defaults=len(entries) - len(missing_default_entries),
        missing_default=len(missing_default_entries),
        brittle_ui_path=len(brittle_ui_path_entries),
        verbose_default_phrase=verbose_default_count,
        by_type={
            "missing_default": len(missing_default_entries),
            "brittle_ui_path": len(brittle_ui_path_entries),
            "verbose_default_phrase": verbose_default_count,
        },
        sample_missing_default_keys=[entry.key for entry in missing_default_entries[:_SAMPLE_LIMIT]],
        sample_brittle_ui_paths=[
            f"{entry.key} -> {entry.path}"
            for entry in brittle_ui_path_entries[:_SAMPLE_LIMIT]
        ],
        sample_verbose_default_phrases=verbose_samples,
    )
    return ConfigProfile(summary=summary, entries=entries)


def build_config_gap_specs(profile: ConfigProfile) -> list[dict[str, Any]]:
    """Build aggregated config-specific gap specs from the lookup profile."""
    if not profile.summary.enabled:
        return []

    summary = profile.summary
    lookup_name = Path(summary.lookup_path).name if summary.lookup_path else _LOOKUP_FILENAME
    gap_specs: list[dict[str, Any]] = []

    if summary.missing_default:
        gap_specs.append({
            "gap_type": "missing_default",
            "severity": "warning",
            "message": (
                f"{summary.missing_default} config keys in '{lookup_name}' have no documented default value"
            ),
            "heuristic_reason": "config lookup entry has an empty default field",
            "doc_source": lookup_name,
            "doc_snippet": "; ".join(summary.sample_missing_default_keys),
            "spec_value": {
                "lookup_entry_count": summary.lookup_entry_count,
                "with_defaults": summary.with_defaults,
            },
        })

    if summary.brittle_ui_path:
        gap_specs.append({
            "gap_type": "brittle_ui_path",
            "severity": "info",
            "message": (
                f"{summary.brittle_ui_path} config keys rely on BIP RiRo UI breadcrumb paths that are useful but brittle"
            ),
            "heuristic_reason": "config lookup entry contains a UI breadcrumb path",
            "doc_source": lookup_name,
            "doc_snippet": "; ".join(summary.sample_brittle_ui_paths),
            "spec_value": {
                "lookup_entry_count": summary.lookup_entry_count,
                "ui_path_entries": summary.brittle_ui_path,
            },
        })

    if summary.verbose_default_phrase:
        gap_specs.append({
            "gap_type": "verbose_default_phrase",
            "severity": "info",
            "message": (
                f"{summary.verbose_default_phrase} legacy config rows use the phrase 'defaults to' instead of a structured default value"
            ),
            "heuristic_reason": "legacy config page contains verbose default phrasing",
            "doc_source": summary.config_doc_source,
            "doc_snippet": "; ".join(summary.sample_verbose_default_phrases),
            "spec_value": {
                "verbose_default_phrase": summary.verbose_default_phrase,
            },
        })

    return gap_specs


def _find_config_doc(docs_dir: Path) -> Path | None:
    if not docs_dir.is_dir():
        return None
    matches = sorted(docs_dir.glob(_CONFIG_DOC_GLOB))
    return matches[0] if matches else None


def _scan_verbose_default_phrases(config_doc_path: Path | None) -> tuple[int, list[str]]:
    if config_doc_path is None or not config_doc_path.is_file():
        return 0, []

    try:
        html = config_doc_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0, []

    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    count = len(_VERBOSE_DEFAULT_RE.findall(text))

    samples: list[str] = []
    for match in _VERBOSE_DEFAULT_RE.finditer(text):
        start = max(0, match.start() - 12)
        end = min(len(text), match.end() + 40)
        snippet = text[start:end].strip()
        if snippet and snippet not in samples:
            samples.append(snippet)
        if len(samples) >= _SAMPLE_LIMIT:
            break

    return count, samples