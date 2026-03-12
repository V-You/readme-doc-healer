"""Legacy doc scanner -- matches Confluence HTML exports to OpenAPI endpoints."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup

from .glossary import Glossary
from .spec_parser import Operation


@dataclass
class DocMatch:
    """A legacy doc file matched to a spec endpoint."""
    doc_source: str
    doc_title: str
    confidence: float
    strategy: str  # path_exact, filename_fuzzy, heading_fuzzy, glossary_alias
    matched_terms: list[str] = field(default_factory=list)
    snippet: str = ""


@dataclass
class DocExample:
    """An example block extracted from a legacy doc."""
    kind: str  # success_response, error_response, sample_call
    body: str  # raw text of the example (JSON, curl, etc.)


@dataclass
class DocParamConstraint:
    """A parameter constraint extracted from a legacy doc table."""
    section: str  # url_parameters, data_parameters, success_response, error_response
    name: str
    description: str
    character: str  # allowed character pattern, e.g. "[a-f 0-9]"
    length: str  # max length, e.g. "32"
    required: str  # "Required", "Optional", or ""


@dataclass
class DocErrorCode:
    """An error code entry from a legacy doc error response table."""
    code: str  # e.g. "200.*.*"
    description: str


@dataclass
class ScannedDoc:
    """A single parsed legacy doc file."""
    filename: str
    title: str
    body_text: str
    endpoint_paths_found: list[str]  # literal paths found in the text
    chapter: str
    operation_name: str  # extracted from filename
    examples: list[DocExample] = field(default_factory=list)
    param_constraints: list[DocParamConstraint] = field(default_factory=list)
    error_codes: list[DocErrorCode] = field(default_factory=list)


def scan_docs_directory(docs_path: str | Path) -> list[ScannedDoc]:
    """Scan a directory of legacy doc HTML files and extract content."""
    docs_path = Path(docs_path)
    if not docs_path.is_dir():
        return []

    docs = []
    for html_file in sorted(docs_path.glob("*.html")):
        if html_file.name == "index.html":
            continue
        doc = _parse_html_doc(html_file)
        if doc:
            docs.append(doc)
    return docs


def match_docs_to_operation(
    operation: Operation,
    docs: list[ScannedDoc],
    glossary: Glossary,
) -> list[DocMatch]:
    """Two-pass matching: exact path, then fuzzy filename/heading + glossary aliases."""
    matches: list[DocMatch] = []
    seen_files: set[str] = set()

    # pass 1: exact path match -- look for the endpoint path in the doc body
    for doc in docs:
        for found_path in doc.endpoint_paths_found:
            if _paths_match(operation.path, found_path):
                if doc.filename not in seen_files:
                    matches.append(DocMatch(
                        doc_source=doc.filename,
                        doc_title=doc.title,
                        confidence=1.0,
                        strategy="path_exact",
                        matched_terms=[found_path],
                        snippet=_extract_snippet(doc.body_text, found_path),
                    ))
                    seen_files.add(doc.filename)

    # pass 2: fuzzy match -- compare operation ID / summary against filenames and titles
    fuzzy_matches = _fuzzy_match(operation, docs, seen_files)
    matches.extend(fuzzy_matches)
    seen_files.update(m.doc_source for m in fuzzy_matches)

    # pass 3: glossary alias match -- expand operation terms via glossary
    glossary_matches = _glossary_match(operation, docs, glossary, seen_files)
    matches.extend(glossary_matches)

    # sort by confidence descending
    matches.sort(key=lambda m: m.confidence, reverse=True)
    return matches


def _parse_html_doc(path: Path) -> ScannedDoc | None:
    """Parse a Confluence HTML export file."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            soup = BeautifulSoup(f.read(), "lxml")
    except Exception:
        return None

    # extract title
    title_el = soup.find("title")
    title = title_el.get_text(strip=True) if title_el else path.stem
    # strip common prefix
    title = re.sub(r"^Documentation\s*:\s*", "", title)

    # extract body text -- use space separator for readable text
    main_content = soup.find(id="main-content") or soup.find("body")
    body_text = main_content.get_text(separator=" ", strip=True) if main_content else ""

    # for path extraction, keep block boundaries to avoid glueing labels
    # into endpoints (e.g. ownedContacts + Option in Confluence tables).
    # we normalize inline spacing in _find_endpoint_paths.
    raw_text = main_content.get_text(separator="\n", strip=True) if main_content else ""

    # find endpoint paths from both text versions
    endpoint_paths = _find_endpoint_paths(raw_text)
    # also check the spaced version for paths that don't cross tag boundaries
    for p in _find_endpoint_paths(body_text):
        if p not in endpoint_paths:
            endpoint_paths.append(p)

    # parse filename convention: NN-Operation-Name_PageID.html
    chapter, operation_name = _parse_filename(path.name)

    # extract structured example blocks (success response, error response, sample call)
    examples = _extract_examples_from_soup(main_content)

    # extract structured parameter constraints and error codes from inner tables
    param_constraints, error_codes = _extract_tables_from_soup(main_content)

    return ScannedDoc(
        filename=path.name,
        title=title,
        body_text=body_text,
        endpoint_paths_found=endpoint_paths,
        chapter=chapter,
        operation_name=operation_name,
        examples=examples,
        param_constraints=param_constraints,
        error_codes=error_codes,
    )


