#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
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
APPLYABLE_ACTIONS = WRITE_ACTIONS | CONDITIONAL_ACTIONS

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from readme_doc_healer.doc_scanner import ScannedDoc, scan_docs_directory


@dataclass
class SchemaTarget:
    schema_obj: dict[str, Any]
    target_kind: str
    parameter_obj: dict[str, Any] | None = None
    parent_schema_obj: dict[str, Any] | None = None


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
) -> list[SchemaTarget]:
    field_name_lower = field_name.lower()
    seen_target_ids: set[tuple[int, int]] = set()
    targets: list[SchemaTarget] = []

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

            target_id = (id(param_obj), id(schema_obj))
            if target_id in seen_target_ids:
                continue

            seen_target_ids.add(target_id)
            targets.append(
                SchemaTarget(
                    schema_obj=schema_obj,
                    target_kind="parameter",
                    parameter_obj=param_obj,
                )
            )

    return targets


def find_schema_property_targets(
    spec: dict[str, Any],
    schema_obj: dict[str, Any],
    field_name: str,
) -> list[SchemaTarget]:
    field_name_lower = field_name.lower()
    seen_nodes: set[int] = set()
    targets_by_id: dict[tuple[int, int], SchemaTarget] = {}

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
                    actual_prop = resolved_prop if isinstance(resolved_prop, dict) else prop_schema
                    target_key = (id(actual_prop), id(resolved_candidate))
                    targets_by_id[target_key] = SchemaTarget(
                        schema_obj=actual_prop,
                        target_kind="request_body_property",
                        parent_schema_obj=resolved_candidate,
                    )

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
) -> list[SchemaTarget]:
    request_body = resolve_object_dict(spec, operation.get("requestBody"), set())
    if not isinstance(request_body, dict):
        return []

    content = request_body.get("content")
    if not isinstance(content, dict):
        return []

    targets_by_id: dict[tuple[int, int], SchemaTarget] = {}
    for media in content.values():
        if not isinstance(media, dict):
            continue

        schema = resolve_schema_object(spec, media.get("schema"))
        if not isinstance(schema, dict):
            continue

        for target in find_schema_property_targets(spec, schema, field_name):
            target_key = (id(target.schema_obj), id(target.parent_schema_obj or {}))
            targets_by_id[target_key] = target

    return list(targets_by_id.values())


