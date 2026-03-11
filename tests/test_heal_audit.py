"""Tests for heal and audit tools."""

import json
import pytest

from readme_doc_healer.heal import run_heal, _derive_slug, _derive_title, _derive_category
from readme_doc_healer.audit import run_audit
from readme_doc_healer.config import Settings
from readme_doc_healer.mcp_apps import render_gap_matrix, render_audit_dashboard
from readme_doc_healer.spec_parser import Operation


class TestHeal:
    """Heal tool -- local-only context assembly."""

    def test_heal_by_path(self, spec_path: str, docs_path: str, glossary_path: str, settings: Settings):
        result = run_heal(
            endpoint="GET /channels/{channelId}",
            spec_path=spec_path,
            docs_path=docs_path,
            glossary_path=glossary_path,
            settings=settings,
        )
        assert "spec_fragment" in result
        assert "legacy_doc_snippets" in result
        assert "gap_entries" in result
        assert "workflow_candidates" in result
        assert result["summary"]["operation_id"] == "getChannel"

    def test_heal_by_operation_id(self, spec_path: str, docs_path: str, settings: Settings):
        result = run_heal(
            endpoint="editContact",
            spec_path=spec_path,
            docs_path=docs_path,
            settings=settings,
        )
        assert result["summary"]["endpoint"] == "POST /contacts/{contactId}"

    def test_heal_not_found(self, spec_path: str, docs_path: str, settings: Settings):
        result = run_heal(
            endpoint="/nonexistent",
            spec_path=spec_path,
            docs_path=docs_path,
            settings=settings,
        )
        assert "error" in result

    def test_heal_spec_fragment_has_responses(self, spec_path: str, docs_path: str, settings: Settings):
        result = run_heal(
            endpoint="GET /channels/{channelId}",
            spec_path=spec_path,
            docs_path=docs_path,
            settings=settings,
        )
        frag = result["spec_fragment"]
        assert "responses" in frag
        assert "path" in frag
        assert frag["path"] == "/channels/{channelId}"

    def test_heal_channel_workflow(self, spec_path: str, docs_path: str, glossary_path: str, settings: Settings):
        result = run_heal(
            endpoint="GET /channels/{channelId}",
            spec_path=spec_path,
            docs_path=docs_path,
            glossary_path=glossary_path,
            settings=settings,
        )
        workflows = result["workflow_candidates"]
        names = [w["name"] for w in workflows]
        assert any("Channel" in n for n in names)

    def test_heal_bundled_mode(self, spec_path: str, docs_path: str, settings: Settings):
        result = run_heal(
            endpoint="GET /channels/{channelId}",
            spec_path=spec_path,
            docs_path=docs_path,
            settings=settings,
            output_mode="bundled",
        )
        # bundled mode returns a flat structure with all fields
        assert "endpoint" in result
        assert "method" in result


class TestAudit:
    """Audit tool -- offline fixture mode."""

    def test_audit_offline(self):
        result = run_audit(offline=True)
        assert "report" in result
        assert "markdown" in result

    def test_audit_fixture_data(self):
        result = run_audit(offline=True)
        report = result["report"]
        assert report["offline"] is True
        assert report["project"] == "aci-merchant-onboarding-demo"
        assert len(report["page_quality"]["worst_pages"]) == 5
        assert len(report["search_terms"]["top_no_results"]) == 5
        assert len(report["feedback"]["negative_pages"]) == 3

    def test_audit_markdown_output(self):
        result = run_audit(offline=True)
        md = result["markdown"]
        assert "# Audit report" in md
        assert "Update Merchant Account" in md
        assert "offline (fixture data)" in md

    def test_audit_no_key_falls_back(self):
        """Without API key and not explicitly offline, should still work (fallback)."""
        from readme_doc_healer.config import Settings
        no_key_settings = Settings(readme_api_key=None)
        result = run_audit(readme_api_key=None, offline=False, settings=no_key_settings)
        report = result["report"]
        # no key means offline fallback
        assert report["offline"] is True