def _extract_examples_from_soup(main_content) -> list[DocExample]:
    """Extract structured example blocks from legacy doc HTML.

    Looks for table rows (<tr>) headed by "Success response", "Error response",
    or "Sample call" -- the pattern used in ACI Confluence exports. Within each
    row, finds the "Example" label and collects all subsequent text.
    """
    if main_content is None:
        return []

    examples: list[DocExample] = []

    _SECTION_MARKERS = {
        "success response": "success_response",
        "error response": "error_response",
        "sample call": "sample_call",
    }

    for th in main_content.find_all("th"):
        heading = th.get_text(strip=True).lower()
        kind = _SECTION_MARKERS.get(heading)
        if not kind:
            continue

        tr = th.parent
        if not tr:
            continue

        # get the row text with newline separators so we can find "Example"
        full_text = tr.get_text(separator="\n", strip=True)
        lines = full_text.split("\n")

        # scan for a line that is exactly "Example"
        for i, line in enumerate(lines):
            if line.strip().lower() == "example":
                body = "\n".join(lines[i + 1:]).strip()
                if body:
                    examples.append(DocExample(kind=kind, body=body))
                break

    return examples


def _extract_tables_from_soup(
    main_content,
) -> tuple[list[DocParamConstraint], list[DocErrorCode]]:
    """Extract structured parameter constraints and error codes from inner tables.

    ACI Confluence docs nest detail tables inside main table rows. Each outer
    <th> marks a section (URL parameters, Data parameters, Success response,
    Error response). Inner tables contain columns like Name, Character, Length
    or Response code, Description.
    """
    if main_content is None:
        return [], []

    constraints: list[DocParamConstraint] = []
    error_codes: list[DocErrorCode] = []

    _SECTION_MAP = {
        "url parameters": "url_parameters",
        "data parameters": "data_parameters",
        "success response": "success_response",
        "error response": "error_response",
    }

    for th in main_content.find_all("th"):
        heading = th.get_text(strip=True).lower()
        section_key = _SECTION_MAP.get(heading)
        if not section_key:
            continue

        tr = th.parent
        if not tr:
            continue
        td = tr.find("td")
        if not td:
            continue

        # iterate inner tables within this section
        for inner_table in td.find_all("table"):
            headers = [
                h.get_text(strip=True).lower()
                for h in inner_table.find_all("th")
            ]
            if not headers:
                continue

            # error code table (Response code | Description)
            if "response code" in headers:
                code_idx = headers.index("response code")
                desc_idx = headers.index("description") if "description" in headers else None
                for row in inner_table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) <= code_idx:
                        continue
                    code = cells[code_idx].get_text(strip=True)
                    desc = cells[desc_idx].get_text(strip=True) if desc_idx is not None and len(cells) > desc_idx else ""
                    if code:
                        error_codes.append(DocErrorCode(code=code, description=desc))
                continue

            # parameter constraint table (Name | Description | Character | Length [| Required])
            if "name" in headers and "character" in headers:
                name_idx = headers.index("name")
                desc_idx = headers.index("description") if "description" in headers else None
                char_idx = headers.index("character")
                len_idx = headers.index("length") if "length" in headers else None
                req_idx = headers.index("required") if "required" in headers else None

                for row in inner_table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) <= name_idx:
                        continue
                    name = cells[name_idx].get_text(strip=True)
                    if not name:
                        continue
                    desc = cells[desc_idx].get_text(strip=True) if desc_idx is not None and len(cells) > desc_idx else ""
                    char = cells[char_idx].get_text(strip=True) if len(cells) > char_idx else ""
                    length = cells[len_idx].get_text(strip=True) if len_idx is not None and len(cells) > len_idx else ""
                    required = cells[req_idx].get_text(strip=True) if req_idx is not None and len(cells) > req_idx else ""

                    constraints.append(DocParamConstraint(
                        section=section_key,
                        name=name,
                        description=desc,
                        character=char,
                        length=length,
                        required=required,
                    ))

    return constraints, error_codes


