---
name: doc-healer
description: Chains diagnose, heal, and audit into a full documentation migration assist session. Use when a user wants to find API doc gaps against an OpenAPI spec, generate improved ReadMe-compatible content, push fixes to a ReadMe project, or audit a live project for quality signals. Also use when the user mentions legacy docs, Confluence exports, gap analysis, doc healing, or ReadMe audit.
argument-hint: "[API specs] or diagnose | heal | audit ..."
---

# Doc healer

Run a diagnose-heal-audit workflow against API documentation. This skill
chains three MCP tools into a repeatable migration-assist loop.

## When to use

- User asks to find gaps between an OpenAPI spec and legacy docs
- User wants to generate or improve endpoint documentation
- User wants to push approved content to a ReadMe project
- User asks to audit a ReadMe project for page quality, search failures, or negative feedback
- User mentions "migrate docs", "doc gaps", "heal endpoint", or "audit ReadMe"

Do NOT use for general OpenAPI validation, non-documentation tasks, or questions
about ReadMe pricing or account management.

## Structural overview

The server exposes three tools and four resources:

| Tool | Purpose | API key needed? |
|------|---------|-----------------|
| `diagnose` | Compare spec vs legacy docs, produce gap report | No (local only) |
| `heal` | Assemble context for one endpoint, optionally push to ReadMe | Only when `push=true` |
| `audit` | Pull live quality signals from a ReadMe project | Yes (falls back to offline fixture) |

Resources: `glossary://terms`, `endpoints://{spec_path}`,
`ui://gap-matrix/{spec_path}/{docs_path}`, `ui://audit-dashboard`.

Local demo data resolves from `.env`:
- `PROJECT_NAME`: display name for the active project
- `PROJECT_DIR`: folder name under `base_data/`
- Preferred layout: `base_data/<PROJECT_DIR>/...`
- Backward compatibility: if project files are missing there, the server falls back to the legacy flat `base_data/` layout

## Workflow

Follow these steps in order. Each step can be run independently, but the full
loop gives the best results.

### Step 1 -- diagnose

Find every gap between the spec and the legacy docs.

```
diagnose()
```

This returns a compact summary (top 10 worst endpoints, totals, config quality).
To get all gap details for every endpoint, call `diagnose(summary_only=false)` --
but note this returns a large payload (~2 MB for ACI) that may be spooled to a
temporary file outside the workspace. Prefer the compact default and use the
gap matrix UI for visual exploration.

Review the gap report. Focus on critical-severity items first.

If the user does not provide paths, the server resolves them from `.env`
using `PROJECT_DIR` and `base_data/<PROJECT_DIR>/`.

### Step 2 -- heal (context assembly)

Pick a specific endpoint from the gap report and assemble context.

```
heal(
    endpoint="GET /channels/{channelId}"
)
```

This returns a context package with: spec fragment, legacy doc snippets,
gap entries, and workflow candidates. Use this context to generate improved
documentation in ReadMe-compatible markdown.

### Step 3 -- review and iterate

Present the generated documentation to the user. Let them review, edit, and
approve. Do not push anything without explicit user approval.

### Step 4 -- heal (push mode)

Once the user approves content, push it to ReadMe.

```
heal(
    endpoint="GET /channels/{channelId}",
    push=true,
    content_markdown="<the approved markdown>",
    dry_run=true
)
```

Always start with `dry_run=true` (the default). Show the user what would be
created or updated. Only set `dry_run=false` after they confirm.

### Step 5 -- audit

Measure whether the fixes improve the project.

```
audit(offline=false)
```

If no API key is available, use `offline=true` to load the demo fixture.
The audit returns page quality scores, zero-result searches, and negative
feedback -- use these to pick the next endpoints to heal.

### Step 6 -- repeat

Go back to step 2 with the next worst endpoint. Continue until the user is
satisfied or coverage targets are met.

## Defaults to use when details are missing

- If the user does not specify a spec path, resolve it from `base_data/<PROJECT_DIR>/` first, then fall back to the legacy flat `base_data/` layout
- If the user does not specify a docs path, resolve it from `base_data/<PROJECT_DIR>/Legacy-Documentation/` first, then fall back to the legacy flat `base_data/` layout
- If the user does not specify a glossary path, resolve `glossary.json` from `base_data/<PROJECT_DIR>/` first, then fall back to the legacy flat `base_data/` layout
- If multiple spec files exist in the project folder, verify the chosen file before relying on it
- If the user does not specify an endpoint, pick the one with the most critical gaps from the last diagnose run
- If the user does not specify a branch, the server defaults to `stable`
- If push mode is requested without content, run heal in local mode first and generate content before pushing
- If no API key is set and audit is requested, fall back to `offline=true`

## Guidance

- Always run diagnose before heal -- the gap report informs which endpoints need work
- Never push content without the user's explicit approval
- Default to `dry_run=true` for push mode -- show the preview first
- When presenting gap reports, group by severity (critical first) and summarize totals
- The glossary resolves terminology drift (e.g., "Contact" means "User" in ACI's domain) -- always include it
- For the audit tool, the ReadMe Metrics API requires an Enterprise plan; on lower plans the server falls back to fixture data automatically
- The MCP Apps (`ui://gap-matrix/...` and `ui://audit-dashboard`) render as HTML5 in supporting clients -- mention them if the user is in VS Code or a client that supports MCP Apps

## Example prompts

- "Analyze the ACI Merchant Onboarding API docs for gaps"
- "Show me the gap matrix for this spec"
- "Heal the GET /channels/{channelId} endpoint"
- "Generate documentation for the worst endpoint"
- "Push the approved docs to ReadMe as a dry run"
- "Audit my ReadMe project for quality issues"
- "What are the top zero-result searches?"
- "Run the full diagnose-heal-audit loop on the demo data"

