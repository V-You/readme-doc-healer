"""Diagnose tool -- compares OpenAPI spec against legacy docs and produces a gap report."""

from __future__ import annotations

from .config import Settings
from .config_profile import ConfigProfile, build_config_gap_specs, is_config_operation, load_config_profile
from .doc_scanner import DocMatch, ScannedDoc, match_docs_to_operation, scan_docs_directory
from .gap_report import Gap, GapReport, MatchedDoc
from .glossary import Glossary, load_glossary
from .redaction import redact_dict
from .spec_parser import Operation, parse_spec
from .vagueness import (
    check_endpoint_description,
    check_parameter_description,
    check_request_body_property,
)


def run_diagnose(
    spec_path: str,
    docs_path: str,
    glossary_path: str | None = None,
    settings: Settings | None = None,
) -> GapReport:
    """Run the full diagnose pipeline: parse spec, scan docs, match, detect gaps."""
    if settings is None:
        from .config import get_settings
        settings = get_settings()

    glossary_path = glossary_path or settings.resolved_glossary_path

    # parse
    spec = parse_spec(spec_path)
    docs = scan_docs_directory(docs_path)
    glossary = load_glossary(glossary_path) if glossary_path else Glossary(entries=[])
    config_profile = load_config_profile(docs_path)

    # build the gap report
    report = GapReport(
        spec_path=spec_path,
        docs_path=docs_path,
        config_quality=config_profile.summary,
    )
    config_operations_assessed = 0

    for operation in spec.operations:
        # match docs to this endpoint
        doc_matches = match_docs_to_operation(operation, docs, glossary)
        best_match = doc_matches[0] if doc_matches else None

        # detect gaps
        _check_endpoint_gaps(operation, doc_matches, best_match, report, settings)
        _check_parameter_gaps(operation, doc_matches, best_match, report, settings)
        _check_request_body_gaps(operation, doc_matches, best_match, report, settings)
        _check_example_gaps(operation, doc_matches, best_match, report, settings, docs)
        _check_error_code_gaps(operation, doc_matches, best_match, report, settings)

        if is_config_operation(operation):
            config_operations_assessed += 1
            _check_config_profile_gaps(operation, report, config_profile)

        # check for undocumented endpoint
        if not doc_matches:
            report.gaps.append(_make_gap(
                operation=operation,
                gap_type="undocumented_endpoint",
                severity="critical",
                message=f"No legacy documentation found for {operation.method.upper()} {operation.path}",
                heuristic_reason="no matching legacy doc found",
            ))

    report.config_quality.operations_assessed = config_operations_assessed

    # compute summary
    report.compute_summary()

    # apply redaction
    report.gaps = [
        Gap(**redact_dict(gap.__dict__, settings, fields=("doc_snippet", "spec_value")))
        for gap in report.gaps
        if isinstance(gap.spec_value, str) or gap.doc_snippet
    ] + [
        gap for gap in report.gaps
        if not isinstance(gap.spec_value, str) and not gap.doc_snippet
    ]
    # re-sort by severity
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    report.gaps.sort(key=lambda g: (severity_order.get(g.severity, 3), g.endpoint, g.method))

    return report


def _check_endpoint_gaps(
    op: Operation,
    doc_matches: list[DocMatch],
    best_match: DocMatch | None,
    report: GapReport,
    settings: Settings,
) -> None:
    """Check endpoint-level description quality."""
    result = check_endpoint_description(op)
    if result:
        severity = _apply_severity_modifiers(result.gap_type, _base_severity(result.gap_type, is_endpoint=True), op)
        gap = _make_gap(
            operation=op,
            gap_type=result.gap_type,
            severity=severity,
            message=result.heuristic_reason,
            heuristic_reason=result.heuristic_reason,
            needs_llm_review=result.needs_llm_review,
            doc_matches=doc_matches,
            best_match=best_match,
        )
        report.gaps.append(gap)


