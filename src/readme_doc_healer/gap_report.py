"""Gap report model -- structures the output of diagnose and input to heal."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class MatchedDoc:
    doc_source: str
    doc_title: str = ""
    confidence: float = 0.0
    strategy: str = ""
    matched_terms: list[str] = field(default_factory=list)


@dataclass
class Gap:
    endpoint: str
    method: str
    gap_type: str
    severity: str
    message: str
    operation_id: str | None = None
    parameter: str | None = None
    spec_value: Any = None
    doc_snippet: str = ""
    doc_source: str = ""
    heuristic: bool = True
    heuristic_reason: str | None = None
    needs_llm_review: bool = False
    status: str = "needs_review"
    match_strategy: str | None = None
    match_confidence: float | None = None
    matched_docs: list[MatchedDoc] = field(default_factory=list)
    redacted: bool = False


@dataclass
class GapSummary:
    total_endpoints: int
    total_gaps: int
    by_severity: dict[str, int] = field(default_factory=lambda: {"critical": 0, "warning": 0, "info": 0})
    by_type: dict[str, int] = field(default_factory=dict)


@dataclass
class GapReport:
    spec_path: str
    docs_path: str
    generated_at: str = ""
    summary: GapSummary = field(default_factory=lambda: GapSummary(0, 0))
    gaps: list[Gap] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat()

    def compute_summary(self) -> None:
        """Recompute summary from current gaps."""
        severity_counts = {"critical": 0, "warning": 0, "info": 0}
        type_counts: dict[str, int] = {}
        endpoints = set()

        for gap in self.gaps:
            endpoints.add(f"{gap.method} {gap.endpoint}")
            severity_counts[gap.severity] = severity_counts.get(gap.severity, 0) + 1
            type_counts[gap.gap_type] = type_counts.get(gap.gap_type, 0) + 1

        self.summary = GapSummary(
            total_endpoints=len(endpoints),
            total_gaps=len(self.gaps),
            by_severity=severity_counts,
            by_type=type_counts,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        """Render a human-readable markdown summary."""
        lines = [
            f"# Gap report",
            f"",
            f"**Spec:** {self.spec_path}",
            f"**Docs:** {self.docs_path}",
            f"**Generated:** {self.generated_at}",
            f"",
            f"## Summary",
            f"- Total endpoints with gaps: {self.summary.total_endpoints}",
            f"- Total gaps: {self.summary.total_gaps}",
            f"- Critical: {self.summary.by_severity.get('critical', 0)}",
            f"- Warning: {self.summary.by_severity.get('warning', 0)}",
            f"- Info: {self.summary.by_severity.get('info', 0)}",
            f"",
        ]

        if self.summary.by_type:
            lines.append("### By type")
            for gap_type, count in sorted(self.summary.by_type.items()):
                lines.append(f"- {gap_type}: {count}")
            lines.append("")

        # group gaps by endpoint
        by_endpoint: dict[str, list[Gap]] = {}
        for gap in self.gaps:
            key = f"{gap.method.upper()} {gap.endpoint}"
            by_endpoint.setdefault(key, []).append(gap)

        lines.append("## Gaps by endpoint")
        lines.append("")

        for endpoint, gaps in sorted(by_endpoint.items()):
            lines.append(f"### {endpoint}")
            for gap in gaps:
                severity_marker = {"critical": "!!!", "warning": "!!", "info": "!"}.get(gap.severity, "")
                param_text = f" (param: `{gap.parameter}`)" if gap.parameter else ""
                lines.append(f"- **[{gap.severity.upper()}]** {gap.gap_type}{param_text}: {gap.message}")
                if gap.heuristic_reason:
                    lines.append(f"  - Heuristic: {gap.heuristic_reason}")
                if gap.doc_source:
                    lines.append(f"  - Doc source: {gap.doc_source} (match: {gap.match_strategy}, confidence: {gap.match_confidence})")
            lines.append("")

        return "\n".join(lines)
