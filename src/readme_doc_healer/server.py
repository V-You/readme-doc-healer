"""FastMCP server -- exposes diagnose, heal, and audit as MCP tools."""

from __future__ import annotations

import json

from fastmcp import FastMCP

from .config import get_settings
from .diagnose import run_diagnose
from .heal import run_heal, run_heal_push
from .audit import run_audit
from .glossary import load_glossary
from .spec_parser import parse_spec
from .mcp_apps import render_gap_matrix, render_audit_dashboard

mcp = FastMCP(
    name="readme-doc-healer",
    instructions=(
        "ReadMe Doc Healer -- diagnose legacy API documentation gaps against an "
        "OpenAPI spec, generate improved ReadMe-compatible documentation content, "
        "and surface support-relevant quality signals."
    ),
)


@mcp.tool(
    name="diagnose",
    description=(
        "Parse an OpenAPI spec and a directory of legacy docs (Confluence HTML "
        "exports, markdown, plaintext). Produces a structured gap report identifying "
        "endpoints with missing or vague descriptions, parameters without business "
        "context, missing response examples, undocumented error codes, and "
        "terminology drift between spec and docs. Returns JSON gap report + "
        "markdown summary. No API key needed -- local files only."
    ),
)
def diagnose(
    spec_path: str,
    docs_path: str,
    glossary_path: str | None = None,
) -> str:
    """Run gap analysis on an OpenAPI spec against legacy documentation.

    Args:
        spec_path: Path to OpenAPI JSON/YAML spec file.
        docs_path: Path to directory of legacy doc files (HTML, markdown).
        glossary_path: Optional path to glossary JSON for terminology normalization.
    """
    settings = get_settings()
    report = run_diagnose(
        spec_path=spec_path,
        docs_path=docs_path,
        glossary_path=glossary_path,
        settings=settings,
    )

    # return both structured JSON and markdown summary
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
    spec_path: str,
    docs_path: str,
    glossary_path: str | None = None,
    output_mode: str | None = None,
    push: bool = False,
    content_markdown: str | None = None,
    dry_run: bool = True,
    branch: str | None = None,
) -> str:
    """Assemble context for healing an endpoint's documentation.

    Args:
        endpoint: Endpoint to heal -- "GET /path", "/path", or operationId.
        spec_path: Path to OpenAPI JSON/YAML spec file.
        docs_path: Path to directory of legacy doc files.
        glossary_path: Optional path to glossary JSON.
        output_mode: "sectioned" (default) or "bundled".
        push: If true, publish content to ReadMe (requires content_markdown).
        content_markdown: Required when push=true -- the approved content to publish.
        dry_run: When pushing, preview without writing (default true).
        branch: ReadMe branch to publish to (default from settings).
    """
    if push:
        settings = get_settings()
        if content_markdown is None:
            return json.dumps({"error": "content_markdown is required when push=true."})
        result = run_heal_push(
            endpoint=endpoint,
            content_markdown=content_markdown,
            spec_path=spec_path,
            docs_path=docs_path,
            glossary_path=glossary_path,
            settings=settings,
            branch=branch,
            dry_run=dry_run,
        )
        return json.dumps(result, indent=2, default=str)

    settings = get_settings()
    result = run_heal(
        endpoint=endpoint,
        spec_path=spec_path,
        docs_path=docs_path,
        glossary_path=glossary_path,
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
    glossary = load_glossary(settings.glossary_path)
    terms = []
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
    endpoints = []
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

@mcp.resource("ui://gap-matrix/{spec_path}/{docs_path}")
def gap_matrix_app(spec_path: str, docs_path: str) -> str:
    """Color-coded gap matrix showing severity distribution and expandable endpoint details."""
    settings = get_settings()
    report = run_diagnose(spec_path=spec_path, docs_path=docs_path, settings=settings)
    return render_gap_matrix(report.to_dict())


@mcp.resource("ui://audit-dashboard")
def audit_dashboard_app() -> str:
    """Triage dashboard with score gauges, worst pages, and failed searches."""
    settings = get_settings()
    result = run_audit(offline=True, settings=settings)
    return render_audit_dashboard(result["report"])


def main() -> None:
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
