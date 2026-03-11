"""Glossary loader -- reads glossary.json and builds alias lookup tables."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GlossaryEntry:
    term: str
    aliases: list[str] = field(default_factory=list)
    definition: str = ""
    context: str = ""
    pattern: str | None = None


@dataclass
class Glossary:
    """Loaded glossary with bidirectional alias lookups."""

    entries: list[GlossaryEntry]
    # maps any alias (lowered) -> canonical term
    _alias_to_term: dict[str, str] = field(default_factory=dict, repr=False)
    # maps canonical term (lowered) -> all aliases (original case)
    _term_to_aliases: dict[str, list[str]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for entry in self.entries:
            term_lower = entry.term.lower()
            self._term_to_aliases[term_lower] = [entry.term] + entry.aliases
            self._alias_to_term[term_lower] = entry.term
            for alias in entry.aliases:
                self._alias_to_term[alias.lower()] = entry.term

    def resolve(self, text: str) -> str | None:
        """Resolve a term or alias to its canonical form. Returns None if not found."""
        return self._alias_to_term.get(text.lower())

    def all_names_for(self, term: str) -> list[str]:
        """Return all known names (term + aliases) for a canonical term."""
        return self._term_to_aliases.get(term.lower(), [])

    def expand_text(self, text: str) -> set[str]:
        """Find all glossary terms/aliases mentioned in text. Returns canonical terms."""
        found = set()
        text_lower = text.lower()
        for alias, term in self._alias_to_term.items():
            # word boundary match to avoid partial matches
            if re.search(rf"\b{re.escape(alias)}\b", text_lower):
                found.add(term)
        return found


def load_glossary(path: str | Path) -> Glossary:
    """Load glossary from JSON file."""
    path = Path(path)
    if not path.exists():
        return Glossary(entries=[])

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    raw_entries = data.get("entries", [])
    entries = [
        GlossaryEntry(
            term=e["term"],
            aliases=e.get("aliases", []),
            definition=e.get("definition", ""),
            context=e.get("context", ""),
            pattern=e.get("pattern"),
        )
        for e in raw_entries
    ]
    return Glossary(entries=entries)
