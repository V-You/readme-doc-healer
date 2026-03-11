#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
BASE_DATA = ROOT / "base_data"
COLLECTION_PATH = BASE_DATA / "ACI Merchant Onboarding API.postman_collection.json"
LOCAL_SPEC_PATH = BASE_DATA / "ACI Merchant Onboarding API.openapi.yaml"
ONLINE_SPEC_PATH = BASE_DATA / "ACI_WebAPI_OpenAPI300___p2o-defcon007-com.txt"
OUTPUT_JSON_PATH = BASE_DATA / "ACI Merchant Onboarding API.best.openapi.json"
OUTPUT_YAML_PATH = BASE_DATA / "ACI Merchant Onboarding API.best.openapi.yaml"
OUTPUT_DUPLICATE_REPORT_PATH = BASE_DATA / "ACI Merchant Onboarding API.duplicate-report.json"

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head", "trace"}

COLLISION_OVERRIDES: dict[tuple[str, str], dict[str, str]] = {
    (
        "post",
        "/merchants/{param}/channels",
    ): {
        "summary": "Add Channel",
        "description": (
            "Add a channel to a merchant. The Postman collection contains two "
            "equivalent requests for this same operation."
        ),
        "operationId": "addChannel",
    },
    (
        "post",
        "/merchantAccounts/{param}",
    ): {
        "summary": "Update Merchant Account",
        "description": (
            "Update a merchant account. The Postman collection uses this same "
            "endpoint for both general merchant account edits and 3DS2 "
            "configuration, and the examples below capture both workflows."
        ),
        "operationId": "updateMerchantAccount",
    },
    (
        "post",
        "/merchants/{param}/setting",
    ): {
        "summary": "Update Merchant Settings",
        "description": (
            "Update merchant-level settings. The Postman collection uses this "
            "same endpoint for generic RIRO updates and 3DS2 activation, and "
            "the examples below capture both workflows."
        ),
        "operationId": "updateMerchantSettings",
    },
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def normalize_path_template(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{param}", path)


def unique_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def extract_servers(description: str) -> list[dict[str, str]]:
    urls = unique_urls(re.findall(r"https?://[^\s)<]+", description or ""))
    return [{"url": url} for url in urls]


def postman_path(url: Any) -> str:
    if isinstance(url, dict):
        path_segments = url.get("path") or []
        if isinstance(path_segments, list) and path_segments:
            parts = []
            for segment in path_segments:
                if isinstance(segment, str) and segment.startswith("{{") and segment.endswith("}}"):
                    parts.append("{" + segment[2:-2] + "}")
                else:
                    parts.append(str(segment))
            return "/" + "/".join(parts)

        raw = str(url.get("raw") or "")
    else:
        raw = str(url or "")

    if not raw:
        return "/"

    raw = re.sub(r"^\{\{[^}]+\}\}", "", raw)
    raw = re.sub(r"^https?://[^/]+", "", raw)
    raw = raw or "/"

    def replace_placeholder(match: re.Match[str]) -> str:
        return "{" + match.group(1) + "}"

    return re.sub(r"\{\{([^}]+)\}\}", replace_placeholder, raw)


def flatten_collection(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def walk(nodes: list[dict[str, Any]], lineage: list[str]) -> None:
        for node in nodes:
            if "item" in node and "request" not in node:
                walk(node["item"], [*lineage, node["name"]])
                continue

            if "request" not in node:
                continue

            request = node["request"]
            path = postman_path(request.get("url"))
            results.append(
                {
                    "name": node.get("name", "request"),
                    "folder": " / ".join(lineage),
                    "method": request.get("method", "GET").lower(),
                    "path": path,
                    "normalized_path": normalize_path_template(path),
                    "request": request,
                }
            )

    walk(items, [])
    return results


def build_operation_index(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for path, path_item in (spec.get("paths") or {}).items():
        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue
            index[(method, normalize_path_template(path))] = {
                "path": path,
                "operation": operation,
                "path_item": path_item,
            }
    return index


def rename_security_scheme(spec: dict[str, Any], old: str, new: str) -> None:
    if spec.get("security"):
        spec["security"] = [
            {new if name == old else name: scopes for name, scopes in entry.items()}
            for entry in spec["security"]
        ]

    for path_item in (spec.get("paths") or {}).values():
        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue
            if operation.get("security"):
                operation["security"] = [
                    {new if name == old else name: scopes for name, scopes in entry.items()}
                    for entry in operation["security"]
                ]


def slugify(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip() or "Example"


def unique_example_name(examples: dict[str, Any], preferred_name: str) -> str:
    candidate = slugify(preferred_name)
    if candidate not in examples:
        return candidate

    suffix = 2
    while f"{candidate} {suffix}" in examples:
        suffix += 1
    return f"{candidate} {suffix}"


def canonicalize_example(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def media_contains_example(media: dict[str, Any], example_value: Any) -> bool:
    wanted = canonicalize_example(example_value)

    if media.get("example") is not None and canonicalize_example(media["example"]) == wanted:
        return True

    schema_example = ((media.get("schema") or {}).get("example"))
    if schema_example is not None and canonicalize_example(schema_example) == wanted:
        return True

    for example in (media.get("examples") or {}).values():
        if isinstance(example, dict) and example.get("value") is not None:
            candidate = example["value"]
        else:
            candidate = example
        if canonicalize_example(candidate) == wanted:
            return True

    return False


def operation_has_request_examples(operation: dict[str, Any]) -> bool:
    for media in ((operation.get("requestBody") or {}).get("content") or {}).values():
        if media.get("examples") or media.get("example") or ((media.get("schema") or {}).get("example") is not None):
            return True
    return False


def clear_request_examples(operation: dict[str, Any]) -> None:
    for media in ((operation.get("requestBody") or {}).get("content") or {}).values():
        media.pop("examples", None)
        media.pop("example", None)
        schema = media.get("schema") or {}
        schema.pop("example", None)


def infer_scalar_type(value: Any) -> str:
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return "boolean"
    return "string"


def postman_body_details(request: dict[str, Any]) -> dict[str, Any] | None:
    body = request.get("body") or {}
    mode = body.get("mode")

    if mode == "urlencoded":
        fields: list[dict[str, Any]] = []
        example_value: dict[str, Any] = {}

        for item in body.get("urlencoded") or []:
            key = item.get("key")
            if not key:
                continue

            value = item.get("value")
            fields.append(
                {
                    "name": key,
                    "description": item.get("description"),
                    "example": value,
                    "type": infer_scalar_type(value),
                    "disabled": bool(item.get("disabled")),
                }
            )

            include_in_example = value not in (None, "") and (not item.get("disabled") or value not in (None, ""))
            if include_in_example and key not in example_value:
                example_value[key] = value

        return {
            "media_type": "application/x-www-form-urlencoded",
            "fields": fields,
            "example": example_value,
        }

    if mode == "raw":
        raw = body.get("raw")
        language = (((body.get("options") or {}).get("raw") or {}).get("language") or "").lower()
        media_type = "application/json" if language == "json" else "text/plain"
        if media_type == "application/json":
            try:
                example_value: Any = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                example_value = raw or ""
        else:
            example_value = raw or ""

        return {
            "media_type": media_type,
            "fields": [],
            "example": example_value,
        }

    return None


def ensure_media_content(operation: dict[str, Any], media_type: str) -> dict[str, Any]:
    request_body = operation.setdefault("requestBody", {"content": {}})
    content = request_body.setdefault("content", {})
    media = content.setdefault(media_type, {})

    if media_type == "application/x-www-form-urlencoded":
        schema = media.setdefault("schema", {})
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})

    return media


def merge_property(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    if not target.get("type"):
        target["type"] = incoming["type"]
    elif target.get("type") == "string" and incoming["type"] == "boolean":
        target["type"] = "boolean"

    if incoming.get("description") and not target.get("description"):
        target["description"] = incoming["description"]

    if incoming.get("example") not in (None, "") and "example" not in target:
        target["example"] = incoming["example"]


def merge_local_operation(best_operation: dict[str, Any], local_operation: dict[str, Any]) -> None:
    if local_operation.get("description"):
        best_operation["description"] = local_operation["description"]

    if local_operation.get("operationId"):
        best_operation["operationId"] = local_operation["operationId"]

    local_request_body = local_operation.get("requestBody")
    if not local_request_body:
        return

    best_request_body = best_operation.setdefault("requestBody", {"content": {}})
    best_content = best_request_body.setdefault("content", {})

    for media_type, local_media in (local_request_body.get("content") or {}).items():
        if media_type not in best_content:
            best_content[media_type] = copy.deepcopy(local_media)
            continue

        best_media = best_content[media_type]
        local_examples = local_media.get("examples") or {}
        if local_examples:
            best_examples = best_media.setdefault("examples", {})
            for name, example in local_examples.items():
                if name not in best_examples:
                    best_examples[name] = copy.deepcopy(example)

        local_example = local_media.get("example")
        if local_example is not None and "example" not in best_media:
            best_media["example"] = copy.deepcopy(local_example)

        local_schema = local_media.get("schema") or {}
        best_schema = best_media.setdefault("schema", {})
        if local_schema.get("example") is not None and "example" not in best_schema:
            best_schema["example"] = copy.deepcopy(local_schema["example"])


def merge_collection_request(operation: dict[str, Any], source_request: dict[str, Any], add_examples: bool) -> None:
    details = postman_body_details(source_request["request"])
    if not details or not details["media_type"]:
        return

    media = ensure_media_content(operation, details["media_type"])

    if details["media_type"] == "application/x-www-form-urlencoded":
        schema = media.setdefault("schema", {})
        schema.setdefault("type", "object")
        properties = schema.setdefault("properties", {})
        repeated_fields: set[str] = set()
        seen_fields: set[str] = set()

        for field in details["fields"]:
            if field["name"] in seen_fields:
                repeated_fields.add(field["name"])
            seen_fields.add(field["name"])
            target = properties.setdefault(field["name"], {})
            merge_property(target, field)

        if repeated_fields:
            media["x-postman-repeated-fields"] = sorted(repeated_fields)

    example_value = details["example"]
    if add_examples and example_value not in (None, {}, ""):
        if media_contains_example(media, example_value):
            return
        examples = media.setdefault("examples", {})
        name = unique_example_name(examples, source_request["name"])
        examples[name] = {
            "summary": source_request["name"],
            "value": example_value,
        }


def apply_collision_override(operation: dict[str, Any], key: tuple[str, str]) -> None:
    override = COLLISION_OVERRIDES.get(key)
    if not override:
        return

    operation["summary"] = override["summary"]
    operation["description"] = override["description"]
    operation["operationId"] = override["operationId"]


def request_signature(source_request: dict[str, Any]) -> str:
    request = source_request["request"]
    body = request.get("body") or {}
    raw_options = (body.get("options") or {}).get("raw") or {}

    signature = {
        "auth": request.get("auth") or {},
        "headers": [
            {
                "key": header.get("key"),
                "value": header.get("value"),
                "description": header.get("description"),
                "disabled": bool(header.get("disabled")),
            }
            for header in request.get("header") or []
        ],
        "body": {
            "mode": body.get("mode"),
            "urlencoded": [
                {
                    "key": field.get("key"),
                    "value": field.get("value"),
                    "description": field.get("description"),
                    "disabled": bool(field.get("disabled")),
                    "type": field.get("type"),
                }
                for field in body.get("urlencoded") or []
            ],
            "raw": body.get("raw"),
            "language": raw_options.get("language"),
        },
    }
    return canonicalize_example(signature)


def repeated_field_names(fields: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for field in fields:
        name = field.get("key")
        if not name:
            continue
        if name in seen:
            repeated.add(name)
        seen.add(name)
    return sorted(repeated)


def collision_source_details(source_request: dict[str, Any]) -> dict[str, Any]:
    request = source_request["request"]
    body = request.get("body") or {}
    urlencoded_fields = body.get("urlencoded") or []
    details: dict[str, Any] = {
        "name": source_request["name"],
        "folder": source_request["folder"],
        "path": source_request["path"],
        "body_mode": body.get("mode"),
        "header_names": [header.get("key") for header in request.get("header") or [] if header.get("key")],
    }

    if urlencoded_fields:
        details["body_field_names"] = [field.get("key") for field in urlencoded_fields if field.get("key")]
        details["disabled_body_field_names"] = [
            field.get("key") for field in urlencoded_fields if field.get("key") and field.get("disabled")
        ]
        details["body_repeated_fields"] = repeated_field_names(urlencoded_fields)

    body_details = postman_body_details(request)
    if body_details and body_details["example"] not in (None, {}, ""):
        details["body_example"] = body_details["example"]

    return details


def classify_collision(sources: list[dict[str, Any]]) -> str:
    signatures = {request_signature(source) for source in sources}
    if len(signatures) == 1:
        return "exact_duplicate"
    return "shared_endpoint_multiple_workflows"


def recommended_action(classification: str) -> str:
    if classification == "exact_duplicate":
        return "drop_redundant_request_or_keep_as_detection_fixture"
    return "merge_into_one_operation_and_keep_multiple_examples"


def build_duplicate_report(
    best_operations: dict[tuple[str, str], dict[str, Any]],
    requests_by_operation: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    collisions: list[dict[str, Any]] = []

    for key, sources in requests_by_operation.items():
        if len(sources) < 2:
            continue

        best_entry = best_operations.get(key)
        best_operation = (best_entry or {}).get("operation") or {}
        classification = classify_collision(sources)

        collisions.append(
            {
                "method": key[0].upper(),
                "path": (best_entry or {}).get("path") or sources[0]["path"],
                "normalized_path": key[1],
                "classification": classification,
                "dedupe_safe": classification == "exact_duplicate",
                "recommended_action": recommended_action(classification),
                "source_request_count": len(sources),
                "canonical_summary": best_operation.get("summary"),
                "canonical_operation_id": best_operation.get("operationId"),
                "collision_override_applied": key in COLLISION_OVERRIDES,
                "source_requests": [collision_source_details(source) for source in sources],
            }
        )

    exact_duplicate_count = sum(item["classification"] == "exact_duplicate" for item in collisions)
    shared_endpoint_count = sum(
        item["classification"] == "shared_endpoint_multiple_workflows" for item in collisions
    )

    return {
        "collection": COLLECTION_PATH.name,
        "generated_from": {
            "collection": COLLECTION_PATH.name,
            "local_spec": LOCAL_SPEC_PATH.name,
            "online_spec": ONLINE_SPEC_PATH.name,
        },
        "summary": {
            "collision_count": len(collisions),
            "exact_duplicate_count": exact_duplicate_count,
            "shared_endpoint_multiple_workflows_count": shared_endpoint_count,
        },
        "collisions": collisions,
    }


def main() -> None:
    collection = load_json(COLLECTION_PATH)
    local_spec = load_yaml(LOCAL_SPEC_PATH)
    best_spec = load_yaml(ONLINE_SPEC_PATH)

    collection_requests = flatten_collection(collection["item"])
    requests_by_operation: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for request in collection_requests:
        key = (request["method"], request["normalized_path"])
        requests_by_operation.setdefault(key, []).append(request)

    local_operations = build_operation_index(local_spec)
    best_operations = build_operation_index(best_spec)

    best_spec["openapi"] = local_spec.get("openapi", best_spec.get("openapi", "3.0.3"))
    best_spec["servers"] = extract_servers(collection["info"].get("description", "")) or copy.deepcopy(local_spec.get("servers") or [])
    best_spec["components"] = copy.deepcopy(local_spec.get("components") or {})
    rename_security_scheme(best_spec, "apikeyAuth", "apiKey")
    best_spec["security"] = copy.deepcopy(local_spec.get("security") or [])

    for key, best_entry in best_operations.items():
        best_operation = best_entry["operation"]
        local_entry = local_operations.get(key)
        if local_entry:
            merge_local_operation(best_operation, local_entry["operation"])

        sources = requests_by_operation.get(key, [])
        if len(sources) > 1:
            clear_request_examples(best_operation)

        unique_postman_examples = {
            canonicalize_example(details["example"])
            for source in sources
            for details in [postman_body_details(source["request"])]
            if details and details["example"] not in (None, {}, "")
        }
        add_collection_examples = len(unique_postman_examples) > 1 or not operation_has_request_examples(best_operation)

        for source in sources:
            merge_collection_request(best_operation, source, add_collection_examples)

        if len(sources) > 1:
            best_operation["x-postman-source-requests"] = [
                {
                    "name": source["name"],
                    "folder": source["folder"],
                    "path": source["path"],
                }
                for source in sources
            ]
            best_operation["x-postman-duplicate-request-count"] = len(sources)

        apply_collision_override(best_operation, key)

    duplicate_report = build_duplicate_report(best_operations, requests_by_operation)

    OUTPUT_JSON_PATH.write_text(json.dumps(best_spec, indent=2) + "\n")
    OUTPUT_YAML_PATH.write_text(yaml.safe_dump(best_spec, sort_keys=False, allow_unicode=False))
    OUTPUT_DUPLICATE_REPORT_PATH.write_text(json.dumps(duplicate_report, indent=2) + "\n")


if __name__ == "__main__":
    main()