def _find_endpoint_paths(text: str) -> list[str]:
    """Find API endpoint path patterns in text (e.g. /merchants/{merchantId}/channels)."""
    if not text:
        return []

    # normalize spacing so inline tags like /psps/ <code>{pspId}</code> /ownedContacts
    # become a single path token before regex matching.
    normalized = re.sub(r"\s+", " ", text)
    normalized = re.sub(r"/\s+", "/", normalized)
    normalized = re.sub(r"\s+/", "/", normalized)
    normalized = re.sub(r"\{\s+", "{", normalized)
    normalized = re.sub(r"\s+\}", "}", normalized)

    # match path segments and template params, stopping at non-path characters.
    # each segment is either a literal word or a {param} template.
    pattern = r"(?<!\w)(\/(?:[a-zA-Z][a-zA-Z0-9._-]*|\{[a-zA-Z][a-zA-Z0-9_]*\})(?:\/(?:[a-zA-Z][a-zA-Z0-9._-]*|\{[a-zA-Z][a-zA-Z0-9_]*\}))*)"
    raw_matches = re.findall(pattern, normalized)

    paths = []
    for m in raw_matches:
        # must have at least two segments or contain a param placeholder
        if m.count("/") >= 2 or "{" in m:
            # normalize: strip trailing slashes, collapse doubles
            cleaned = re.sub(r"/+", "/", m).rstrip("/")
            if cleaned not in paths:
                paths.append(cleaned)
    return paths


def _parse_filename(filename: str) -> tuple[str, str]:
    """Parse Confluence filename convention: NN-Operation-Name_PageID.html"""
    stem = filename.replace(".html", "")
    # remove trailing page ID
    stem = re.sub(r"_\d+$", "", stem)
    # extract chapter number
    chapter_match = re.match(r"^(\d{2})-(.+)$", stem)
    if chapter_match:
        chapter = chapter_match.group(1)
        op_name = chapter_match.group(2)
    else:
        chapter = ""
        op_name = stem
    return chapter, op_name


def _paths_match(spec_path: str, doc_path: str) -> bool:
    """Check if a path from a doc matches a spec path template."""
    # normalize both
    spec_norm = spec_path.lower().rstrip("/")
    doc_norm = doc_path.lower().rstrip("/")

    # exact match
    if spec_norm == doc_norm:
        return True

    # the doc might have concrete values where the spec has {params}
    # build regex from spec path
    regex_str = re.sub(r"\{[^}]+\}", r"[^/]+", re.escape(spec_norm))
    regex_str = regex_str.replace(r"\{", "{").replace(r"\}", "}")
    # unescape the [^/]+ parts
    regex_str = re.sub(r"\\(\[)", r"\1", regex_str)
    regex_str = re.sub(r"\\(\])", r"\1", regex_str)
    regex_str = re.sub(r"\\(\+)", r"\1", regex_str)
    regex_str = re.sub(r"\\(\^)", r"\1", regex_str)

    try:
        return bool(re.fullmatch(regex_str, doc_norm))
    except re.error:
        return spec_norm == doc_norm


