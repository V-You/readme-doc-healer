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
| 10. README | Done |  |

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
- "Patch the spec" (backfill description fields in the YAML), requires a "spec healer"
- **How to make the project more useful on a fundamental level.**  
  - OpenAPI specs can be richer, BUT frontend breadcrumb paths need to be kept out of it as they are brittle. So: description, type, enum/allowed values, default, examples.  
  - Separate config endpoints (RiRo values) from transactional endpoints (normal)
  