def _check_parameter_gaps(
    op: Operation,
    doc_matches: list[DocMatch],
    best_match: DocMatch | None,
    report: GapReport,
    settings: Settings,
) -> None:
    """Check parameter-level description quality."""
    for param in op.parameters:
        result = check_parameter_description(param, op)
        if result:
            severity = _apply_severity_modifiers(result.gap_type, _base_severity(result.gap_type, is_endpoint=False, is_required=param.required), op)
            gap = _make_gap(
                operation=op,
                gap_type=result.gap_type,
                severity=severity,
                message=result.heuristic_reason,
                parameter=param.name,
                spec_value=param.description or None,
                heuristic_reason=result.heuristic_reason,
                needs_llm_review=result.needs_llm_review,
                doc_matches=doc_matches,
                best_match=best_match,
            )
            report.gaps.append(gap)


def _check_request_body_gaps(
    op: Operation,
    doc_matches: list[DocMatch],
    best_match: DocMatch | None,
    report: GapReport,
    settings: Settings,
) -> None:
    """Check request body property descriptions."""
    for prop_name, prop_schema in op.request_body_properties.items():
        result = check_request_body_property(prop_name, prop_schema, op)
        if result:
            severity = _apply_severity_modifiers(result.gap_type, _base_severity(result.gap_type, is_endpoint=False), op)
            gap = _make_gap(
                operation=op,
                gap_type=result.gap_type,
                severity=severity,
                message=result.heuristic_reason,
                parameter=prop_name,
                spec_value=prop_schema.get("description"),
                heuristic_reason=result.heuristic_reason,
                needs_llm_review=result.needs_llm_review,
                doc_matches=doc_matches,
                best_match=best_match,
            )
            report.gaps.append(gap)


def _check_example_gaps(
    op: Operation,
    doc_matches: list[DocMatch],
    best_match: DocMatch | None,
    report: GapReport,
    settings: Settings,
    docs: list[ScannedDoc] | None = None,
) -> None:
    """Check for missing request/response examples."""
    # check whether matched legacy docs have examples we can source from
    legacy_has_success = False
    legacy_has_error = False
    legacy_has_sample = False
    legacy_source = ""
    if docs and best_match:
        matched_doc = next((d for d in docs if d.filename == best_match.doc_source), None)
        if matched_doc:
            for ex in matched_doc.examples:
                if ex.kind == "success_response":
                    legacy_has_success = True
                elif ex.kind == "error_response":
                    legacy_has_error = True
                elif ex.kind == "sample_call":
                    legacy_has_sample = True
            if legacy_has_success or legacy_has_error or legacy_has_sample:
                legacy_source = matched_doc.filename

    if not op.has_request_example and op.request_body_properties:
        severity = _apply_severity_modifiers("missing_example", "warning", op)
        hint = ""
        if legacy_has_sample:
            hint = f" (legacy doc '{legacy_source}' has a sample call that may contain a request body)"
        gap = _make_gap(
            operation=op,
            gap_type="missing_example",
            severity=severity,
            message=f"No request example for {op.method.upper()} {op.path}{hint}",
            heuristic_reason=f"no request body example found in spec{hint}",
            doc_matches=doc_matches,
            best_match=best_match,
        )
        report.gaps.append(gap)

    if not op.has_response_example:
        severity = _apply_severity_modifiers("missing_example", "warning", op)
        hint = ""
        if legacy_has_success:
            hint = f" (legacy doc '{legacy_source}' has a success response example)"
        gap = _make_gap(
            operation=op,
            gap_type="missing_example",
            severity=severity,
            message=f"No response example for {op.method.upper()} {op.path}{hint}",
            heuristic_reason=f"no response example found in spec{hint}",
            doc_matches=doc_matches,
            best_match=best_match,
        )
        report.gaps.append(gap)


def _check_error_code_gaps(
    op: Operation,
    doc_matches: list[DocMatch],
    best_match: DocMatch | None,
    report: GapReport,
    settings: Settings,
) -> None:
    """Check for missing error response codes."""
    error_codes = [c for c in op.response_codes if c.startswith(("4", "5"))]
    if not error_codes:
        severity = _apply_severity_modifiers("missing_error_code", "info", op)
        gap = _make_gap(
            operation=op,
            gap_type="missing_error_code",
            severity=severity,
            message=f"No error response codes documented for {op.method.upper()} {op.path}",
            heuristic_reason="no 4xx or 5xx responses in spec",
            doc_matches=doc_matches,
            best_match=best_match,
        )
        report.gaps.append(gap)


