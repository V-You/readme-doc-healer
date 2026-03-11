"""Audit tool -- surfaces support-relevant quality signals from a ReadMe project.

When online (default): hits ReadMe Refactored v2 metrics endpoints.
When offline or API unreachable: loads canned fixture from base_data/audit-fixture.json.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import base64

import httpx

from .config import Settings, get_settings, _PROJECT_ROOT


_FIXTURE_PATH = _PROJECT_ROOT / "base_data" / "audit-fixture.json"
_README_API_BASE = "https://api.readme.com/v2"
_METRICS_API_BASE = "https://metrics.readme.io/v2"


@dataclass
class AuditReport:
    """Structured triage report from audit."""
    project: str
    generated_at: str
    page_quality: dict[str, Any]
    search_terms: dict[str, Any]
    feedback: dict[str, Any]
    offline: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            "# Audit report",
            "",
            f"**Project:** {self.project}",
            f"**Generated:** {self.generated_at}",
            f"**Mode:** {'offline (fixture data)' if self.offline else 'live'}",
            "",
        ]

        # page quality section
        pq = self.page_quality
        lines.append("## Page quality")
        if "average_score" in pq:
            lines.append(f"- Average score: {pq['average_score']}/100")
        lines.append("")
        lines.append("### Worst pages")
        for page in pq.get("worst_pages", []):
            lines.append(
                f"- **{page['title']}** (score: {page['score']}) -- "
                f"{page['errors']} errors, {page['warnings']} warnings"
            )
            if page.get("admin_url"):
                lines.append(f"  - [Admin link]({page['admin_url']})")
        lines.append("")

        # search terms section
        st = self.search_terms
        lines.append("## Search terms")
        lines.append("### Zero-result searches")
        for term in st.get("top_no_results", []):
            lines.append(f"- \"{term['term']}\" ({term['searches']} searches, {term['results']} results)")
        lines.append("")
        lines.append("### Low-result searches")
        for term in st.get("top_low_results", []):
            lines.append(f"- \"{term['term']}\" ({term['searches']} searches, {term['results']} results)")
        lines.append("")

        # feedback section
        fb = self.feedback
        lines.append("## User feedback")
        for page in fb.get("negative_pages", []):
            ratio = f"{page.get('thumbs_down', 0)} down / {page.get('thumbs_up', 0)} up"
            lines.append(f"### {page['title']} ({ratio})")
            for comment in page.get("comments", []):
                lines.append(f"- \"{comment}\"")
            if page.get("admin_url"):
                lines.append(f"- [Admin link]({page['admin_url']})")
            lines.append("")

        return "\n".join(lines)


def run_audit(
    readme_api_key: str | None = None,
    offline: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Run the audit pipeline -- fetch metrics or load fixture.

    Returns a dict with report data and markdown summary.
    """
    if settings is None:
        settings = get_settings()

    api_key = readme_api_key or settings.readme_api_key

    if offline or not api_key:
        report = _load_fixture()
    else:
        report = _fetch_live_metrics(api_key)

    result = {
        "report": report.to_dict(),
        "markdown": report.to_markdown(),
    }
    return result


def _load_fixture() -> AuditReport:
    """Load canned fixture data for offline demo."""
    if not _FIXTURE_PATH.exists():
        return AuditReport(
            project="demo (no fixture found)",
            generated_at="",
            page_quality={"average_score": 0, "worst_pages": []},
            search_terms={"top_no_results": [], "top_low_results": []},
            feedback={"negative_pages": []},
            offline=True,
        )

    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        data = json.load(f)

    return AuditReport(
        project=data.get("project", "unknown"),
        generated_at=data.get("generated_at", ""),
        page_quality=data.get("page_quality", {}),
        search_terms=data.get("search_terms", {}),
        feedback=data.get("feedback", {}),
        offline=True,
    )


def _fetch_live_metrics(api_key: str) -> AuditReport:
    """Fetch live metrics from ReadMe metrics API.

    Metrics live at metrics.readme.io/v2 with Basic auth (key:).
    Requires Enterprise plan -- returns 401 on lower tiers,
    in which case we fall back to the offline fixture.
    """
    from datetime import datetime, timezone

    basic_token = base64.b64encode(f"{api_key}:".encode()).decode()
    headers = {
        "Authorization": f"Basic {basic_token}",
        "Accept": "application/json",
    }

    page_quality: dict[str, Any] = {"average_score": 0, "worst_pages": []}
    search_terms: dict[str, Any] = {"top_no_results": [], "top_low_results": []}
    feedback: dict[str, Any] = {"negative_pages": []}

    try:
        with httpx.Client(timeout=15.0) as client:
            # page quality -- average votes
            resp = client.get(f"{_METRICS_API_BASE}/thumb/average", headers=headers)
            if resp.status_code == 401:
                # enterprise-only -- fall back to fixture
                return _load_fixture()
            if resp.status_code == 200:
                data = resp.json()
                page_quality["average_score"] = data.get("average", 0)

            # page quality -- worst pages
            resp = client.get(f"{_METRICS_API_BASE}/thumb/worst", headers=headers)
            if resp.status_code == 200:
                worst = resp.json()
                page_quality["worst_pages"] = [
                    {
                        "slug": p.get("slug", ""),
                        "title": p.get("title", ""),
                        "score": p.get("score", 0),
                        "errors": p.get("errors", 0),
                        "warnings": p.get("warnings", 0),
                        "admin_url": p.get("uri", ""),
                    }
                    for p in (worst if isinstance(worst, list) else worst.get("data", []))
                ][:10]

            # search terms
            resp = client.get(f"{_METRICS_API_BASE}/search/top-terms", headers=headers)
            if resp.status_code == 200:
                terms_data = resp.json()
                terms_list = terms_data if isinstance(terms_data, list) else terms_data.get("data", [])
                for t in terms_list:
                    entry = {
                        "term": t.get("term", ""),
                        "searches": t.get("searches", 0),
                        "results": t.get("results", 0),
                    }
                    if entry["results"] == 0:
                        search_terms["top_no_results"].append(entry)
                    elif entry["results"] <= 2:
                        search_terms["top_low_results"].append(entry)

            # page comments / feedback
            resp = client.get(f"{_METRICS_API_BASE}/thumb/comments", headers=headers)
            if resp.status_code == 200:
                comments_data = resp.json()
                comments_list = comments_data if isinstance(comments_data, list) else comments_data.get("data", [])
                pages: dict[str, dict[str, Any]] = {}
                for c in comments_list:
                    slug = c.get("slug", "unknown")
                    if slug not in pages:
                        pages[slug] = {
                            "slug": slug,
                            "title": c.get("title", slug),
                            "thumbs_down": 0,
                            "thumbs_up": 0,
                            "comments": [],
                            "admin_url": c.get("uri", ""),
                        }
                    if c.get("sentiment") == "negative":
                        pages[slug]["thumbs_down"] += 1
                    else:
                        pages[slug]["thumbs_up"] += 1
                    if c.get("comment"):
                        pages[slug]["comments"].append(c["comment"])

                feedback["negative_pages"] = sorted(
                    pages.values(),
                    key=lambda p: p["thumbs_down"],
                    reverse=True,
                )[:10]

    except httpx.HTTPError:
        return _load_fixture()

    return AuditReport(
        project="live",
        generated_at=datetime.now(timezone.utc).isoformat(),
        page_quality=page_quality,
        search_terms=search_terms,
        feedback=feedback,
        offline=False,
    )
