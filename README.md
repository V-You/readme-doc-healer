# ReadMe Doc Healer

An MCP server that diagnoses legacy API documentation gaps against an OpenAPI spec, assembles context for the host LLM to generate improved ReadMe-compatible content, and surfaces live quality signals from a ReadMe project – all from the IDE. Uses real data from the ACI Web API.

| `/doc-healer` Analyze the <br>Web API docs for gaps | Heal the endpoint <br>GET /channels/{channelId}  | Run the full diagnose-heal-audit loop <br>on the demo data |
|---|---|---|
| <kbd><img src="img/" alt="" width="" /></kbd>|<kbd><img src="img/" alt="" width="" /></kbd> | <kbd><img src="img/" alt="" width="" /></kbd> |



## The 1,252 problem

The Web API exposes **1,252 configuration options** across two endpoints. Only half have meaningful descriptions. Customer documentation had drifted from the spec. The frontend config manuals weren't connected to the API calls at all.

This tool was built to make that API usable. `diagnose` finds **474 documentation gaps** across 72 operations. `heal` assembles the context so an LLM can write the fix. `audit` checks whether users noticed the improvement.

## Tools

### `diagnose`

Parses an OpenAPI spec and a directory of legacy docs (Confluence HTML exports). Produces a structured gap report: missing descriptions, vague parameters, missing examples, terminology drift, undocumented endpoints.

- **Matching strategies:** path_exact (literal endpoint paths in HTML), filename_fuzzy (operation keywords in filenames), glossary_alias (business term normalization)
- **Vagueness detection:** rule-based heuristics with `needs_llm_review` flags for borderline cases
- **No API key needed** – local files only

### `heal`

Assembles structured context for a specific endpoint so the host LLM can generate documentation. Returns spec fragment, legacy doc snippets (redacted), gap entries, and workflow candidates.

- **Output modes:** `sectioned` (default, for review) or `bundled` (single blob)
- **Workflow detection:** chapter grouping from Confluence index.html, resource clustering from path segments
- **Push mode:** when `push=true`, creates or updates guide pages on ReadMe via the Refactored v2 API. Dry-run by default
- **No in-tool LLM calls** – the host LLM does the writing

### `audit`

Connects to a live ReadMe project and surfaces support-relevant quality signals: worst pages by quality score, zero-result searches, pages with negative feedback.

- **Live mode:** hits the ReadMe Metrics API at `metrics.readme.io` (requires Enterprise plan)
- **Offline mode:** loads canned fixture data for demo purposes
- **Triage-ready output:** markdown report with ranked lists

## MCP Apps

`diagnose` and `audit` render as interactive HTML5 visualizations via `ui://` scheme (`text/html;profile=mcp-app`). When the MCP client doesn't support MCP Apps, tools return plain JSON + markdown.

- **Gap matrix** (`ui://gap-matrix/{spec_path}/{docs_path}`):  
color-coded severity distribution, gap type bars, expandable endpoint details 
- **Audit dashboard** (`ui://audit-dashboard`):  
score gauge, worst pages table, failed searches, negative feedback



## Resources

| URI | Description |
|-----|-------------|
| `glossary://terms` | Business term glossary with aliases for terminology normalization |
| `endpoints://{spec_path}` | Endpoint index parsed from an OpenAPI spec |

## Quick start

```bash
# clone and install
git clone <repo-url>
cd readme-doc-healer
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# configure local defaults in .env
# README_API_KEY=rdme_...     # optional - needed for push mode and live audit
# PROJECT_NAME="ACI"         # display name for the local demo/input project
# PROJECT_DIR="ACI"          # folder name under base_data/

# run the server
readme-doc-healer
```

### MCP client configuration

Add to your MCP client config (VS Code settings, Claude Desktop, etc.):

```json
{
  "servers": {
    "readme-doc-healer": {
      "command": "${workspaceFolder}/.venv/bin/readme-doc-healer",
      "type": "stdio"
    }
  }
}

```

### Example workflow

```
> diagnose

474 gaps found across 72 endpoints.
Critical: 181, Warning: 246, Info: 47

> heal updateMerchantAccount

52 gaps for PUT /merchants/{merchantId}
Spec fragment, legacy snippets, and workflow candidates assembled.

> [LLM generates improved documentation from the context]

> heal --push --branch=1.0 --dry-run=false updateMerchantAccount ...
  content_markdown="# Update merchant account ..."

Guide created at https://doc-healer.readme.io/docs/update-merchant-account

> audit --offline

Triage report: 5 worst pages, 5 zero-result searches, 3 negative feedback pages
```

## Demo data

The server now resolves local demo data from `.env`:

