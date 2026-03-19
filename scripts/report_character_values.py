#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from readme_doc_healer.doc_scanner import scan_docs_directory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan legacy docs for observed Character column values and write "
            "a JSON report for mapping review."
        )
    )
    parser.add_argument(
        "--docs",
        required=True,
        type=Path,
        help="Path to the legacy documentation directory.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path to the JSON report file to write.",
    )
    return parser.parse_args()


def normalize_character_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def relative_to_root(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def build_report(docs_path: Path) -> dict[str, Any]:
    docs = scan_docs_directory(docs_path)
    groups: dict[str, dict[str, Any]] = {}
    total_constraints = 0
    constraints_with_character = 0

    for doc in docs:
        for constraint in doc.param_constraints:
            total_constraints += 1
            raw_value = constraint.character.strip()
            if not raw_value:
                continue

            constraints_with_character += 1
            normalized_value = normalize_character_value(raw_value)

            if normalized_value not in groups:
                groups[normalized_value] = {
                    "normalized_value": normalized_value,
                    "raw_variants": set(),
                    "sections": set(),
                    "field_counter": Counter(),
                    "source_counter": Counter(),
                    "source_titles": {},
                    "occurrences": [],
                }

            group = groups[normalized_value]
            group["raw_variants"].add(raw_value)
            group["sections"].add(constraint.section)
            group["field_counter"][constraint.name] += 1
            group["source_counter"][doc.filename] += 1
            group["source_titles"][doc.filename] = doc.title
            group["occurrences"].append(
                {
                    "source_file": doc.filename,
                    "source_title": doc.title,
                    "section": constraint.section,
                    "field_name": constraint.name,
                    "description": constraint.description,
                    "character": raw_value,
                    "length": constraint.length,
                    "required": constraint.required,
                }
            )

    values: list[dict[str, Any]] = []
    for normalized_value, group in groups.items():
        field_names = [
            {"name": name, "count": count}
            for name, count in sorted(
                group["field_counter"].items(),
                key=lambda item: (-item[1], item[0].lower()),
            )
        ]
        source_files = [
            {
                "filename": filename,
                "title": group["source_titles"][filename],
                "count": count,
            }
            for filename, count in sorted(
                group["source_counter"].items(),
                key=lambda item: (-item[1], item[0].lower()),
            )
        ]
        occurrences = sorted(
            group["occurrences"],
            key=lambda item: (
                item["source_file"].lower(),
                item["section"].lower(),
                item["field_name"].lower(),
            ),
        )
        values.append(
            {
                "normalized_value": normalized_value,
                "occurrence_count": len(occurrences),
                "raw_variants": sorted(group["raw_variants"]),
                "sections": sorted(group["sections"]),
                "field_names": field_names,
                "source_files": source_files,
                "occurrences": occurrences,
            }
        )

    values.sort(
        key=lambda item: (-item["occurrence_count"], item["normalized_value"].lower())
    )

    return {
        "docs_path": relative_to_root(docs_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "docs_scanned": len(docs),
            "total_param_constraints": total_constraints,
            "constraints_with_character_values": constraints_with_character,
            "unique_character_values": len(values),
        },
        "values": values,
    }


def main() -> int:
    args = parse_args()
    docs_path = args.docs.resolve()
    output_path = args.output.resolve()

    if not docs_path.is_dir():
        raise SystemExit(f"Docs path is not a directory: {docs_path}")

    report = build_report(docs_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n")

    print(f"Wrote report: {relative_to_root(output_path)}")
    print(f"Docs scanned: {report['summary']['docs_scanned']}")
    print(
        "Constraints with Character values: "
        f"{report['summary']['constraints_with_character_values']}"
    )
    print(f"Unique Character values: {report['summary']['unique_character_values']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())