def _make_operation(
    path="/test",
    method="get",
    operation_id=None,
    summary="",
    description="",
    parameters=None,
    request_body_properties=None,
    response_codes=None,
    has_request_example=False,
    has_response_example=False,
    tags=None,
) -> Operation:
    """Helper to construct a minimal Operation for unit tests."""
    return Operation(
        path=path,
        method=method,
        operation_id=operation_id,
        summary=summary,
        description=description,
        parameters=parameters or [],
        request_body_properties=request_body_properties or {},
        response_codes=response_codes or ["200"],
        has_request_example=has_request_example,
        has_response_example=has_response_example,
        tags=tags or [],
    )


class TestDeriveSlug:
    """Unit tests for _derive_slug -- camelCase to kebab-case."""

    def test_camel_case(self):
        op = _make_operation(operation_id="getChannel")
        assert _derive_slug(op) == "get-channel"

    def test_multi_word_camel(self):
        op = _make_operation(operation_id="getMerchantAccountDetails")
        assert _derive_slug(op) == "get-merchant-account-details"

    def test_already_lowercase(self):
        op = _make_operation(operation_id="health")
        assert _derive_slug(op) == "health"

    def test_fallback_to_path(self):
        op = _make_operation(path="/merchants/{merchantId}/accounts", method="post")
        assert _derive_slug(op) == "post--merchants-merchantid-accounts"


class TestDeriveTitle:
    """Unit tests for _derive_title."""

    def test_uses_summary(self):
        op = _make_operation(summary="Get a channel")
        assert _derive_title(op) == "Get a channel"

    def test_falls_back_to_operation_id(self):
        op = _make_operation(operation_id="getChannel")
        assert _derive_title(op) == "Get channel"

    def test_falls_back_to_method_path(self):
        op = _make_operation(path="/test/path", method="delete")
        assert _derive_title(op) == "DELETE /test/path"


class TestDeriveCategory:
    """Unit tests for _derive_category using real docs index."""

    def test_matches_channel_endpoint(self, docs_path: str):
        op = _make_operation(
            path="/channels/{channelId}",
            operation_id="getChannel",
        )
        cat = _derive_category(op, docs_path)
        # should match a chapter in the Confluence index
        assert cat != "API Documentation"  # not fallback
        assert isinstance(cat, str)

    def test_unknown_endpoint_falls_back(self, docs_path: str):
        op = _make_operation(
            path="/nonexistent/endpoint",
            operation_id="nonsenseOperation",
        )
        cat = _derive_category(op, docs_path)
        assert cat == "API Documentation"

    def test_no_index_falls_back(self, tmp_path):
        op = _make_operation(operation_id="getChannel")
        cat = _derive_category(op, str(tmp_path))
        assert cat == "API Documentation"


class TestHealPushDryRun:
    """Push mode -- dry-run only (no API calls that write)."""

    def test_dry_run_returns_preview(self, spec_path: str, docs_path: str, glossary_path: str, settings: Settings):
        result = run_heal(
            endpoint="GET /channels/{channelId}",
            spec_path=spec_path,
            docs_path=docs_path,
            glossary_path=glossary_path,
            settings=settings,
        )
        # use the heal result's markdown-ready content
        content = "# Test\n\nSample documentation content."
        from readme_doc_healer.heal import run_heal_push
        push_result = run_heal_push(
            endpoint="GET /channels/{channelId}",
            content_markdown=content,
            spec_path=spec_path,
            docs_path=docs_path,
            glossary_path=glossary_path,
            settings=settings,
            dry_run=True,
        )
        assert push_result["dry_run"] is True
        assert push_result["slug"] == "get-channel"
        assert push_result["content_length"] == len(content)
        assert push_result["action"] in ("create", "update")
        assert "payload_preview" in push_result

    def test_push_rejects_empty_content(self, spec_path: str, docs_path: str, settings: Settings):
        from readme_doc_healer.heal import run_heal_push
        result = run_heal_push(
            endpoint="GET /channels/{channelId}",
            content_markdown="",
            spec_path=spec_path,
            docs_path=docs_path,
            settings=settings,
            dry_run=True,
        )
        assert "error" in result

    def test_push_rejects_bad_endpoint(self, spec_path: str, docs_path: str, settings: Settings):
        from readme_doc_healer.heal import run_heal_push
        result = run_heal_push(
            endpoint="GET /nonexistent/path",
            content_markdown="# Test",
            spec_path=spec_path,
            docs_path=docs_path,
            settings=settings,
            dry_run=True,
        )
        assert "error" in result