- `PROJECT_NAME` is the display label for the active local project
- `PROJECT_DIR` is the folder name under `base_data/`
- The server prefers `base_data/<PROJECT_DIR>/...`
- If files are not present there yet, it falls back to the legacy flat `base_data/` layout

Preferred layout:

```text
base_data/
  <PROJECT_DIR>/
    <OpenAPI spec file>
    Legacy-Documentation/
    glossary.json
    audit-fixture.json
```

Current bundled demo files still exist in the legacy flat `base_data/` layout:

| File | Description |
|------|-------------|
| `ACI Web API.best.openapi.yaml` | Merged best-of OpenAPI spec (72 operations, 38 paths) |
| `Legacy-Documentation/` | Confluence HTML export (68 files) with `index.html` table of contents |
| `glossary.json` | 25 business terms with aliases, definitions, and context tags |
| `audit-fixture.json` | Canned metrics for offline audit demo |

## Architecture

```
VS Code / IDE
+------------------------------------------+
| MCP Client (Claude, Copilot, etc.)       |
+---+--------------------------------------+
    | MCP protocol (stdio)
+---v--------------------------------------+
| readme-doc-healer (FastMCP)              |
|                                          |
|  +----------+ +------+ +---------+      |
|  | diagnose | | heal | |  audit  |      |
|  +----+-----+ +--+---+ +----+----+      |
|       |          |           |           |
|  +----v----------v--+  +----v----------+ |
|  | Spec + Doc Parser|  | ReadMe API v2 | |
|  | (local files)    |  | + Metrics API | |
|  +------------------+  +--------------+  |
+------------------------------------------+
```

Key decision: `heal` does NOT call an LLM. It assembles context; the host LLM generates the documentation.

## Tech stack

- Python 3.11+, FastMCP 3.1
- `pyyaml` / `jsonref` for OpenAPI spec parsing
- `httpx` for ReadMe API calls
- `beautifulsoup4` / `lxml` for Confluence HTML parsing
- `pydantic-settings` for configuration
- MCP Apps: HTML5 in sandboxed iframe via `ui://` scheme

## Auth

| Surface | Base URL | Auth | Notes |
|---------|----------|------|-------|
| ReadMe API v2 | `api.readme.com/v2` | Bearer | Guides, categories, recipes, search, branches |
| Metrics API | `metrics.readme.io/v2` | Basic (key:) | Page quality, search terms – Enterprise only |

`diagnose` needs no API key. `heal` needs a key only for push mode. `audit` live mode needs an Enterprise-tier key; falls back to fixture otherwise.

## Tests

```bash
pytest                   # 51 tests
pytest -x -q             # quick run, stop on first failure
pytest tests/test_fixtures.py     # matching and parsing tests
pytest tests/test_heal_audit.py   # heal context assembly + audit
```

## Build status

| Step | Status | Description |
|------|--------|-------------|
| 1. Scaffold + diagnose | Done | FastMCP server, gap report, 474 gaps detected |
| 2. Gap report schema | Done | JSON schema data contract |
| 3. Auth spike | Done | v2 API verified: guides, categories, recipes, search, branches |
| 4. Fixture tests | Done | 26 tests covering matching, parsing, glossary, heal, audit |
| 5. Heal (local) | Done | Context assembly with workflow detection |
| 6. Audit | Done | Live metrics + offline fixture fallback |
| 7. Push mode | Done | Create/update guides via v2 API, dry-run default |
| 8. MCP resources | Done | Glossary + endpoint index |
| 9. MCP Apps | Done | Gap matrix + audit dashboard (HTML5, ui:// scheme) |
| 10. README | Done | This file |

## Notes

### Verified against real ACI data (after build steps 1-2, 4)

- 474 gaps across 72 operations (all 72 have at least one gap)
- Match strategy distribution: 189 path_exact, 84 filename_fuzzy, 184 glossary_alias, 17 unmatched
- Only 2 undocumented endpoints (down from 4 after path extraction fix)

### Auth spike findings

- v2 API at `api.readme.com/v2` uses Bearer auth with the API key directly
- Metrics at `metrics.readme.io/v2` use Basic auth, require Enterprise plan
- v1 API at `dash.readme.com/api/v1` is blocked for Git-backed projects
- Project "Doc Healer" is on `business2018` plan, branch "1.0"

### Conversion of multiple web API sources into one "best"

`build_best_openapi.py` generates the merged outputs. It lifts Postman request-body field descriptions into the OpenAPI schema, keeps the stronger structural conversion, restores proper header apiKey auth, adds operationIds, adds both UAT and LIVE servers, and keeps duplicate-source metadata.

### Future consideration

- Standardize filenames inside `base_data/<PROJECT_DIR>/` so the server does not need to heuristically pick the OpenAPI spec file.

---

8C830FBA