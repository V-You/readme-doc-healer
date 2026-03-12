"""Gap report model -- structures the output of diagnose and input to heal."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .recipes import RecipeIssue, RecipeQualitySummary


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
class ConfigQualitySummary:
    enabled: bool = False
    lookup_path: str = ""
    config_doc_source: str = ""
    operations_assessed: int = 0
    lookup_entry_count: int = 0
    with_defaults: int = 0
    missing_default: int = 0
    brittle_ui_path: int = 0
    verbose_default_phrase: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    sample_missing_default_keys: list[str] = field(default_factory=list)
    sample_brittle_ui_paths: list[str] = field(default_factory=list)
    sample_verbose_default_phrases: list[str] = field(default_factory=list)


@dataclass
class GapReport:
    spec_path: str
    docs_path: str
    generated_at: str = ""
    summary: GapSummary = field(default_factory=lambda: GapSummary(0, 0))
    config_quality: ConfigQualitySummary = field(default_factory=ConfigQualitySummary)
    recipe_quality: Any = field(default=None)
    recipe_issues: list[Any] = field(default_factory=list)
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
        d = asdict(self)
        # recipe fields use Any to avoid circular imports; serialize properly
        if self.recipe_quality is not None:
            d["recipe_quality"] = asdict(self.recipe_quality)
        else:
            d.pop("recipe_quality", None)
        if self.recipe_issues:
            d["recipe_issues"] = [asdict(i) for i in self.recipe_issues]
        else:
            d.pop("recipe_issues", None)
        return d

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

        if self.config_quality.enabled:
            lines.extend([
                "## Config quality",
                f"- Config operations assessed: {self.config_quality.operations_assessed}",
                f"- Lookup entries: {self.config_quality.lookup_entry_count}",
                f"- Keys with defaults: {self.config_quality.with_defaults}",
                f"- Keys missing defaults: {self.config_quality.missing_default}",
                f"- Keys with UI breadcrumb paths: {self.config_quality.brittle_ui_path}",
                f"- Verbose 'defaults to' phrases: {self.config_quality.verbose_default_phrase}",
            ])
            if self.config_quality.lookup_path:
                lines.append(f"- Lookup source: {self.config_quality.lookup_path}")
            if self.config_quality.config_doc_source:
                lines.append(f"- Config doc source: {self.config_quality.config_doc_source}")
            if self.config_quality.sample_missing_default_keys:
                lines.append(
                    "- Sample keys missing defaults: "
                    + ", ".join(self.config_quality.sample_missing_default_keys)
                )
            lines.append("")

        if self.recipe_quality is not None and self.recipe_quality.enabled:
            rq = self.recipe_quality
            lines.extend([
                "## Recipe quality",
                f"- Recipes: {rq.total_recipes} ({rq.valid_recipes} valid, {rq.invalid_recipes} with issues)",
                f"- Categories: {rq.total_categories}",
                f"- Unresolved setting IDs: {rq.unresolved_setting_ids}",
                f"- Unresolved MA fields: {rq.unresolved_ma_fields}",
                f"- Unmapped recipes: {rq.unmapped_recipes}",
            ])
            if rq.source_path:
                lines.append(f"- Source: {rq.source_path}")
            if rq.by_category:
                lines.append("- By category: " + ", ".join(
                    f"{cat}: {cnt}" for cat, cnt in sorted(rq.by_category.items())
                ))
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
