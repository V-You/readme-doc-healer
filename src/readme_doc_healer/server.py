"""FastMCP server -- exposes diagnose, heal, and audit as MCP tools."""

from __future__ import annotations

import json
from dataclasses import asdict

from fastmcp import FastMCP
from fastmcp.server.apps import AppConfig, ResourceCSP

from .config import Settings, get_settings
from .diagnose import run_diagnose
from .heal import run_heal, run_heal_push
from .audit import run_audit
from .glossary import load_glossary
from .spec_parser import parse_spec
from .mcp_apps import (
    render_gap_matrix,
    render_audit_dashboard,
    gap_matrix_template,
    audit_dashboard_template,
)

mcp = FastMCP(
    name="readme-doc-healer",
    instructions=(
        "ReadMe Doc Healer -- diagnose legacy API documentation gaps against an "
        "OpenAPI spec, generate improved ReadMe-compatible documentation content, "
        "and surface support-relevant quality signals."
    ),
)


def _resolve_local_inputs(
    settings: Settings,
    spec_path: str | None,
    docs_path: str | None,
    glossary_path: str | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Resolve local file inputs from explicit args or project-scoped defaults."""
    resolved_spec_path = spec_path or settings.resolved_spec_path
    resolved_docs_path = docs_path or settings.resolved_docs_path
    resolved_glossary_path = glossary_path or settings.resolved_glossary_path

    missing: list[str] = []
    if not resolved_spec_path:
        missing.append("spec_path")
    if not resolved_docs_path:
        missing.append("docs_path")

    if not missing:
        return resolved_spec_path, resolved_docs_path, resolved_glossary_path, None

    project_dir = str(settings.project_data_dir) if settings.project_data_dir else None
    error = {
        "error": f"Could not resolve {', '.join(missing)} from tool args or .env defaults.",
        "project_dir": project_dir,
        "search_roots": [str(root) for root in settings.data_search_roots],
        "hint": "Set spec_path/docs_path explicitly, or add project files under base_data/<PROJECT_DIR>/.",
    }
    return None, None, None, json.dumps(error, indent=2)


@mcp.tool(
    name="diagnose",
    description=(
        "Parse an OpenAPI spec and a directory of legacy docs (Confluence HTML "
        "exports, markdown, plaintext). Produces a structured gap report identifying "
        "endpoints with missing or vague descriptions, parameters without business "
        "context, missing response examples, undocumented error codes, and "
        "terminology drift between spec and docs. "
        "By default returns a compact summary (summary_only=true) with totals, "
        "config quality metrics, and the 10 worst endpoints -- keeping the response "
        "small for agent consumption. Pass summary_only=false to include all gaps. "
        "No API key needed -- local files only."
    ),
    app=AppConfig(resourceUri="ui://doc-healer/gap-matrix.html"),
)
def diagnose(
    spec_path: str | None = None,
    docs_path: str | None = None,
    glossary_path: str | None = None,
    summary_only: bool = True,
) -> str:
    """Run gap analysis on an OpenAPI spec against legacy documentation.

    Args:
        spec_path: Optional path to OpenAPI JSON/YAML spec file.
        docs_path: Optional path to directory of legacy doc files (HTML, markdown).
        glossary_path: Optional path to glossary JSON for terminology normalization.
        summary_only: If true (default), return only summary + config_quality +
            top 10 worst endpoints. Set false to include all gap details.
    """
    settings = get_settings()
    resolved_spec_path, resolved_docs_path, resolved_glossary_path, error = _resolve_local_inputs(
        settings,
        spec_path,
        docs_path,
        glossary_path,
    )
    if error:
        return error

    assert resolved_spec_path is not None
    assert resolved_docs_path is not None

    report = run_diagnose(
        spec_path=resolved_spec_path,
        docs_path=resolved_docs_path,
        glossary_path=resolved_glossary_path,
        settings=settings,
    )

    if summary_only:
        # group gaps by endpoint, find worst 10 by (critical count, total count)
        by_ep: dict[str, list[dict]] = {}
        for gap in report.gaps:
            key = f"{gap.method.upper()} {gap.endpoint}"
            by_ep.setdefault(key, []).append({
                "severity": gap.severity,
                "gap_type": gap.gap_type,
                "message": gap.message,
                "parameter": gap.parameter,
            })
        worst = sorted(
            by_ep.items(),
            key=lambda x: (
                -sum(1 for g in x[1] if g["severity"] == "critical"),
                -len(x[1]),
            ),
        )[:10]

        result = {
            "summary": asdict(report.summary),
            "config_quality": asdict(report.config_quality),
            "worst_endpoints": [
                {
                    "endpoint": ep,
                    "critical": sum(1 for g in gaps if g["severity"] == "critical"),
                    "warning": sum(1 for g in gaps if g["severity"] == "warning"),
                    "info": sum(1 for g in gaps if g["severity"] == "info"),
                    "total": len(gaps),
                    "gaps": gaps,
                }
                for ep, gaps in worst
            ],
            "markdown": report.to_markdown().split("## Gaps by endpoint")[0].strip(),
            "hint": (
                f"Showing top 10 of {len(by_ep)} endpoints. "
                "Call diagnose(summary_only=false) for all gap details."
            ),
        }
    else:
        result = {
            "report": report.to_dict(),
            "markdown": report.to_markdown(),
        }

    return json.dumps(result, indent=2, default=str)


@mcp.tool(
    name="heal",
    description=(
        "Assemble structured context for a specific endpoint so the host LLM can "
        "generate improved ReadMe-compatible documentation. Returns a context "
        "package with spec fragment, legacy doc snippets, gap entries, and workflow "
        "candidates. When push=false (default), no API key needed -- local files "
        "only. When push=true, publishes to ReadMe via the Refactored v2 API."
    ),
)
def heal(
    endpoint: str,
    spec_path: str | None = None,
    docs_path: str | None = None,
    glossary_path: str | None = None,
    output_mode: str | None = None,
    push: bool = False,
    content_markdown: str | None = None,
    dry_run: bool = True,
    branch: str | None = None,
    slug: str | None = None,
) -> str:
    """Assemble context for healing an endpoint's documentation.

    Args:
        endpoint: Endpoint to heal -- "GET /path", "/path", or operationId.
        spec_path: Optional path to OpenAPI JSON/YAML spec file.
        docs_path: Optional path to directory of legacy doc files.
        glossary_path: Optional path to glossary JSON.
        output_mode: "sectioned" (default) or "bundled".
        push: If true, publish content to ReadMe (requires content_markdown).
        content_markdown: Required when push=true -- the approved content to publish.
        dry_run: When pushing, preview without writing (default true).
        branch: ReadMe branch to publish to (default from settings).
        slug: Optional slug override for the ReadMe page URL.
    """
    settings = get_settings()
    resolved_spec_path, resolved_docs_path, resolved_glossary_path, error = _resolve_local_inputs(
        settings,
        spec_path,
        docs_path,
        glossary_path,
    )
    if error:
        return error

    assert resolved_spec_path is not None
    assert resolved_docs_path is not None

    if push:
        if content_markdown is None:
            return json.dumps({"error": "content_markdown is required when push=true."})
        result = run_heal_push(
            endpoint=endpoint,
            content_markdown=content_markdown,
            spec_path=resolved_spec_path,
            docs_path=resolved_docs_path,
            glossary_path=resolved_glossary_path,
            settings=settings,
            branch=branch,
            dry_run=dry_run,
            slug=slug,
        )
        return json.dumps(result, indent=2, default=str)

    result = run_heal(
        endpoint=endpoint,
        spec_path=resolved_spec_path,
        docs_path=resolved_docs_path,
        glossary_path=resolved_glossary_path,
        settings=settings,
        output_mode=output_mode,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool(
    name="audit",
    description=(
        "Connect to a live ReadMe project and surface support-relevant quality "
        "signals: lowest-scoring pages, top search terms with no results, pages "
        "with negative user feedback. Produces a triage report an SE can act on "
        "immediately. When offline=true or no API key, loads a canned fixture "
        "for demo purposes."
    ),
    app=AppConfig(resourceUri="ui://doc-healer/audit-dashboard.html"),
)
def audit(
    readme_api_key: str | None = None,
    offline: bool = False,
) -> str:
    """Audit a ReadMe project for documentation quality signals.

    Args:
        readme_api_key: ReadMe API key. Falls back to .env README_API_KEY.
        offline: If true, use canned fixture data instead of live API.
    """
    settings = get_settings()
    result = run_audit(
        readme_api_key=readme_api_key,
        offline=offline,
        settings=settings,
    )
    return json.dumps(result, indent=2, default=str)


# --- MCP resources ---

@mcp.resource("glossary://terms")
def glossary_resource() -> str:
    """Business term glossary with aliases -- used for terminology normalization."""
    settings = get_settings()
    glossary_path = settings.resolved_glossary_path
    if not glossary_path:
        return json.dumps({"terms": [], "count": 0}, indent=2)

    glossary = load_glossary(glossary_path)
    terms: list[dict[str, object]] = []
    for entry in glossary.entries:
        terms.append({
            "term": entry.term,
            "aliases": entry.aliases,
            "definition": entry.definition,
            "context": entry.context,
        })
    return json.dumps({"terms": terms, "count": len(terms)}, indent=2)


@mcp.resource("endpoints://{spec_path}")
def endpoint_index_resource(spec_path: str) -> str:
    """Endpoint index parsed from an OpenAPI spec."""
    spec = parse_spec(spec_path)
    endpoints: list[dict[str, object]] = []
    for op in spec.operations:
        endpoints.append({
            "method": op.method.upper(),
            "path": op.path,
            "operation_id": op.operation_id,
            "summary": op.summary,
            "tags": op.tags,
        })
    return json.dumps({
        "title": spec.title,
        "version": spec.version,
        "openapi_version": spec.openapi_version,
        "endpoints": endpoints,
        "count": len(endpoints),
    }, indent=2)


# --- MCP Apps (ui:// scheme) ---

_APPS_CSP = ResourceCSP(
    resourceDomains=["https://unpkg.com"],
    connectDomains=["https://unpkg.com"],
)


@mcp.resource(
    "ui://doc-healer/gap-matrix.html",
    app=AppConfig(csp=_APPS_CSP),
)
def gap_matrix_app_template() -> str:
    """JS-driven gap matrix -- receives data from the diagnose tool via postMessage."""
    return gap_matrix_template()


@mcp.resource(
    "ui://doc-healer/audit-dashboard.html",
    app=AppConfig(csp=_APPS_CSP),
)
def audit_dashboard_app_template() -> str:
    """JS-driven audit dashboard -- receives data from the audit tool via postMessage."""
    return audit_dashboard_template()


# --- Legacy server-rendered MCP Apps (standalone) ---

@mcp.resource("ui://gap-matrix/{spec_path}/{docs_path}")
def gap_matrix_app(spec_path: str, docs_path: str) -> str:
    """Server-rendered gap matrix with data baked in (standalone use)."""
    settings = get_settings()
    report = run_diagnose(spec_path=spec_path, docs_path=docs_path, settings=settings)
    return render_gap_matrix(report.to_dict())


@mcp.resource("ui://audit-dashboard")
def audit_dashboard_app() -> str:
    """Server-rendered audit dashboard with fixture data baked in (standalone use)."""
    settings = get_settings()
    result = run_audit(offline=True, settings=settings)
    return render_audit_dashboard(result["report"])


def main() -> None:
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
