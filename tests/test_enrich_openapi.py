from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from readme_doc_healer.doc_scanner import DocErrorCode, DocExample, DocParamConstraint, ScannedDoc


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "enrich_openapi.py"
SPEC = importlib.util.spec_from_file_location("enrich_openapi_script", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _mapping() -> dict[str, object]:
    return {
        "normalization": {"collapse_whitespace": True},
        "raw_to_canonical": {
            "[a-f 0-9]": "lower_hex",
            "[A-Z a-z 0-9], [-_ ]": "alnum_dash_underscore_space",
            "true/false": "boolean_like",
            "the URL": "url_like",
            "-": "skip",
        },
        "canonical": {
            "lower_hex": {
                "action": "apply_pattern",
                "confidence": "high",
                "pattern": "^[a-f0-9]*$",
            },
            "alnum_dash_underscore_space": {
                "action": "apply_pattern",
                "confidence": "medium",
                "pattern": "^[A-Za-z0-9 _-]*$",
            },
            "boolean_like": {
                "action": "suggest_boolean",
                "confidence": "medium",
                "preferred_type": "boolean",
                "fallback_string_enum": ["true", "false"],
                "apply_when": {"spec_type_in": ["boolean", "string", "null"]},
            },
            "url_like": {
                "action": "suggest_format",
                "confidence": "medium",
                "suggested_format": "uri",
                "apply_when": {"field_name_matches": "(?i)(url|uri)$"},
            },
            "skip": {
                "action": "skip",
                "confidence": "high",
                "reason": "No usable restriction.",
            },
            "needs_review": {
                "action": "review",
                "confidence": "low",
                "reason": "Observed value is ambiguous and should not be converted automatically.",
            },
        },
    }


def _base_spec() -> dict[str, object]:
    return {
        "openapi": "3.0.0",
        "paths": {
            "/widgets/{widgetId}": {
                "post": {
                    "summary": "Add Widget",
                    "parameters": [
                        {
                            "name": "widgetId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "displayName": {
                                            "type": "string",
                                            "description": "displayName",
                                        },
                                        "enabled": {"type": "string"},
                                        "callbackUrl": {"type": "string"},
                                    },
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Successful response",
                            "content": {"application/json": {}},
                        }
                    },
                }
            }
        },
    }


def _scanned_doc() -> ScannedDoc:
    return ScannedDoc(
        filename="01-Add-Widget.html",
        title="01 Add Widget",
        body_text="",
        endpoint_paths_found=["/widgets/{widgetId}"],
        chapter="01",
        operation_name="Add Widget",
        examples=[
            DocExample(kind="success_response", body='{"widget": {"id": "abc123"}}'),
            DocExample(
                kind="sample_call",
                body="curl -X POST https://api.example.test/widgets/abc123 -d '{\"displayName\": \"Demo widget\"}'",
            ),
        ],
        param_constraints=[
            DocParamConstraint(
                section="url_parameters",
                name="widgetId",
                description="The ID of the widget.",
                character="[a-f 0-9]",
                length="32",
                required="Required",
            ),
            DocParamConstraint(
                section="data_parameters",
                name="displayName",
                description="Human-readable widget name.",
                character="[A-Z a-z 0-9], [-_ ]",
                length="64",
                required="Required",
            ),
            DocParamConstraint(
                section="data_parameters",
                name="enabled",
                description="Whether the widget is enabled.",
                character="true/false",
                length="",
                required="Optional",
            ),
            DocParamConstraint(
                section="data_parameters",
                name="callbackUrl",
                description="The callback URL.",
                character="the URL",
                length="",
                required="Optional",
            ),
        ],
        error_codes=[DocErrorCode(code="404: Not Found", description="Widget not found")],
    )


