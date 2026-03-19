#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head", "trace"}
WRITE_ACTIONS = {"apply_pattern", "apply_enum"}
CONDITIONAL_ACTIONS = {"suggest_boolean", "suggest_format"}
NON_WRITE_ACTIONS = {"skip", "review"}

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from readme_doc_healer.doc_scanner import ScannedDoc, scan_docs_directory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Load an OpenAPI spec, legacy docs, and a reviewed Character value "
            "mapping, then generate a dry-run summary or apply safe enrichments."
        )
    )
    parser.add_argument("--spec", required=True, type=Path, help="Path to the base OpenAPI spec.")
    parser.add_argument("--docs", required=True, type=Path, help="Path to the legacy documentation directory.")
    parser.add_argument(
        "--mapping",
        type=Path,
        help="Path to the Character value mapping YAML. Defaults to <docs parent>/character-value-mapping.yaml.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help=(
            "Path to write the JSON report. Defaults to "
            "result_data/enrich/<spec>.character-summary.json in dry-run mode, "
            "or result_data/enrich/<spec>.apply-report.json in apply mode."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Output path for enriched spec in apply mode. Defaults to a sibling file "
            "with '.best.enriched.openapi.*' naming when possible."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply write actions from the mapping (apply_pattern and apply_enum) and write an enriched spec.",
    )
    return parser.parse_args()