class TestMcpAppsGapMatrix:
    """render_gap_matrix -- HTML output validation."""

    @pytest.fixture
    def sample_report(self):
        return {
            "summary": {
                "total_gaps": 5,
                "total_endpoints": 2,
                "by_severity": {"critical": 2, "warning": 2, "info": 1},
                "by_type": {"missing_description": 3, "missing_example": 2},
            },
            "gaps": [
                {"method": "GET", "endpoint": "/test", "severity": "critical",
                 "gap_type": "missing_description", "message": "No description"},
                {"method": "GET", "endpoint": "/test", "severity": "warning",
                 "gap_type": "missing_example", "message": "No example"},
                {"method": "POST", "endpoint": "/other", "severity": "critical",
                 "gap_type": "missing_description", "message": "No description"},
                {"method": "POST", "endpoint": "/other", "severity": "warning",
                 "gap_type": "missing_description", "message": "Vague"},
                {"method": "POST", "endpoint": "/other", "severity": "info",
                 "gap_type": "missing_example", "message": "Could be better"},
            ],
        }

    def test_returns_html(self, sample_report):
        html = render_gap_matrix(sample_report)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_contains_title(self, sample_report):
        html = render_gap_matrix(sample_report)
        assert "Gap matrix" in html

    def test_contains_endpoints(self, sample_report):
        html = render_gap_matrix(sample_report)
        assert "GET /test" in html
        assert "POST /other" in html

    def test_contains_severity_counts(self, sample_report):
        html = render_gap_matrix(sample_report)
        # total gaps shown in summary bar
        assert ">5<" in html

    def test_empty_report(self):
        html = render_gap_matrix({"summary": {}, "gaps": []})
        assert "<!DOCTYPE html>" in html


class TestMcpAppsAuditDashboard:
    """render_audit_dashboard -- HTML output validation."""

    @pytest.fixture
    def sample_audit(self):
        return {
            "project": "test-project",
            "offline": True,
            "page_quality": {
                "average_score": 42,
                "worst_pages": [
                    {"title": "Bad Page", "score": 15, "errors": 5, "warnings": 3},
                    {"title": "OK Page", "score": 55, "errors": 1, "warnings": 2},
                ],
            },
            "search_terms": {
                "top_no_results": [
                    {"term": "webhook", "searches": 120},
                    {"term": "oauth", "searches": 80},
                ],
                "top_low_results": [
                    {"term": "merchant", "searches": 50, "results": 1},
                ],
            },
            "feedback": {
                "negative_pages": [
                    {"title": "Setup Guide", "thumbs_down": 10, "thumbs_up": 2,
                     "comments": ["confusing", "needs update"]},
                ],
            },
        }

    def test_returns_html(self, sample_audit):
        html = render_audit_dashboard(sample_audit)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_contains_project(self, sample_audit):
        html = render_audit_dashboard(sample_audit)
        assert "test-project" in html

    def test_contains_worst_pages(self, sample_audit):
        html = render_audit_dashboard(sample_audit)
        assert "Bad Page" in html
        assert "OK Page" in html

    def test_contains_search_terms(self, sample_audit):
        html = render_audit_dashboard(sample_audit)
        assert "webhook" in html
        assert "oauth" in html

    def test_contains_feedback(self, sample_audit):
        html = render_audit_dashboard(sample_audit)
        assert "Setup Guide" in html
        assert "confusing" in html

    def test_offline_label(self, sample_audit):
        html = render_audit_dashboard(sample_audit)
        assert "Offline" in html

    def test_empty_audit(self):
        html = render_audit_dashboard({"page_quality": {}, "search_terms": {}, "feedback": {}})
        assert "<!DOCTYPE html>" in html
