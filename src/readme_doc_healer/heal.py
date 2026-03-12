"""Heal tool -- assembles structured context for the host LLM to generate documentation.

Local-only mode (push=false):
  Returns a context package with separate sections -- spec fragment, legacy doc
  snippets, gap entries, and workflow candidates -- so the host LLM can generate
  improved ReadMe-compatible documentation.

Push mode (push=true):
  Publishes approved content to a ReadMe project via the Refactored v2 API.
  Requires content_markdown and a valid API key.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .config import Settings, get_settings
from .config_profile import ConfigProfile, is_config_operation, load_config_profile
from .diagnose import run_diagnose
from .doc_scanner import DocExample, DocMatch, DocParamConstraint, DocErrorCode, ScannedDoc, match_docs_to_operation, scan_docs_directory
from .gap_report import Gap, GapReport
from .glossary import Glossary, load_glossary
from .redaction import redact_text
from .spec_parser import Operation, ParsedSpec, parse_spec


_README_API_BASE = "https://api.readme.com/v2"


@dataclass
class WorkflowCandidate:
    """A detected workflow that this endpoint participates in."""
    name: str
    confidence: float
    source: str  # chapter_grouping, cross_link, glossary_tag
    related_endpoints: list[str] = field(default_factory=list)
    source_pages: list[str] = field(default_factory=list)


@dataclass
class HealContext:
    """Structured context package returned by heal in local-only mode."""
    endpoint: str
    method: str
    operation_id: str | None
    spec_fragment: dict[str, Any]
    legacy_doc_snippets: list[dict[str, Any]]
    legacy_examples: list[dict[str, Any]]
    legacy_param_constraints: list[dict[str, Any]]
    legacy_error_codes: list[dict[str, Any]]
    gap_entries: list[dict[str, Any]]
    workflow_candidates: list[dict[str, Any]]
    config_profile: dict[str, Any] | None
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_heal(
    endpoint: str,
    spec_path: str,
    docs_path: str,
    glossary_path: str | None = None,
    settings: Settings | None = None,
    output_mode: str | None = None,
) -> dict[str, Any]:
    """Assemble a context package for the host LLM to generate documentation.

    Returns a structured dict with spec_fragment, legacy_doc_snippets,
    gap_entries, and workflow_candidates sections.
    """
    if settings is None:
        settings = get_settings()

    glossary_path = glossary_path or settings.resolved_glossary_path
    mode = output_mode or settings.heal_mode

    # parse inputs
    spec = parse_spec(spec_path)
    docs = scan_docs_directory(docs_path)
    glossary = load_glossary(glossary_path) if glossary_path else Glossary(entries=[])
    config_profile = load_config_profile(docs_path)

    # find the target operation
    operation = _resolve_endpoint(endpoint, spec)
    if operation is None:
        return {"error": f"Endpoint '{endpoint}' not found in spec. Available paths: {_list_paths(spec)}"}

    # run diagnose to get gap entries for this endpoint
    report = run_diagnose(spec_path, docs_path, glossary_path, settings=settings)
    endpoint_gaps = _filter_gaps_for_endpoint(report, operation)

    # get matching legacy docs
    doc_matches = match_docs_to_operation(operation, docs, glossary)

    # build spec fragment
    spec_fragment = _build_spec_fragment(operation, spec)

    # build legacy doc snippets (with redaction)
    legacy_snippets = _build_legacy_snippets(doc_matches, docs, operation, settings)

    # extract structured examples from matched legacy docs
    legacy_examples = _build_legacy_examples(doc_matches, docs)

    # extract structured parameter constraints and error codes
    legacy_param_constraints = _build_legacy_param_constraints(doc_matches, docs)
    legacy_error_codes = _build_legacy_error_codes(doc_matches, docs)

    # detect workflow candidates
    workflows = _detect_workflows(operation, spec, docs, glossary, docs_path)
    config_context = _build_config_profile_context(operation, config_profile)

    # build summary
    summary: dict[str, Any] = {
        "endpoint": f"{operation.method.upper()} {operation.path}",
        "operation_id": operation.operation_id,
        "total_gaps": len(endpoint_gaps),
        "critical_gaps": sum(1 for g in endpoint_gaps if g["severity"] == "critical"),
        "matched_docs": len(doc_matches),
        "workflow_candidates": len(workflows),
        "legacy_examples_found": len(legacy_examples),
        "legacy_param_constraints_found": len(legacy_param_constraints),
        "legacy_error_codes_found": len(legacy_error_codes),
        "config_profile_enabled": bool(config_context),
    }
    if config_context:
        summary["config_lookup_entries"] = config_context["summary"]["lookup_entry_count"]

    context = HealContext(
        endpoint=operation.path,
        method=operation.method,
        operation_id=operation.operation_id,
        spec_fragment=spec_fragment,
        legacy_doc_snippets=legacy_snippets,
        legacy_examples=legacy_examples,
        legacy_param_constraints=legacy_param_constraints,
        legacy_error_codes=legacy_error_codes,
        gap_entries=endpoint_gaps,
        workflow_candidates=[asdict(w) for w in workflows],
        config_profile=config_context or None,
        summary=summary,
    )

    if mode == "bundled":
        return context.to_dict()

    # sectioned mode (default): return sections separately for review
    return {
        "summary": summary,
        "spec_fragment": spec_fragment,
        "legacy_doc_snippets": legacy_snippets,
        "legacy_examples": legacy_examples,
        "legacy_param_constraints": legacy_param_constraints,
        "legacy_error_codes": legacy_error_codes,
        "gap_entries": endpoint_gaps,
        "workflow_candidates": [asdict(w) for w in workflows],
        "config_profile": config_context or None,
    }


def _build_config_profile_context(operation: Operation, config_profile: ConfigProfile) -> dict[str, Any]:
    """Attach bounded config-profile context for RiRo endpoints."""
    if not is_config_operation(operation):
        return {}
    return config_profile.to_heal_context()


def _resolve_endpoint(endpoint: str, spec: ParsedSpec) -> Operation | None:
    """Resolve an endpoint string to a spec operation.

    Accepts formats:
      - "GET /channels/{channelId}" (method + path)
      - "/channels/{channelId}" (path only, returns first method found)
      - "getChannel" (operationId)
    """
    endpoint = endpoint.strip()

    # try "METHOD /path" format
    parts = endpoint.split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
        method = parts[0].lower()
        path = parts[1]
        op = spec.find_operation(path, method)
        if op:
            return op

    # try path-only (return first matching path)
    if endpoint.startswith("/"):
        for op in spec.operations:
            if op.path.lower() == endpoint.lower():
                return op

    # try operationId
    return spec.find_by_operation_id(endpoint)


def _list_paths(spec: ParsedSpec) -> list[str]:
    """List available paths for error messages."""
    seen: set[str] = set()
    paths: list[str] = []
    for op in spec.operations:
        key = f"{op.method.upper()} {op.path}"
        if key not in seen:
            seen.add(key)
            paths.append(key)
    return sorted(paths)[:20]  # cap at 20 for readability


def _filter_gaps_for_endpoint(report: GapReport, operation: Operation) -> list[dict[str, Any]]:
    """Extract gap entries matching the target endpoint."""
    result: list[dict[str, Any]] = []
    for gap in report.gaps:
        if gap.endpoint == operation.path and gap.method == operation.method:
            result.append(asdict(gap))
    return result


def _build_spec_fragment(operation: Operation, spec: ParsedSpec) -> dict[str, Any]:
    """Extract the raw spec fragment for this endpoint from the parsed spec."""
    path_data = spec.raw.get("paths", {}).get(operation.path, {})
    method_data = path_data.get(operation.method, {})

    fragment: dict[str, Any] = {
        "path": operation.path,
        "method": operation.method,
        "operation_id": operation.operation_id,
        "summary": operation.summary,
        "description": operation.description,
        "tags": operation.tags,
        "parameters": method_data.get("parameters", []),
        "request_body": method_data.get("requestBody"),
        "responses": method_data.get("responses", {}),
    }
    return fragment


def _build_legacy_snippets(
    doc_matches: list[DocMatch],
    docs: list[ScannedDoc],
    operation: Operation,
    settings: Settings,
) -> list[dict[str, Any]]:
    """Build redacted legacy doc snippets from matched docs."""
    snippets = []
    for match in doc_matches:
        # find the full doc text
        full_doc = next((d for d in docs if d.filename == match.doc_source), None)
        body_text = full_doc.body_text if full_doc else ""

        # extract the most relevant section (around the endpoint path mention)
        relevant = _extract_relevant_section(body_text, operation.path)

        # apply redaction
        redacted_text, was_redacted = redact_text(relevant, settings)

        snippets.append({
            "doc_source": match.doc_source,
            "doc_title": match.doc_title,
            "match_strategy": match.strategy,
            "match_confidence": match.confidence,
            "matched_terms": match.matched_terms,
            "snippet": redacted_text,
            "redacted": was_redacted,
        })

    return snippets


def _build_legacy_examples(
    doc_matches: list[DocMatch],
    docs: list[ScannedDoc],
) -> list[dict[str, Any]]:
    """Extract structured examples from matched legacy docs."""
    examples: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for match in doc_matches:
        full_doc = next((d for d in docs if d.filename == match.doc_source), None)
        if not full_doc:
            continue
        for ex in full_doc.examples:
            key = (match.doc_source, ex.kind)
            if key in seen:
                continue
            seen.add(key)
            examples.append({
                "doc_source": match.doc_source,
                "kind": ex.kind,
                "body": ex.body,
            })

    return examples


def _build_legacy_param_constraints(
    doc_matches: list[DocMatch],
    docs: list[ScannedDoc],
) -> list[dict[str, Any]]:
    """Extract structured parameter constraints from matched legacy docs."""
    constraints: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for match in doc_matches:
        full_doc = next((d for d in docs if d.filename == match.doc_source), None)
        if not full_doc:
            continue
        for pc in full_doc.param_constraints:
            key = (match.doc_source, pc.section, pc.name)
            if key in seen:
                continue
            seen.add(key)
            constraints.append({
                "doc_source": match.doc_source,
                "section": pc.section,
                "name": pc.name,
                "description": pc.description,
                "character": pc.character,
                "length": pc.length,
                "required": pc.required,
            })

    return constraints


def _build_legacy_error_codes(
    doc_matches: list[DocMatch],
    docs: list[ScannedDoc],
) -> list[dict[str, Any]]:
    """Extract structured error codes from matched legacy docs."""
    codes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for match in doc_matches:
        full_doc = next((d for d in docs if d.filename == match.doc_source), None)
        if not full_doc:
            continue
        for ec in full_doc.error_codes:
            key = (match.doc_source, ec.code)
            if key in seen:
                continue
            seen.add(key)
            codes.append({
                "doc_source": match.doc_source,
                "code": ec.code,
                "description": ec.description,
            })

    return codes


def _extract_relevant_section(text: str, endpoint_path: str, context_chars: int = 1500) -> str:
    """Extract the section of text most relevant to the endpoint.

    Tries to find the endpoint path in the text and returns surrounding context.
    Falls back to the first N characters if path not found.
    """
    if not text:
        return ""

    # try to find the endpoint path (case-insensitive, ignore param names)
    path_pattern = re.sub(r"\{[^}]+\}", r"\\{[^}]+\\}", re.escape(endpoint_path))
    match = re.search(path_pattern, text, re.IGNORECASE)

    if match:
        start = max(0, match.start() - context_chars // 2)
        end = min(len(text), match.end() + context_chars // 2)
        section = text[start:end]
        if start > 0:
            section = "..." + section
        if end < len(text):
            section = section + "..."
        return section

    # fallback: return first chunk
    if len(text) > context_chars:
        return text[:context_chars] + "..."
    return text


def _detect_workflows(
    operation: Operation,
    spec: ParsedSpec,
    docs: list[ScannedDoc],
    glossary: Glossary,
    docs_path: str,
) -> list[WorkflowCandidate]:
    """Detect workflow candidates for the target endpoint.

    Sources:
    1. Chapter grouping in the Confluence index.html
    2. Cross-links between legacy pages
    3. Endpoint clusters sharing the same resource
    """
    workflows: list[WorkflowCandidate] = []

    # 1. chapter grouping -- endpoints in the same chapter form a CRUD workflow
    chapter_workflow = _detect_chapter_workflow(operation, docs, spec, docs_path)
    if chapter_workflow:
        workflows.append(chapter_workflow)

    # 2. resource cluster -- endpoints sharing the same base path segment
    resource_workflow = _detect_resource_workflow(operation, spec)
    if resource_workflow:
        workflows.append(resource_workflow)

    return workflows


def _detect_chapter_workflow(
    operation: Operation,
    docs: list[ScannedDoc],
    spec: ParsedSpec,
    docs_path: str,
) -> WorkflowCandidate | None:
    """Detect workflow from chapter grouping in index.html."""
    index_path = Path(docs_path) / "index.html"
    if not index_path.exists():
        return None

    with open(index_path, encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f.read(), "lxml")

    # build chapter -> child pages map from the ToC tree
    chapters = _parse_index_chapters(soup)

    # match our operation to legacy docs (without glossary for direct matching)
    op_matches = match_docs_to_operation(operation, docs, Glossary(entries=[]))
    matched_filenames = {m.doc_source for m in op_matches}

    # find which chapter contains this doc
    target_chapter = None
    for ch_name, child_hrefs in chapters.items():
        for href in child_hrefs:
            if href in matched_filenames:
                target_chapter = ch_name
                break
        if target_chapter:
            break

    if not target_chapter:
        return None

    # get sibling pages in the same chapter
    chapter_pages = chapters[target_chapter]
    if len(chapter_pages) < 2:
        return None

    # find related spec endpoints from sibling docs
    related = []
    for page_href in chapter_pages:
        sibling_docs = [d for d in docs if d.filename == page_href]
        for doc in sibling_docs:
            for op2 in spec.operations:
                m2 = match_docs_to_operation(op2, [doc], Glossary(entries=[]))
                if m2 and (op2.path != operation.path or op2.method != operation.method):
                    ep = f"{op2.method.upper()} {op2.path}"
                    if ep not in related:
                        related.append(ep)
                    break

    return WorkflowCandidate(
        name=target_chapter,
        confidence=0.7,
        source="chapter_grouping",
        related_endpoints=related[:10],
        source_pages=chapter_pages[:10],
    )


def _detect_resource_workflow(
    operation: Operation,
    spec: ParsedSpec,
) -> WorkflowCandidate | None:
    """Detect workflow from endpoints sharing the same base resource path."""
    # extract the resource segment (e.g. /channels from /channels/{channelId})
    segments = [s for s in operation.path.split("/") if s and not s.startswith("{")]
    if not segments:
        return None

    # use the last literal segment as the resource name
    resource = segments[-1]

    # find all operations on the same resource
    related = []
    for op in spec.operations:
        op_segments = [s for s in op.path.split("/") if s and not s.startswith("{")]
        if resource in op_segments and (op.path != operation.path or op.method != operation.method):
            related.append(f"{op.method.upper()} {op.path}")

    if not related:
        return None

    return WorkflowCandidate(
        name=f"{resource} CRUD operations",
        confidence=0.6,
        source="resource_cluster",
        related_endpoints=related[:15],
        source_pages=[],
    )


def _parse_index_chapters(soup: BeautifulSoup) -> dict[str, list[str]]:
    """Parse index.html ToC tree to extract chapter_name -> [child page hrefs].

    The Confluence export uses one <ul><li> per child page, all nested under
    the chapter <li>. So a chapter like "06 Channel-level operations" has
    multiple sibling <ul> children, each containing one <li><a> link.
    """
    chapters: dict[str, list[str]] = {}

    for li in soup.find_all("li"):
        a = li.find("a", recursive=False)
        if not a:
            continue

        text = a.get_text(strip=True)
        ch_match = re.match(r"^(\d{2})\s+(.+)$", text)
        if not ch_match:
            continue

        chapter_name = ch_match.group(2).strip()

        # collect all <a> links in nested <ul> elements
        child_hrefs = []
        for child_ul in li.find_all("ul", recursive=False):
            for child_a in child_ul.find_all("a"):
                href = child_a.get("href", "")
                if href and href != "#":
                    child_hrefs.append(href)

        if child_hrefs:
            chapters[chapter_name] = child_hrefs

    return chapters


def _chapter_name_from_index(chapters: dict[str, list[str]], chapter_num: str) -> str | None:
    """Look up chapter name by number -- kept for backward compat."""
    _CHAPTER_NAMES = {
        "02": "Contact operations",
        "03": "Clearing Institute operations",
        "04": "Division-level operations",
        "05": "Merchant-level operations",
        "06": "Channel-level operations",
        "07": "Merchant Account operations",
        "08": "RiRo settings operations",
        "09": "API Token operations",
    }
    return _CHAPTER_NAMES.get(chapter_num)


# --- push mode ---


def run_heal_push(
    endpoint: str,
    content_markdown: str,
    spec_path: str,
    docs_path: str,
    glossary_path: str | None = None,
    settings: Settings | None = None,
    branch: str | None = None,
    dry_run: bool = True,
    slug: str | None = None,
) -> dict[str, Any]:
    """Publish approved content to ReadMe via the Refactored v2 API.

    The flow: resolve endpoint -> derive slug -> check if guide exists ->
    create or update. Dry-run (default) previews without writing.

    Args:
        slug: Optional slug override. When set, uses this instead of auto-deriving
              from the operationId. Useful when a previously deleted page still
              occupies the auto-derived slug on ReadMe.
    """
    if settings is None:
        settings = get_settings()

    api_key = settings.readme_api_key
    if not api_key:
        return {"error": "No API key configured. Set README_API_KEY in .env or pass readme_api_key."}

    if not content_markdown or not content_markdown.strip():
        return {"error": "content_markdown is required for push mode."}

    branch = branch or settings.readme_branch
    spec = parse_spec(spec_path)
    operation = _resolve_endpoint(endpoint, spec)
    if operation is None:
        return {"error": f"Endpoint '{endpoint}' not found in spec."}

    # derive slug from operationId or path (unless overridden)
    if not slug:
        slug = _derive_slug(operation)
    category_title = _derive_category(operation, docs_path)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    # resolve the category URI from the project
    category_uri = _resolve_category_uri(branch, category_title, headers, settings)

    # check if the guide already exists
    existing = _get_guide(branch, slug, headers)

    payload = {
        "title": _derive_title(operation),
        "slug": slug,
        "content": {"body": content_markdown},
        "category": {"uri": category_uri},
    }

    if dry_run:
        return {
            "dry_run": True,
            "action": "update" if existing else "create",
            "branch": branch,
            "slug": slug,
            "category": category_title,
            "category_uri": category_uri,
            "title": payload["title"],
            "content_length": len(content_markdown),
            "payload_preview": payload,
        }

    # execute the write
    if existing:
        result = _update_guide(branch, slug, payload, headers)
    else:
        result = _create_guide(branch, payload, headers)

    return result


def _derive_slug(operation: Operation) -> str:
    """Derive a ReadMe slug from the operation."""
    if operation.operation_id:
        # kebab-case from camelCase
        slug = re.sub(r"([a-z])([A-Z])", r"\1-\2", operation.operation_id)
        return slug.lower().strip("-")
    # fallback: method-path
    path_part = operation.path.replace("/", "-").replace("{", "").replace("}", "")
    return f"{operation.method}-{path_part}".lower().strip("-")


def _derive_title(operation: Operation) -> str:
    """Derive a page title from the operation."""
    if operation.summary:
        return operation.summary
    if operation.operation_id:
        # "getChannel" -> "Get channel"
        spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", operation.operation_id)
        return spaced.capitalize()
    return f"{operation.method.upper()} {operation.path}"


def _derive_category(operation: Operation, docs_path: str) -> str:
    """Derive a category title from the Confluence index.html structure."""
    index_path = Path(docs_path) / "index.html"
    if not index_path.exists():
        return "API Documentation"

    with open(index_path, encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f.read(), "lxml")

    chapters = _parse_index_chapters(soup)

    # try to match the endpoint to a chapter
    for ch_name, child_hrefs in chapters.items():
        for href in child_hrefs:
            href_lower = href.lower()
            if operation.operation_id and operation.operation_id.lower() in href_lower:
                return ch_name
            path_terms = [s for s in operation.path.split("/") if s and not s.startswith("{")]
            if any(t.lower() in href_lower for t in path_terms):
                return ch_name

    return "API Documentation"


def _get_guide(branch: str, slug: str, headers: dict[str, str]) -> dict | None:
    """Try to fetch an existing guide by slug. Returns None if not found."""
    try:
        resp = httpx.get(
            f"{_README_API_BASE}/branches/{branch}/guides/{slug}",
            headers=headers,
            timeout=15.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except httpx.HTTPError:
        pass
    return None


def _resolve_category_uri(
    branch: str,
    category_title: str,
    headers: dict[str, str],
    settings: Settings,
) -> str:
    """Resolve a category title to its v2 URI.

    Checks existing guides categories first. If not found and
    allow_category_create is true, creates it. Otherwise falls back
    to the first existing category.
    """
    try:
        resp = httpx.get(
            f"{_README_API_BASE}/branches/{branch}/categories/guides",
            headers=headers,
            timeout=15.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            categories = data.get("data", [])

            # exact match
            for cat in categories:
                if cat.get("title", "").lower() == category_title.lower():
                    return cat["uri"]

            # partial match
            for cat in categories:
                if category_title.lower() in cat.get("title", "").lower():
                    return cat["uri"]

            # fallback: use the first category
            if categories:
                return categories[0]["uri"]
    except httpx.HTTPError:
        pass

    # construct a best-guess URI if API call failed
    return f"/branches/{branch}/categories/guides/{category_title}"


def _create_guide(branch: str, payload: dict, headers: dict[str, str]) -> dict[str, Any]:
    """Create a new guide page via the v2 API."""
    try:
        resp = httpx.post(
            f"{_README_API_BASE}/branches/{branch}/guides",
            headers=headers,
            json=payload,
            timeout=15.0,
        )
        return {
            "action": "create",
            "status": resp.status_code,
            "response": resp.json() if resp.status_code < 400 else resp.text[:500],
            "success": 200 <= resp.status_code < 300,
        }
    except httpx.HTTPError as e:
        return {"action": "create", "error": str(e), "success": False}


def _update_guide(branch: str, slug: str, payload: dict, headers: dict[str, str]) -> dict[str, Any]:
    """Update an existing guide page via the v2 API."""
    try:
        resp = httpx.patch(
            f"{_README_API_BASE}/branches/{branch}/guides/{slug}",
            headers=headers,
            json=payload,
            timeout=15.0,
        )
        return {
            "action": "update",
            "status": resp.status_code,
            "response": resp.json() if resp.status_code < 400 else resp.text[:500],
            "success": 200 <= resp.status_code < 300,
        }
    except httpx.HTTPError as e:
        return {"action": "update", "error": str(e), "success": False}