def relative_to_root(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def normalize_character_value(value: str, collapse_whitespace: bool) -> str:
    text = value.strip()
    if collapse_whitespace:
        text = re.sub(r"\s+", " ", text)
    return text


def default_mapping_path(docs_path: Path) -> Path:
    return docs_path.resolve().parent / "character-value-mapping.yaml"


def default_report_path(spec_path: Path) -> Path:
    name = spec_path.name
    for suffix in (".yaml", ".yml", ".json"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return ROOT / "result_data" / "enrich" / f"{name}.character-summary.json"


def default_apply_report_path(spec_path: Path) -> Path:
    name = spec_path.name
    for suffix in (".yaml", ".yml", ".json"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return ROOT / "result_data" / "enrich" / f"{name}.apply-report.json"


def default_output_path(spec_path: Path) -> Path:
    name = spec_path.name
    if ".best.openapi." in name:
        name = name.replace(".best.openapi.", ".best.enriched.openapi.")
        return spec_path.with_name(name)

    for suffix in (".yaml", ".yml", ".json"):
        if name.endswith(suffix):
            stem = name[: -len(suffix)]
            return spec_path.with_name(f"{stem}.enriched{suffix}")

    return spec_path.with_name(f"{name}.enriched")


def load_spec(path: Path) -> dict[str, Any]:
    raw = path.read_text()
    if path.suffix.lower() == ".json":
        return json.loads(raw)
    return yaml.safe_load(raw)


def write_spec(path: Path, spec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(spec, indent=2, ensure_ascii=True) + "\n")
        return

    dumped = yaml.safe_dump(spec, sort_keys=False, allow_unicode=False)
    path.write_text(dumped)


def backup_existing_file(path: Path) -> Path | None:
    if not path.exists():
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    backup_dir = ROOT / "bak"
    backup_dir.mkdir(parents=True, exist_ok=True)

    candidate = backup_dir / f"{path.name}.bak.{stamp}"
    idx = 1
    while candidate.exists():
        candidate = backup_dir / f"{path.name}.bak.{stamp}.{idx}"
        idx += 1

    shutil.copy2(path, candidate)
    return candidate


def count_operations(spec: dict[str, Any]) -> int:
    operations = 0
    for path_item in (spec.get("paths") or {}).values():
        for method in path_item:
            if method in HTTP_METHODS:
                operations += 1
    return operations


def load_mapping(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text()) or {}
    mapping = loaded.get("character_value_mapping")
    if not isinstance(mapping, dict):
        raise ValueError("Mapping file must contain a top-level 'character_value_mapping' object.")

    raw_to_canonical = mapping.get("raw_to_canonical") or {}
    canonical = mapping.get("canonical") or {}
    normalization = mapping.get("normalization") or {}

    if not isinstance(raw_to_canonical, dict) or not isinstance(canonical, dict):
        raise ValueError("Mapping file must define 'raw_to_canonical' and 'canonical' mappings.")

    missing_canonical = sorted({name for name in raw_to_canonical.values() if name not in canonical})
    if missing_canonical:
        joined = ", ".join(missing_canonical)
        raise ValueError(f"Mapping file references undefined canonical keys: {joined}")

    if "needs_review" not in canonical:
        raise ValueError("Mapping file must define the 'needs_review' canonical rule.")

    for name, rule in canonical.items():
        if not isinstance(rule, dict) or "action" not in rule:
            raise ValueError(f"Canonical mapping '{name}' must define an action.")
        field_name_pattern = ((rule.get("apply_when") or {}).get("field_name_matches"))
        if field_name_pattern:
            re.compile(field_name_pattern)

    return {
        "normalization": normalization,
        "raw_to_canonical": raw_to_canonical,
        "canonical": canonical,
    }


def resolve_character_rule(raw_value: str, mapping: dict[str, Any]) -> dict[str, Any]:
    collapse_whitespace = bool((mapping.get("normalization") or {}).get("collapse_whitespace", True))
    normalized_value = normalize_character_value(raw_value, collapse_whitespace)
    raw_to_canonical = mapping["raw_to_canonical"]
    canonical_name = raw_to_canonical.get(normalized_value)
    missing_mapping = canonical_name is None

    if missing_mapping:
        canonical_name = "needs_review"

    rule = dict(mapping["canonical"][canonical_name])
    rule["canonical_name"] = canonical_name
    rule["normalized_value"] = normalized_value
    rule["missing_mapping"] = missing_mapping
    return rule


def _decode_json_pointer_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def resolve_local_ref(spec: dict[str, Any], ref: str) -> dict[str, Any] | None:
    if not ref.startswith("#/"):
        return None

    node: Any = spec
    for token in ref[2:].split("/"):
        key = _decode_json_pointer_token(token)
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]

    if isinstance(node, dict):
        return node
    return None


def resolve_object_dict(
    spec: dict[str, Any],
    obj: dict[str, Any] | None,
    seen_refs: set[str],
) -> dict[str, Any] | None:
    if not isinstance(obj, dict):
        return None

    current: dict[str, Any] | None = obj
    while isinstance(current, dict) and "$ref" in current:
        ref = current.get("$ref")
        if not isinstance(ref, str):
            return None
        if ref in seen_refs:
            return None
        seen_refs.add(ref)
        current = resolve_local_ref(spec, ref)

    return current if isinstance(current, dict) else None


def normalize_path(path: str) -> str:
    return path.lower().rstrip("/")


def paths_match(spec_path: str, doc_path: str) -> bool:
    spec_norm = normalize_path(spec_path)
    doc_norm = normalize_path(doc_path)

    if spec_norm == doc_norm:
        return True

    regex_str = re.sub(r"\{[^}]+\}", r"[^/]+", re.escape(spec_norm))
    regex_str = regex_str.replace(r"\{", "{").replace(r"\}", "}")
    regex_str = re.sub(r"\\(\[)", r"\1", regex_str)
    regex_str = re.sub(r"\\(\])", r"\1", regex_str)
    regex_str = re.sub(r"\\(\+)", r"\1", regex_str)
    regex_str = re.sub(r"\\(\^)", r"\1", regex_str)

    try:
        return bool(re.fullmatch(regex_str, doc_norm))
    except re.error:
        return spec_norm == doc_norm


def find_matched_spec_paths(doc: ScannedDoc, spec_paths: list[str]) -> list[str]:
    matched: list[str] = []
    for found in doc.endpoint_paths_found:
        for spec_path in spec_paths:
            if paths_match(spec_path, found) and spec_path not in matched:
                matched.append(spec_path)
    return matched


def infer_method_hints(operation_name: str) -> set[str] | None:
    tokens = {t.lower() for t in re.findall(r"[A-Za-z]+", operation_name or "")}
    hints: set[str] = set()

    if tokens & {"get", "list"}:
        hints.add("get")
    if tokens & {"delete", "remove"}:
        hints.add("delete")
    if tokens & {"add", "create", "attach", "reset", "update", "edit", "set", "detach", "generate", "check"}:
        hints.update({"post", "put", "patch"})

    return hints or None


def iter_operations_for_path(
    path_item: dict[str, Any],
    method_hints: set[str] | None,
) -> list[tuple[str, dict[str, Any]]]:
    operations = [
        (method, operation)
        for method, operation in path_item.items()
        if method in HTTP_METHODS and isinstance(operation, dict)
    ]
    if not method_hints:
        return operations

    filtered = [item for item in operations if item[0] in method_hints]
    return filtered or operations


def resolve_parameter_object(spec: dict[str, Any], parameter_obj: Any) -> dict[str, Any] | None:
    if not isinstance(parameter_obj, dict):
        return None

    if "$ref" in parameter_obj:
        return resolve_local_ref(spec, str(parameter_obj["$ref"]))
    return parameter_obj


def resolve_schema_object(spec: dict[str, Any], schema_obj: Any) -> dict[str, Any] | None:
    if not isinstance(schema_obj, dict):
        return None
    return resolve_object_dict(spec, schema_obj, set())


def find_parameter_schema_targets(
    spec: dict[str, Any],
    path_item: dict[str, Any],
    operation: dict[str, Any],
    field_name: str,
) -> list[dict[str, Any]]:
    field_name_lower = field_name.lower()
    seen_schema_ids: set[int] = set()
    targets: list[dict[str, Any]] = []

    for container in (path_item.get("parameters") or [], operation.get("parameters") or []):
        if not isinstance(container, list):
            continue

        for parameter in container:
            param_obj = resolve_parameter_object(spec, parameter)
            if not isinstance(param_obj, dict):
                continue
            if str(param_obj.get("name", "")).lower() != field_name_lower:
                continue

            schema_obj = resolve_schema_object(spec, param_obj.get("schema"))
            if not isinstance(schema_obj, dict):
                continue

            schema_id = id(schema_obj)
            if schema_id in seen_schema_ids:
                continue

            seen_schema_ids.add(schema_id)
            targets.append(schema_obj)

    return targets


def find_schema_property_targets(
    spec: dict[str, Any],
    schema_obj: dict[str, Any],
    field_name: str,
) -> list[dict[str, Any]]:
    field_name_lower = field_name.lower()
    seen_nodes: set[int] = set()
    targets_by_id: dict[int, dict[str, Any]] = {}

    def walk(candidate: dict[str, Any]) -> None:
        resolved_candidate = resolve_object_dict(spec, candidate, set())
        if not isinstance(resolved_candidate, dict):
            return

        candidate_id = id(resolved_candidate)
        if candidate_id in seen_nodes:
            return
        seen_nodes.add(candidate_id)

        properties = resolved_candidate.get("properties")
        if isinstance(properties, dict):
            for prop_name, prop_schema in properties.items():
                if not isinstance(prop_schema, dict):
                    continue

                if prop_name.lower() == field_name_lower:
                    resolved_prop = resolve_object_dict(spec, prop_schema, set())
                    if isinstance(resolved_prop, dict):
                        targets_by_id[id(resolved_prop)] = resolved_prop
                    else:
                        targets_by_id[id(prop_schema)] = prop_schema

                walk(prop_schema)

        for key in ("allOf", "anyOf", "oneOf"):
            composition = resolved_candidate.get(key)
            if isinstance(composition, list):
                for item in composition:
                    if isinstance(item, dict):
                        walk(item)

        items = resolved_candidate.get("items")
        if isinstance(items, dict):
            walk(items)

        additional = resolved_candidate.get("additionalProperties")
        if isinstance(additional, dict):
            walk(additional)

    walk(schema_obj)
    return list(targets_by_id.values())


def find_request_body_schema_targets(
    spec: dict[str, Any],
    operation: dict[str, Any],
    field_name: str,
) -> list[dict[str, Any]]:
    request_body = resolve_object_dict(spec, operation.get("requestBody"), set())
    if not isinstance(request_body, dict):
        return []

    content = request_body.get("content")
    if not isinstance(content, dict):
        return []

    targets_by_id: dict[int, dict[str, Any]] = {}
    for media in content.values():
        if not isinstance(media, dict):
            continue

        schema = resolve_schema_object(spec, media.get("schema"))
        if not isinstance(schema, dict):
            continue

        for target in find_schema_property_targets(spec, schema, field_name):
            targets_by_id[id(target)] = target

    return list(targets_by_id.values())


def rule_applies_to_target(rule: dict[str, Any], field_name: str, schema_obj: dict[str, Any]) -> bool:
    apply_when = rule.get("apply_when") or {}
    if not isinstance(apply_when, dict):
        return True

    field_name_in = apply_when.get("field_name_in")
    if isinstance(field_name_in, list):
        allowed = {str(item).lower() for item in field_name_in}
        if field_name.lower() not in allowed:
            return False

    field_name_matches = apply_when.get("field_name_matches")
    if isinstance(field_name_matches, str):
        if re.search(field_name_matches, field_name) is None:
            return False

    spec_type_in = apply_when.get("spec_type_in")
    if isinstance(spec_type_in, list):
        allowed_types = {str(item) for item in spec_type_in}
        spec_type = schema_obj.get("type")

        if isinstance(spec_type, list):
            if not any(str(t) in allowed_types for t in spec_type):
                return False
        elif spec_type is None:
            if "null" not in allowed_types:
                return False
        else:
            if str(spec_type) not in allowed_types:
                return False

    return True


def apply_write_action(schema_obj: dict[str, Any], rule: dict[str, Any]) -> tuple[bool, str]:
    action = rule.get("action")
    if action == "apply_pattern":
        pattern = rule.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return False, "missing_pattern"
        if schema_obj.get("pattern"):
            return False, "existing_pattern"
        schema_obj["pattern"] = pattern
        return True, "applied_pattern"

    if action == "apply_enum":
        enum_values = rule.get("enum")
        if not isinstance(enum_values, list) or not enum_values:
            return False, "missing_enum"
        if schema_obj.get("enum"):
            return False, "existing_enum"
        schema_obj["enum"] = list(enum_values)
        return True, "applied_enum"

    return False, "unsupported_action"


def build_character_summary(
    spec_path: Path,
    spec: dict[str, Any],
    docs_path: Path,
    mapping_path: Path,
    mapping: dict[str, Any],
    docs: list[ScannedDoc] | None = None,
) -> dict[str, Any]:
    if docs is None:
        docs = scan_docs_directory(docs_path)
    total_constraints = 0
    constraints_with_character = 0
    action_counts: Counter[str] = Counter()
    canonical_counts: Counter[str] = Counter()
    unique_values: dict[str, dict[str, Any]] = {}

    for doc in docs:
        for constraint in doc.param_constraints:
            total_constraints += 1
            raw_value = constraint.character.strip()
            if not raw_value:
                continue

            constraints_with_character += 1
            resolved = resolve_character_rule(raw_value, mapping)
            normalized_value = resolved["normalized_value"]
            action = resolved["action"]
            canonical_name = resolved["canonical_name"]

            action_counts[action] += 1
            canonical_counts[canonical_name] += 1

            if normalized_value not in unique_values:
                unique_values[normalized_value] = {
                    "normalized_value": normalized_value,
                    "canonical_name": canonical_name,
                    "action": action,
                    "confidence": resolved.get("confidence"),
                    "missing_mapping": resolved["missing_mapping"],
                    "pattern": resolved.get("pattern"),
                    "enum": resolved.get("enum"),
                    "preferred_type": resolved.get("preferred_type"),
                    "fallback_string_enum": resolved.get("fallback_string_enum"),
                    "suggested_format": resolved.get("suggested_format"),
                    "apply_when": resolved.get("apply_when"),
                    "reason": resolved.get("reason"),
                    "note": resolved.get("note"),
                    "occurrence_count": 0,
                    "raw_variants": set(),
                    "sections": set(),
                    "field_counter": Counter(),
                    "source_counter": Counter(),
                }

            item = unique_values[normalized_value]
            item["occurrence_count"] += 1
            item["raw_variants"].add(raw_value)
            item["sections"].add(constraint.section)
            item["field_counter"][constraint.name] += 1
            item["source_counter"][doc.filename] += 1

    values = []
    for item in unique_values.values():
        values.append(
            {
                "normalized_value": item["normalized_value"],
                "canonical_name": item["canonical_name"],
                "action": item["action"],
                "confidence": item["confidence"],
                "missing_mapping": item["missing_mapping"],
                "pattern": item["pattern"],
                "enum": item["enum"],
                "preferred_type": item["preferred_type"],
                "fallback_string_enum": item["fallback_string_enum"],
                "suggested_format": item["suggested_format"],
                "apply_when": item["apply_when"],
                "reason": item["reason"],
                "note": item["note"],
                "occurrence_count": item["occurrence_count"],
                "raw_variants": sorted(item["raw_variants"]),
                "sections": sorted(item["sections"]),
                "field_names": [
                    {"name": name, "count": count}
                    for name, count in sorted(
                        item["field_counter"].items(),
                        key=lambda pair: (-pair[1], pair[0].lower()),
                    )
                ],
                "source_files": [
                    {"filename": filename, "count": count}
                    for filename, count in sorted(
                        item["source_counter"].items(),
                        key=lambda pair: (-pair[1], pair[0].lower()),
                    )
                ],
            }
        )

    values.sort(key=lambda item: (-item["occurrence_count"], item["normalized_value"].lower()))

    missing_mapping_values = [item["normalized_value"] for item in values if item["missing_mapping"]]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": True,
        "spec_path": relative_to_root(spec_path.resolve()),
        "docs_path": relative_to_root(docs_path.resolve()),
        "mapping_path": relative_to_root(mapping_path.resolve()),
        "summary": {
            "paths_in_spec": len(spec.get("paths") or {}),
            "operations_in_spec": count_operations(spec),
            "docs_scanned": len(docs),
            "total_param_constraints": total_constraints,
            "constraints_with_character_values": constraints_with_character,
            "unique_normalized_values": len(values),
            "missing_mapping_value_count": len(missing_mapping_values),
            "missing_mapping_values": missing_mapping_values,
            "write_action_occurrences": sum(action_counts[action] for action in WRITE_ACTIONS),
            "conditional_action_occurrences": sum(action_counts[action] for action in CONDITIONAL_ACTIONS),
            "non_write_action_occurrences": sum(action_counts[action] for action in NON_WRITE_ACTIONS),
        },
        "action_counts": dict(sorted(action_counts.items())),
        "canonical_counts": dict(sorted(canonical_counts.items())),
        "values": values,
    }


def apply_character_mapping(
    spec: dict[str, Any],
    docs: list[ScannedDoc],
    mapping: dict[str, Any],
) -> dict[str, Any]:
    paths_obj = spec.get("paths") or {}
    spec_paths = [path for path in paths_obj.keys() if isinstance(path, str)]

    counters: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    canonical_counts: Counter[str] = Counter()
    write_result_counts: Counter[str] = Counter()

    docs_without_path_match: list[str] = []
    changes: list[dict[str, Any]] = []

    for doc in docs:
        matched_paths = find_matched_spec_paths(doc, spec_paths)
        if not matched_paths:
            docs_without_path_match.append(doc.filename)
            continue

        method_hints = infer_method_hints(doc.operation_name)

        for constraint in doc.param_constraints:
            counters["total_param_constraints"] += 1

            if constraint.section not in {"url_parameters", "data_parameters"}:
                continue

            raw_value = constraint.character.strip()
            if not raw_value:
                continue

            counters["constraints_with_character_values"] += 1
            resolved = resolve_character_rule(raw_value, mapping)
            action = str(resolved.get("action"))
            canonical_name = str(resolved.get("canonical_name"))
            action_counts[action] += 1
            canonical_counts[canonical_name] += 1

            if action not in WRITE_ACTIONS:
                counters["constraints_non_write_action"] += 1
                continue

            target_entries: list[tuple[dict[str, Any], str, str, str]] = []
            for spec_path in matched_paths:
                path_item = paths_obj.get(spec_path)
                if not isinstance(path_item, dict):
                    continue

                operations = iter_operations_for_path(path_item, method_hints)
                for method, operation in operations:
                    if constraint.section == "url_parameters":
                        targets = find_parameter_schema_targets(spec, path_item, operation, constraint.name)
                        target_kind = "parameter"
                    else:
                        targets = find_request_body_schema_targets(spec, operation, constraint.name)
                        target_kind = "request_body_property"

                    for target in targets:
                        target_entries.append((target, spec_path, method, target_kind))

            if not target_entries:
                counters["constraints_without_target"] += 1
                continue

            seen_target_ids: set[int] = set()
            applied_for_constraint = False

            for target_schema, spec_path, method, target_kind in target_entries:
                target_id = id(target_schema)
                if target_id in seen_target_ids:
                    continue
                seen_target_ids.add(target_id)

                if not rule_applies_to_target(resolved, constraint.name, target_schema):
                    counters["targets_filtered_by_apply_when"] += 1
                    continue

                applied, result_code = apply_write_action(target_schema, resolved)
                write_result_counts[result_code] += 1
                if not applied:
                    continue

                applied_for_constraint = True
                counters["applied_changes"] += 1

                if len(changes) < 400:
                    change_item: dict[str, Any] = {
                        "source_doc": doc.filename,
                        "source_title": doc.title,
                        "path": spec_path,
                        "method": method,
                        "section": constraint.section,
                        "field_name": constraint.name,
                        "target_kind": target_kind,
                        "action": action,
                        "canonical_name": canonical_name,
                        "normalized_value": resolved.get("normalized_value"),
                        "result_code": result_code,
                    }

                    if action == "apply_pattern":
                        change_item["pattern"] = resolved.get("pattern")
                    if action == "apply_enum":
                        change_item["enum"] = resolved.get("enum")

                    changes.append(change_item)

            if applied_for_constraint:
                counters["constraints_applied"] += 1
            else:
                counters["constraints_not_applied"] += 1

    return {
        "summary": {
            "docs_scanned": len(docs),
            "docs_without_path_match_count": len(docs_without_path_match),
            "total_param_constraints": counters["total_param_constraints"],
            "constraints_with_character_values": counters["constraints_with_character_values"],
            "constraints_non_write_action": counters["constraints_non_write_action"],
            "constraints_without_target": counters["constraints_without_target"],
            "targets_filtered_by_apply_when": counters["targets_filtered_by_apply_when"],
            "constraints_applied": counters["constraints_applied"],
            "constraints_not_applied": counters["constraints_not_applied"],
            "applied_changes": counters["applied_changes"],
        },
        "action_counts": dict(sorted(action_counts.items())),
        "canonical_counts": dict(sorted(canonical_counts.items())),
        "write_result_counts": dict(sorted(write_result_counts.items())),
        "docs_without_path_match": sorted(docs_without_path_match),
        "changes": changes,
    }


def main() -> int:
    args = parse_args()

    spec_path = args.spec.resolve()
    docs_path = args.docs.resolve()
    mapping_path = args.mapping.resolve() if args.mapping else default_mapping_path(docs_path)
    if args.apply:
        report_path = args.report.resolve() if args.report else default_apply_report_path(spec_path)
    else:
        report_path = args.report.resolve() if args.report else default_report_path(spec_path)

    if not spec_path.is_file():
        raise SystemExit(f"Spec path is not a file: {spec_path}")
    if not docs_path.is_dir():
        raise SystemExit(f"Docs path is not a directory: {docs_path}")
    if not mapping_path.is_file():
        raise SystemExit(f"Mapping path is not a file: {mapping_path}")

    spec = load_spec(spec_path)
    docs = scan_docs_directory(docs_path)
    mapping = load_mapping(mapping_path)

    if args.apply:
        output_path = args.output.resolve() if args.output else default_output_path(spec_path)
        output_backup = backup_existing_file(output_path)

        apply_result = apply_character_mapping(spec, docs, mapping)
        write_spec(output_path, spec)

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": False,
            "apply": True,
            "spec_path": relative_to_root(spec_path),
            "docs_path": relative_to_root(docs_path),
            "mapping_path": relative_to_root(mapping_path),
            "output_path": relative_to_root(output_path),
            "output_backup_path": relative_to_root(output_backup) if output_backup else None,
            "spec_paths_count": len(spec.get("paths") or {}),
            "spec_operations_count": count_operations(spec),
            "apply_result": apply_result,
        }

        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n")

        print(f"Wrote enriched spec: {relative_to_root(output_path)}")
        print(f"Wrote apply report: {relative_to_root(report_path)}")
        print(f"Applied changes: {apply_result['summary']['applied_changes']}")
        print(f"Constraints applied: {apply_result['summary']['constraints_applied']}")
        print(f"Constraints without target: {apply_result['summary']['constraints_without_target']}")
        print(f"Docs without path match: {apply_result['summary']['docs_without_path_match_count']}")
        return 0

    summary = build_character_summary(spec_path, spec, docs_path, mapping_path, mapping, docs=docs)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n")

    print(f"Wrote dry-run report: {relative_to_root(report_path)}")
    print(f"Docs scanned: {summary['summary']['docs_scanned']}")
    print(f"Unique normalized values: {summary['summary']['unique_normalized_values']}")
    print(f"Missing mapping values: {summary['summary']['missing_mapping_value_count']}")
    print(f"Write action occurrences: {summary['summary']['write_action_occurrences']}")
    print(f"Conditional action occurrences: {summary['summary']['conditional_action_occurrences']}")
    print(f"Non-write action occurrences: {summary['summary']['non_write_action_occurrences']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())