"""OpenAPI spec parser -- extracts endpoints, parameters, and operation metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Parameter:
    name: str
    location: str  # query, path, header, cookie
    description: str
    required: bool
    schema_type: str


@dataclass
class Operation:
    path: str
    method: str
    operation_id: str | None
    summary: str
    description: str
    parameters: list[Parameter]
    request_body_properties: dict[str, dict[str, Any]]  # property name -> schema fragment
    response_codes: list[str]
    has_request_example: bool
    has_response_example: bool
    tags: list[str]


@dataclass
class ParsedSpec:
    title: str
    version: str
    openapi_version: str
    operations: list[Operation]
    raw: dict[str, Any] = field(repr=False)

    def find_operation(self, path: str, method: str) -> Operation | None:
        method = method.lower()
        for op in self.operations:
            if op.path == path and op.method == method:
                return op
        return None

    def find_by_operation_id(self, operation_id: str) -> Operation | None:
        for op in self.operations:
            if op.operation_id and op.operation_id.lower() == operation_id.lower():
                return op
        return None


def parse_spec(path: str | Path) -> ParsedSpec:
    """Parse an OpenAPI 3.x spec file (YAML or JSON) into structured form."""
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        if path.suffix in (".yaml", ".yml"):
            raw = yaml.safe_load(f)
        else:
            import json
            raw = json.load(f)

    info = raw.get("info", {})
    operations: list[Operation] = []

    for endpoint_path, methods in raw.get("paths", {}).items():
        for method, op_data in methods.items():
            if method in ("parameters", "summary", "description", "$ref"):
                continue
            if not isinstance(op_data, dict):
                continue

            # parse parameters
            params = []
            for p in op_data.get("parameters", []):
                params.append(Parameter(
                    name=p.get("name", ""),
                    location=p.get("in", ""),
                    description=p.get("description", ""),
                    required=p.get("required", False),
                    schema_type=_extract_type(p.get("schema", {})),
                ))

            # parse request body properties
            rb_props: dict[str, dict[str, Any]] = {}
            rb = op_data.get("requestBody", {})
            if rb:
                for _ct, media in rb.get("content", {}).items():
                    schema = media.get("schema", {})
                    for prop_name, prop_schema in schema.get("properties", {}).items():
                        rb_props[prop_name] = prop_schema

            # check for examples
            has_req_example = _has_example_in_request(op_data)
            has_resp_example = _has_example_in_responses(op_data)

            operations.append(Operation(
                path=endpoint_path,
                method=method.lower(),
                operation_id=op_data.get("operationId"),
                summary=op_data.get("summary", ""),
                description=op_data.get("description", ""),
                parameters=params,
                request_body_properties=rb_props,
                response_codes=list(op_data.get("responses", {}).keys()),
                has_request_example=has_req_example,
                has_response_example=has_resp_example,
                tags=op_data.get("tags", []),
            ))

    return ParsedSpec(
        title=info.get("title", ""),
        version=info.get("version", ""),
        openapi_version=raw.get("openapi", ""),
        operations=operations,
        raw=raw,
    )


def _extract_type(schema: dict) -> str:
    """Pull a human-readable type string from a JSON Schema fragment."""
    if "type" in schema:
        return schema["type"]
    if "oneOf" in schema:
        return " | ".join(s.get("type", "?") for s in schema["oneOf"])
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    return "object"


def _has_example_in_request(op: dict) -> bool:
    rb = op.get("requestBody", {})
    for _ct, media in rb.get("content", {}).items():
        if "example" in media or "examples" in media:
            return True
        schema = media.get("schema", {})
        if "example" in schema:
            return True
    return False


def _has_example_in_responses(op: dict) -> bool:
    for _code, resp in op.get("responses", {}).items():
        for _ct, media in resp.get("content", {}).items():
            if "example" in media or "examples" in media:
                return True
            schema = media.get("schema", {})
            if "example" in schema:
                return True
    return False