def test_apply_enrichment_fills_remaining_gap_types():
    spec = _base_spec()

    result = MODULE.apply_enrichment(spec, [_scanned_doc()], _mapping())

    operation = spec["paths"]["/widgets/{widgetId}"]["post"]
    parameter = operation["parameters"][0]
    parameter_schema = parameter["schema"]
    request_media = operation["requestBody"]["content"]["application/json"]
    request_schema = request_media["schema"]
    display_name_schema = request_schema["properties"]["displayName"]
    enabled_schema = request_schema["properties"]["enabled"]
    callback_schema = request_schema["properties"]["callbackUrl"]
    response_media = operation["responses"]["200"]["content"]["application/json"]
    response_404 = operation["responses"]["404"]

    assert parameter_schema["pattern"] == "^[a-f0-9]*$"
    assert parameter_schema["maxLength"] == 32
    assert parameter_schema["description"] == "The ID of the widget."
    assert parameter_schema["x-enriched-from"] == "01-Add-Widget.html"

    assert display_name_schema["pattern"] == "^[A-Za-z0-9 _-]*$"
    assert display_name_schema["maxLength"] == 64
    assert display_name_schema["description"] == "Human-readable widget name."
    assert display_name_schema["x-enriched-from"] == "01-Add-Widget.html"
    assert request_schema["required"] == ["displayName"]
    assert request_schema["x-enriched-from"] == "01-Add-Widget.html"

    assert enabled_schema["enum"] == ["true", "false"]
    assert enabled_schema["description"] == "Whether the widget is enabled."
    assert enabled_schema["x-enriched-from"] == "01-Add-Widget.html"

    assert callback_schema["format"] == "uri"
    assert callback_schema["description"] == "The callback URL."
    assert callback_schema["x-enriched-from"] == "01-Add-Widget.html"

    assert request_media["example"] == {"displayName": "Demo widget"}
    assert request_media["x-enriched-from"] == "01-Add-Widget.html"

    assert response_media["example"] == {"widget": {"id": "abc123"}}
    assert response_media["x-enriched-from"] == "01-Add-Widget.html"

    assert response_404["description"] == "Widget not found"
    assert response_404["x-enriched-from"] == "01-Add-Widget.html"

    assert result["summary"]["response_examples_applied"] == 1
    assert result["summary"]["request_examples_applied"] == 1
    assert result["summary"]["error_responses_applied"] == 1
    assert result["summary"]["review_note_count"] == 0


def test_apply_enrichment_does_not_overwrite_existing_values():
    spec = _base_spec()
    operation = spec["paths"]["/widgets/{widgetId}"]["post"]
    parameter_schema = operation["parameters"][0]["schema"]
    parameter_schema.update(
        {
            "pattern": "^existing$",
            "maxLength": 12,
            "description": "Existing parameter description.",
        }
    )

    request_media = operation["requestBody"]["content"]["application/json"]
    request_schema = request_media["schema"]
    request_schema["required"] = ["displayName"]
    request_schema["properties"]["displayName"].update(
        {
            "pattern": "^present$",
            "maxLength": 12,
            "description": "Existing property description.",
        }
    )
    request_schema["properties"]["enabled"].update(
        {
            "enum": ["Y", "N"],
            "description": "Existing enabled description.",
        }
    )
    request_schema["properties"]["callbackUrl"].update(
        {
            "format": "hostname",
            "description": "Existing URL description.",
        }
    )

    request_media["example"] = {"existing": "request"}
    response_media = operation["responses"]["200"]["content"]["application/json"]
    response_media["example"] = {"existing": "response"}
    operation["responses"]["404"] = {"description": "Existing 404 response"}

    result = MODULE.apply_enrichment(spec, [_scanned_doc()], _mapping())

    assert parameter_schema["pattern"] == "^existing$"
    assert parameter_schema["maxLength"] == 12
    assert parameter_schema["description"] == "Existing parameter description."
    assert "x-enriched-from" not in parameter_schema

    assert request_schema["properties"]["displayName"]["pattern"] == "^present$"
    assert request_schema["properties"]["displayName"]["maxLength"] == 12
    assert request_schema["properties"]["displayName"]["description"] == "Existing property description."
    assert request_schema["properties"]["enabled"]["enum"] == ["Y", "N"]
    assert request_schema["properties"]["callbackUrl"]["format"] == "hostname"
    assert request_media["example"] == {"existing": "request"}
    assert response_media["example"] == {"existing": "response"}
    assert operation["responses"]["404"]["description"] == "Existing 404 response"

    assert result["summary"]["response_examples_applied"] == 0
    assert result["summary"]["request_examples_applied"] == 0
    assert result["summary"]["error_responses_applied"] == 0