def _check_config_profile_gaps(
    op: Operation,
    report: GapReport,
    config_profile: ConfigProfile,
) -> None:
    """Attach aggregated config-profile gaps to configuration endpoints."""
    if not config_profile.summary.enabled:
        return

    for gap_spec in build_config_gap_specs(config_profile):
        report.gaps.append(Gap(
            endpoint=op.path,
            method=op.method,
            gap_type=gap_spec["gap_type"],
            severity=gap_spec["severity"],
            message=gap_spec["message"],
            operation_id=op.operation_id,
            spec_value=gap_spec.get("spec_value"),
            doc_snippet=gap_spec.get("doc_snippet", ""),
            doc_source=gap_spec.get("doc_source", ""),
            heuristic=True,
            heuristic_reason=gap_spec.get("heuristic_reason"),
            needs_llm_review=False,
            status="needs_review",
        ))


def _base_severity(gap_type: str, is_endpoint: bool = True, is_required: bool = True) -> str:
    """Determine base severity from gap type per PRD rules."""
    if gap_type == "missing_description":
        return "critical" if is_endpoint else ("warning" if is_required else "info")
    if gap_type == "undocumented_endpoint":
        return "critical"
    if gap_type == "doc_spec_mismatch":
        return "critical"
    if gap_type == "missing_default":
        return "warning"
    if gap_type in ("vague_description", "missing_example", "no_business_context"):
        return "warning"
    if gap_type in ("terminology_drift", "missing_error_code", "brittle_ui_path", "verbose_default_phrase"):
        return "info"
    return "info"


_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def _apply_severity_modifiers(gap_type: str, base_severity: str, op: Operation) -> str:
    """Apply context-sensitive severity escalation per PRD modifiers."""
    severity = base_severity

    # auth-sensitive or destructive endpoints
    is_destructive = op.method in ("delete", "put") and any(
        seg in op.path.lower() for seg in ("token", "contact", "password", "auth")
    )
    if is_destructive and gap_type in ("missing_error_code", "missing_example"):
        severity = _escalate(severity)

    # onboarding-critical endpoints
    is_onboarding = any(
        seg in op.path.lower() for seg in ("merchant", "channel")
    ) and op.method in ("post",)
    if is_onboarding and gap_type == "no_business_context":
        severity = "critical"

    # mutation endpoints missing request examples
    if op.method in ("post", "put", "patch") and gap_type == "missing_example":
        severity = _escalate(severity)

    return severity


def _escalate(severity: str) -> str:
    """Escalate severity by one level."""
    if severity == "info":
        return "warning"
    if severity == "warning":
        return "critical"
    return "critical"


def _make_gap(
    operation: Operation,
    gap_type: str,
    severity: str,
    message: str,
    parameter: str | None = None,
    spec_value: str | None = None,
    heuristic_reason: str = "",
    needs_llm_review: bool = False,
    doc_matches: list[DocMatch] | None = None,
    best_match: DocMatch | None = None,
) -> Gap:
    """Build a Gap with match provenance attached."""
    matched_doc_list = [
        MatchedDoc(
            doc_source=m.doc_source,
            doc_title=m.doc_title,
            confidence=m.confidence,
            strategy=m.strategy,
            matched_terms=m.matched_terms,
        )
        for m in (doc_matches or [])
    ]

    # determine status based on match quality
    if best_match and best_match.strategy == "path_exact":
        status = "accepted"
    else:
        status = "needs_review"

    return Gap(
        endpoint=operation.path,
        method=operation.method,
        gap_type=gap_type,
        severity=severity,
        message=message,
        operation_id=operation.operation_id,
        parameter=parameter,
        spec_value=spec_value,
        doc_snippet=best_match.snippet if best_match else "",
        doc_source=best_match.doc_source if best_match else "",
        heuristic=True,
        heuristic_reason=heuristic_reason or None,
        needs_llm_review=needs_llm_review,
        status=status,
        match_strategy=best_match.strategy if best_match else None,
        match_confidence=best_match.confidence if best_match else None,
        matched_docs=matched_doc_list,
    )