def get_request_body_media_targets(
    spec: dict[str, Any],
    operation: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    request_body = resolve_object_dict(spec, operation.get("requestBody"), set())
    if not isinstance(request_body, dict):
        return []

    content = request_body.get("content")
    if not isinstance(content, dict):
        return []

    json_targets: list[tuple[str, dict[str, Any]]] = []
    other_targets: list[tuple[str, dict[str, Any]]] = []
    for media_type, media_obj in content.items():
        if not isinstance(media_obj, dict):
            continue
        entry = (str(media_type), media_obj)
        if str(media_type).lower() == "application/json":
            json_targets.append(entry)
        else:
            other_targets.append(entry)

    return json_targets + other_targets


def get_response_media_target(
    spec: dict[str, Any],
    operation: dict[str, Any],
    status_code: str,
    media_type: str = "application/json",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return None, None

    response_obj = resolve_object_dict(spec, responses.get(status_code), set())
    if not isinstance(response_obj, dict):
        return None, None

    content = response_obj.get("content")
    if not isinstance(content, dict):
        return response_obj, None

    media_obj = content.get(media_type)
    if not isinstance(media_obj, dict):
        return response_obj, None

    return response_obj, media_obj


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


def normalize_field_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def description_is_empty_or_echo(field_name: str, description: Any) -> bool:
    if not isinstance(description, str) or not description.strip():
        return True
    return normalize_field_text(description) == normalize_field_text(field_name)


def parse_numeric_length(length_value: str) -> int | None:
    stripped = length_value.strip()
    if not stripped or not re.fullmatch(r"\d+", stripped):
        return None
    return int(stripped)


def parse_required_flag(required_value: str) -> bool | None:
    stripped = required_value.strip().lower()
    if not stripped:
        return None
    if stripped.startswith("required"):
        return True
    if stripped.startswith("optional"):
        return False
    return None


def add_traceability(target_obj: dict[str, Any], source_doc: str) -> None:
    existing = target_obj.get("x-enriched-from")
    if existing is None:
        target_obj["x-enriched-from"] = source_doc
        return

    if isinstance(existing, str):
        if existing != source_doc:
            target_obj["x-enriched-from"] = [existing, source_doc]
        return

    if isinstance(existing, list) and source_doc not in existing:
        existing.append(source_doc)


def media_has_example(media_obj: dict[str, Any]) -> bool:
    if "example" in media_obj and media_obj["example"] is not None:
        return True

    examples = media_obj.get("examples")
    if isinstance(examples, dict):
        return bool(examples)
    if isinstance(examples, list):
        return bool(examples)
    return False


def apply_max_length(schema_obj: dict[str, Any], max_length: int) -> tuple[bool, str]:
    if max_length < 0:
        return False, "invalid_max_length"
    if schema_obj.get("maxLength") is not None:
        return False, "existing_max_length"
    schema_obj["maxLength"] = max_length
    return True, "applied_max_length"


def apply_required_constraint(target: SchemaTarget, field_name: str) -> tuple[bool, str, dict[str, Any] | None]:
    if target.parameter_obj is not None:
        if target.parameter_obj.get("required") is True:
            return False, "existing_required", target.parameter_obj
        target.parameter_obj["required"] = True
        return True, "applied_required", target.parameter_obj

    parent_schema = target.parent_schema_obj
    if not isinstance(parent_schema, dict):
        return False, "missing_parent_schema", None

    required_fields = parent_schema.get("required")
    if required_fields is None:
        parent_schema["required"] = [field_name]
        return True, "applied_required", parent_schema

    if not isinstance(required_fields, list):
        return False, "invalid_required_container", parent_schema

    if field_name in required_fields:
        return False, "existing_required", parent_schema

    required_fields.append(field_name)
    return True, "applied_required", parent_schema


def apply_description(schema_obj: dict[str, Any], field_name: str, description: str) -> tuple[bool, str]:
    if not description.strip():
        return False, "missing_description_source"
    if not description_is_empty_or_echo(field_name, schema_obj.get("description")):
        return False, "existing_description"
    schema_obj["description"] = description.strip()
    return True, "applied_description"


def parse_json_example_body(body: str) -> tuple[Any | None, str | None]:
    stripped = body.strip()
    if not stripped:
        return None, "empty_example"

    try:
        return json.loads(stripped), None
    except json.JSONDecodeError as exc:
        return None, f"invalid_json:{exc.msg}"


def extract_json_body_from_sample_call(body: str) -> tuple[Any | None, str]:
    stripped = body.strip()
    if not stripped:
        return None, "empty_sample_call"

    if stripped.startswith("{") or stripped.startswith("["):
        parsed, error = parse_json_example_body(stripped)
        if parsed is not None:
            return parsed, "parsed_raw_json"
        return None, error or "invalid_json"

    cleaned = re.sub(r"\\\s*\n", " ", stripped)
    cleaned = cleaned.replace("\\", " ")

    try:
        tokens = shlex.split(cleaned)
    except ValueError:
        tokens = []

    data_flags = {"-d", "--data", "--data-raw", "--data-binary"}
    for idx, token in enumerate(tokens):
        if token not in data_flags or idx + 1 >= len(tokens):
            continue
        candidate = tokens[idx + 1].strip()
        parsed, error = parse_json_example_body(candidate)
        if parsed is not None:
            return parsed, "parsed_curl_data"
        return None, error or "invalid_json"

    return None, "unsupported_sample_call_format"


def map_error_code_to_status(error_code: str) -> str | None:
    match = re.match(r"\s*(\d{3})", error_code)
    if not match:
        return None
    status_code = match.group(1)
    if not 100 <= int(status_code) <= 599:
        return None
    return status_code


def record_change(
    changes: list[dict[str, Any]],
    *,
    doc: ScannedDoc,
    spec_path: str,
    method: str,
    field_name: str | None,
    section: str | None,
    target_kind: str,
    change_type: str,
    source_value: Any = None,
    target_value: Any = None,
    media_type: str | None = None,
    status_code: str | None = None,
) -> None:
    if len(changes) >= 600:
        return

    change_item: dict[str, Any] = {
        "source_doc": doc.filename,
        "source_title": doc.title,
        "path": spec_path,
        "method": method,
        "section": section,
        "field_name": field_name,
        "target_kind": target_kind,
        "change_type": change_type,
    }

    if source_value is not None:
        change_item["source_value"] = source_value
    if target_value is not None:
        change_item["target_value"] = target_value
    if media_type is not None:
        change_item["media_type"] = media_type
    if status_code is not None:
        change_item["status_code"] = status_code

    changes.append(change_item)


def add_review_note(
    notes: list[dict[str, Any]],
    *,
    doc: ScannedDoc,
    note_type: str,
    spec_path: str | None = None,
    method: str | None = None,
    field_name: str | None = None,
    detail: str | None = None,
    section: str | None = None,
    source_value: Any = None,
) -> None:
    if len(notes) >= 400:
        return

    note: dict[str, Any] = {
        "source_doc": doc.filename,
        "source_title": doc.title,
        "note_type": note_type,
    }
    if spec_path is not None:
        note["path"] = spec_path
    if method is not None:
        note["method"] = method
    if field_name is not None:
        note["field_name"] = field_name
    if detail is not None:
        note["detail"] = detail
    if section is not None:
        note["section"] = section
    if source_value is not None:
        note["source_value"] = source_value

    notes.append(note)


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

    if action == "suggest_boolean":
        spec_type = schema_obj.get("type")
        if spec_type == "boolean":
            return False, "existing_boolean_type"
        if isinstance(spec_type, list) and "boolean" in spec_type:
            return False, "existing_boolean_type"

        if schema_obj.get("enum"):
            return False, "existing_enum"
        if schema_obj.get("pattern") or schema_obj.get("format"):
            return False, "conflicting_schema_constraint"

        fallback_enum = rule.get("fallback_string_enum")
        if spec_type is None:
            schema_obj["type"] = rule.get("preferred_type", "boolean")
            return True, "applied_boolean_type"
        if spec_type == "string":
            if not isinstance(fallback_enum, list) or not fallback_enum:
                return False, "missing_boolean_fallback"
            schema_obj["enum"] = list(fallback_enum)
            return True, "applied_boolean_enum"
        if isinstance(spec_type, list) and "string" in spec_type:
            if not isinstance(fallback_enum, list) or not fallback_enum:
                return False, "missing_boolean_fallback"
            schema_obj["enum"] = list(fallback_enum)
            return True, "applied_boolean_enum"
        return False, "incompatible_boolean_target"

    if action == "suggest_format":
        suggested_format = rule.get("suggested_format")
        if not isinstance(suggested_format, str) or not suggested_format:
            return False, "missing_suggested_format"
        if schema_obj.get("format"):
            return False, "existing_format"
        if schema_obj.get("pattern"):
            return False, "conflicting_schema_constraint"

        spec_type = schema_obj.get("type")
        if spec_type is not None and spec_type != "string":
            if not (isinstance(spec_type, list) and "string" in spec_type):
                return False, "incompatible_format_target"

        schema_obj["format"] = suggested_format
        return True, "applied_format"

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


def apply_enrichment(
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
    metadata_result_counts: Counter[str] = Counter()
    example_result_counts: Counter[str] = Counter()
    error_response_result_counts: Counter[str] = Counter()

    docs_without_path_match: list[str] = []
    changes: list[dict[str, Any]] = []
    review_notes: list[dict[str, Any]] = []

    for doc in docs:
        matched_paths = find_matched_spec_paths(doc, spec_paths)
        if not matched_paths:
            docs_without_path_match.append(doc.filename)
            continue

        method_hints = infer_method_hints(doc.operation_name)
        matched_operations: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
        for spec_path in matched_paths:
            path_item = paths_obj.get(spec_path)
            if not isinstance(path_item, dict):
                continue
            operations = iter_operations_for_path(path_item, method_hints)
            for method, operation in operations:
                matched_operations.append((spec_path, method, path_item, operation))

        for constraint in doc.param_constraints:
            counters["total_param_constraints"] += 1

            if constraint.section not in {"url_parameters", "data_parameters"}:
                continue

            raw_value = constraint.character.strip()
            resolved: dict[str, Any] | None = None
            action: str | None = None
            if raw_value:
                counters["constraints_with_character_values"] += 1
                resolved = resolve_character_rule(raw_value, mapping)
                action = str(resolved.get("action"))
                canonical_name = str(resolved.get("canonical_name"))
                action_counts[action] += 1
                canonical_counts[canonical_name] += 1
                if action in NON_WRITE_ACTIONS:
                    counters["constraints_non_write_action"] += 1

            target_entries: list[tuple[SchemaTarget, str, str]] = []
            for spec_path, method, path_item, operation in matched_operations:
                if constraint.section == "url_parameters":
                    targets = find_parameter_schema_targets(spec, path_item, operation, constraint.name)
                else:
                    targets = find_request_body_schema_targets(spec, operation, constraint.name)

                for target in targets:
                    target_entries.append((target, spec_path, method))

            if not target_entries:
                counters["constraints_without_target"] += 1
                if action == "review":
                    add_review_note(
                        review_notes,
                        doc=doc,
                        note_type="character_value_needs_review",
                        field_name=constraint.name,
                        section=constraint.section,
                        detail="Constraint target was not found in the spec.",
                        source_value=raw_value,
                    )
                continue

            seen_target_ids: set[tuple[int, int, int, str, str]] = set()
            applied_for_constraint = False

            for target, spec_path, method in target_entries:
                target_id = (
                    id(target.schema_obj),
                    id(target.parameter_obj or {}),
                    id(target.parent_schema_obj or {}),
                    spec_path,
                    method,
                )
                if target_id in seen_target_ids:
                    continue
                seen_target_ids.add(target_id)

                if resolved is not None and action in APPLYABLE_ACTIONS:
                    if not rule_applies_to_target(resolved, constraint.name, target.schema_obj):
                        counters["targets_filtered_by_apply_when"] += 1
                        write_result_counts["filtered_by_apply_when"] += 1
                        add_review_note(
                            review_notes,
                            doc=doc,
                            note_type="filtered_by_apply_when",
                            spec_path=spec_path,
                            method=method,
                            field_name=constraint.name,
                            section=constraint.section,
                            detail=f"Rule '{action}' did not meet apply_when conditions.",
                            source_value=raw_value,
                        )
                    else:
                        applied, result_code = apply_write_action(target.schema_obj, resolved)
                        write_result_counts[result_code] += 1
                        if applied:
                            applied_for_constraint = True
                            counters["applied_changes"] += 1
                            add_traceability(target.schema_obj, doc.filename)
                            record_change(
                                changes,
                                doc=doc,
                                spec_path=spec_path,
                                method=method,
                                field_name=constraint.name,
                                section=constraint.section,
                                target_kind=target.target_kind,
                                change_type=action,
                                source_value=raw_value,
                                target_value=(resolved.get("pattern") or resolved.get("enum") or resolved.get("suggested_format") or resolved.get("preferred_type")),
                            )
                        elif action in CONDITIONAL_ACTIONS and result_code not in {"existing_boolean_type", "existing_format"}:
                            add_review_note(
                                review_notes,
                                doc=doc,
                                note_type="conditional_rule_not_applied",
                                spec_path=spec_path,
                                method=method,
                                field_name=constraint.name,
                                section=constraint.section,
                                detail=result_code,
                                source_value=raw_value,
                            )
                elif action == "review":
                    add_review_note(
                        review_notes,
                        doc=doc,
                        note_type="character_value_needs_review",
                        spec_path=spec_path,
                        method=method,
                        field_name=constraint.name,
                        section=constraint.section,
                        detail=resolved.get("reason") if resolved else None,
                        source_value=raw_value,
                    )

                max_length = parse_numeric_length(constraint.length)
                if max_length is not None:
                    applied, result_code = apply_max_length(target.schema_obj, max_length)
                    metadata_result_counts[result_code] += 1
                    if applied:
                        applied_for_constraint = True
                        counters["applied_changes"] += 1
                        add_traceability(target.schema_obj, doc.filename)
                        record_change(
                            changes,
                            doc=doc,
                            spec_path=spec_path,
                            method=method,
                            field_name=constraint.name,
                            section=constraint.section,
                            target_kind=target.target_kind,
                            change_type="max_length",
                            source_value=constraint.length,
                            target_value=max_length,
                        )

                required_flag = parse_required_flag(constraint.required)
                if required_flag is True:
                    applied, result_code, trace_target = apply_required_constraint(target, constraint.name)
                    metadata_result_counts[result_code] += 1
                    if applied and trace_target is not None:
                        applied_for_constraint = True
                        counters["applied_changes"] += 1
                        add_traceability(trace_target, doc.filename)
                        record_change(
                            changes,
                            doc=doc,
                            spec_path=spec_path,
                            method=method,
                            field_name=constraint.name,
                            section=constraint.section,
                            target_kind=target.target_kind,
                            change_type="required",
                            source_value=constraint.required,
                            target_value=True,
                        )

                if constraint.description.strip():
                    applied, result_code = apply_description(target.schema_obj, constraint.name, constraint.description)
                    metadata_result_counts[result_code] += 1
                    if applied:
                        applied_for_constraint = True
                        counters["applied_changes"] += 1
                        add_traceability(target.schema_obj, doc.filename)
                        record_change(
                            changes,
                            doc=doc,
                            spec_path=spec_path,
                            method=method,
                            field_name=constraint.name,
                            section=constraint.section,
                            target_kind=target.target_kind,
                            change_type="description",
                            source_value=constraint.description,
                            target_value=constraint.description.strip(),
                        )

            if applied_for_constraint:
                counters["constraints_applied"] += 1
            else:
                counters["constraints_not_applied"] += 1

        success_examples = [example for example in doc.examples if example.kind == "success_response"]
        if success_examples:
            for spec_path, method, _, operation in matched_operations:
                _, media_obj = get_response_media_target(spec, operation, "200")
                if not isinstance(media_obj, dict):
                    example_result_counts["missing_response_media"] += 1
                    continue
                if media_has_example(media_obj):
                    example_result_counts["existing_response_example"] += 1
                    continue

                applied = False
                for example in success_examples:
                    parsed, error = parse_json_example_body(example.body)
                    if parsed is None:
                        example_result_counts[error or "invalid_response_example"] += 1
                        add_review_note(
                            review_notes,
                            doc=doc,
                            note_type="invalid_response_example",
                            spec_path=spec_path,
                            method=method,
                            detail=error,
                            source_value=example.body[:200],
                        )
                        continue

                    media_obj["example"] = parsed
                    add_traceability(media_obj, doc.filename)
                    example_result_counts["applied_response_example"] += 1
                    counters["applied_changes"] += 1
                    applied = True
                    record_change(
                        changes,
                        doc=doc,
                        spec_path=spec_path,
                        method=method,
                        field_name=None,
                        section="success_response",
                        target_kind="response_media",
                        change_type="response_example",
                        target_value="application/json.example",
                        media_type="application/json",
                        status_code="200",
                    )
                    break

                if not applied and success_examples:
                    example_result_counts["response_example_not_applied"] += 1

        sample_call_examples = [example for example in doc.examples if example.kind == "sample_call"]
        if sample_call_examples:
            for spec_path, method, _, operation in matched_operations:
                media_targets = get_request_body_media_targets(spec, operation)
                if not media_targets:
                    example_result_counts["missing_request_media"] += 1
                    continue

                applied = False
                for media_type, media_obj in media_targets:
                    if media_has_example(media_obj):
                        example_result_counts["existing_request_example"] += 1
                        continue

                    for example in sample_call_examples:
                        parsed, result_code = extract_json_body_from_sample_call(example.body)
                        if parsed is None:
                            example_result_counts[result_code] += 1
                            if result_code not in {"unsupported_sample_call_format", "empty_sample_call"}:
                                add_review_note(
                                    review_notes,
                                    doc=doc,
                                    note_type="invalid_request_example",
                                    spec_path=spec_path,
                                    method=method,
                                    detail=result_code,
                                    source_value=example.body[:200],
                                )
                            continue

                        media_obj["example"] = parsed
                        add_traceability(media_obj, doc.filename)
                        example_result_counts["applied_request_example"] += 1
                        counters["applied_changes"] += 1
                        applied = True
                        record_change(
                            changes,
                            doc=doc,
                            spec_path=spec_path,
                            method=method,
                            field_name=None,
                            section="sample_call",
                            target_kind="request_media",
                            change_type="request_example",
                            target_value="requestBody.example",
                            media_type=media_type,
                        )
                        break

                    if applied:
                        break

                if not applied and sample_call_examples:
                    example_result_counts["request_example_not_applied"] += 1

        if doc.error_codes:
            for spec_path, method, _, operation in matched_operations:
                responses = operation.setdefault("responses", {})
                if not isinstance(responses, dict):
                    error_response_result_counts["invalid_responses_container"] += 1
                    continue

                for error_code in doc.error_codes:
                    status_code = map_error_code_to_status(error_code.code)
                    if status_code is None:
                        error_response_result_counts["unmapped_error_code"] += 1
                        add_review_note(
                            review_notes,
                            doc=doc,
                            note_type="unmapped_error_code",
                            spec_path=spec_path,
                            method=method,
                            detail="Could not map legacy error code to an HTTP status.",
                            source_value=error_code.code,
                        )
                        continue

                    if status_code in responses:
                        error_response_result_counts["existing_error_response"] += 1
                        continue

                    response_description = error_code.description.strip() or f"Legacy error code {error_code.code}"
                    responses[status_code] = {
                        "description": response_description,
                    }
                    add_traceability(responses[status_code], doc.filename)
                    error_response_result_counts["applied_error_response"] += 1
                    counters["applied_changes"] += 1
                    record_change(
                        changes,
                        doc=doc,
                        spec_path=spec_path,
                        method=method,
                        field_name=None,
                        section="error_response",
                        target_kind="response",
                        change_type="error_response",
                        source_value=error_code.code,
                        target_value=response_description,
                        status_code=status_code,
                    )

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
            "response_examples_applied": example_result_counts["applied_response_example"],
            "request_examples_applied": example_result_counts["applied_request_example"],
            "error_responses_applied": error_response_result_counts["applied_error_response"],
            "review_note_count": len(review_notes),
        },
        "action_counts": dict(sorted(action_counts.items())),
        "canonical_counts": dict(sorted(canonical_counts.items())),
        "write_result_counts": dict(sorted(write_result_counts.items())),
        "metadata_result_counts": dict(sorted(metadata_result_counts.items())),
        "example_result_counts": dict(sorted(example_result_counts.items())),
        "error_response_result_counts": dict(sorted(error_response_result_counts.items())),
        "docs_without_path_match": sorted(docs_without_path_match),
        "changes": changes,
        "needs_review": review_notes,
    }


def apply_character_mapping(
    spec: dict[str, Any],
    docs: list[ScannedDoc],
    mapping: dict[str, Any],
) -> dict[str, Any]:
    return apply_enrichment(spec, docs, mapping)


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

        apply_result = apply_enrichment(spec, docs, mapping)
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