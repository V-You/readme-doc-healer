"""FastMCP server -- exposes diagnose, heal, and audit as MCP tools."""

from __future__ import annotations

import json

from fastmcp import FastMCP

from .config import get_settings
from .diagnose import run_diagnose

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


def main() -> None:
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