def _fuzzy_match(
    operation: Operation,
    docs: list[ScannedDoc],
    seen: set[str],
) -> list[DocMatch]:
    """Fuzzy match using operation ID, summary, and filename parsing."""
    matches = []

    # build search terms from operation
    search_terms = _operation_search_terms(operation)
    if not search_terms:
        return matches

    for doc in docs:
        if doc.filename in seen:
            continue

        # compare against the operation name from filename
        doc_terms = _normalize_op_name(doc.operation_name)
        if not doc_terms:
            continue

        # score: how many search terms overlap
        overlap = search_terms & doc_terms
        if not overlap:
            continue

        confidence = len(overlap) / max(len(search_terms), len(doc_terms))
        # boost if method matches (e.g. "Get" in filename, "get" in method)
        method_words = {"get", "post", "put", "delete", "edit", "add", "list", "create", "update", "remove"}
        method_overlap = overlap & method_words
        if method_overlap:
            # method match is less informative, reduce its weight
            if overlap == method_overlap:
                confidence *= 0.3

        if confidence >= 0.3:
            matches.append(DocMatch(
                doc_source=doc.filename,
                doc_title=doc.title,
                confidence=min(confidence, 0.95),  # cap below 1.0 for fuzzy
                strategy="filename_fuzzy",
                matched_terms=list(overlap),
                snippet=_extract_snippet(doc.body_text, operation.path),
            ))

    return matches


def _glossary_match(
    operation: Operation,
    docs: list[ScannedDoc],
    glossary: Glossary,
    seen: set[str],
) -> list[DocMatch]:
    """Match using glossary aliases -- e.g. 'Contact' in filename resolves to 'User' endpoints."""
    matches = []

    # find glossary terms in the spec endpoint
    spec_text = f"{operation.path} {operation.summary} {operation.description} {operation.operation_id or ''}"
    spec_terms = glossary.expand_text(spec_text)
    if not spec_terms:
        return matches

    for doc in docs:
        if doc.filename in seen:
            continue

        # find glossary terms in the doc filename / title
        doc_text = f"{doc.operation_name} {doc.title}"
        doc_terms = glossary.expand_text(doc_text)

        # intersection: both the spec and the doc reference the same glossary concept
        overlap = spec_terms & doc_terms
        if not overlap:
            continue

        confidence = min(0.85, 0.5 + 0.15 * len(overlap))

        matches.append(DocMatch(
            doc_source=doc.filename,
            doc_title=doc.title,
            confidence=confidence,
            strategy="glossary_alias",
            matched_terms=list(overlap),
            snippet=_extract_snippet(doc.body_text, operation.path),
        ))

    return matches


def _operation_search_terms(operation: Operation) -> set[str]:
    """Extract normalized search terms from an operation."""
    terms: set[str] = set()
    if operation.operation_id:
        terms.update(_normalize_op_name(operation.operation_id))
    if operation.summary:
        terms.update(_normalize_op_name(operation.summary))
    # add path segments (skip params)
    for segment in operation.path.split("/"):
        if segment and not segment.startswith("{"):
            terms.update(_split_camel_case(segment))
    return terms


def _normalize_op_name(name: str) -> set[str]:
    """Split an operation name into lowercase word tokens."""
    # split on hyphens, underscores, camelCase boundaries, spaces
    words = re.split(r"[-_\s]", name)
    result: set[str] = set()
    for word in words:
        result.update(_split_camel_case(word))
    return {w.lower() for w in result if len(w) > 1}


def _split_camel_case(text: str) -> list[str]:
    """Split camelCase or PascalCase into words."""
    return re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)", text)


def _extract_snippet(body_text: str, search_term: str, context_chars: int = 200) -> str:
    """Extract a text snippet around the first occurrence of search_term."""
    idx = body_text.lower().find(search_term.lower())
    if idx == -1:
        # return first chunk as context
        return body_text[:context_chars].strip()
    start = max(0, idx - context_chars // 2)
    end = min(len(body_text), idx + len(search_term) + context_chars // 2)
    snippet = body_text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(body_text):
        snippet = snippet + "..."
    return snippet
