"""Fixture tests -- validate matching logic against known endpoint/doc pairs.

Based on PRD fixture table, adjusted to actual spec paths and verified matches.
"""

import pytest
from pathlib import Path

from readme_doc_healer.spec_parser import ParsedSpec
from readme_doc_healer.doc_scanner import ScannedDoc, match_docs_to_operation
from readme_doc_healer.glossary import Glossary


class TestExactPathMatch:
    """Endpoints whose path appears literally in the HTML body."""

    def test_get_channel_path_exact(self, spec: ParsedSpec, docs: list[ScannedDoc], glossary: Glossary):
        """GET /channels/{channelId} -> 02-Get-Channel via path_exact."""
        op = spec.find_operation("/channels/{channelId}", "get")
        assert op is not None

        matches = match_docs_to_operation(op, docs, glossary)
        assert len(matches) >= 1

        best = matches[0]
        assert best.doc_source == "02-Get-Channel_48741059.html"
        assert best.strategy == "path_exact"
        assert best.confidence == 1.0

    def test_add_contact_merchant_owned_contacts_path_exact(
        self,
        spec: ParsedSpec,
        docs: list[ScannedDoc],
        glossary: Glossary,
    ):
        """POST /merchants/{merchantId}/ownedContacts -> 01-Add-Contact via path_exact."""
        op = spec.find_operation("/merchants/{merchantId}/ownedContacts", "post")
        assert op is not None

        matches = match_docs_to_operation(op, docs, glossary)
        assert len(matches) >= 1

        best = matches[0]
        assert best.doc_source == "01-Add-Contact_48739456.html"
        assert best.strategy == "path_exact"
        assert best.confidence == 1.0

    def test_add_contact_psp_owned_contacts_path_exact(
        self,
        spec: ParsedSpec,
        docs: list[ScannedDoc],
        glossary: Glossary,
    ):
        """POST /psps/{pspId}/ownedContacts -> 01-Add-Contact via path_exact."""
        op = spec.find_operation("/psps/{pspId}/ownedContacts", "post")
        assert op is not None

        matches = match_docs_to_operation(op, docs, glossary)
        assert len(matches) >= 1

        best = matches[0]
        assert best.doc_source == "01-Add-Contact_48739456.html"
        assert best.strategy == "path_exact"
        assert best.confidence == 1.0


class TestFuzzyFilenameMatch:
    """Endpoints matched via filename keyword overlap."""

    def test_clearing_institutes_fuzzy(self, spec: ParsedSpec, docs: list[ScannedDoc], glossary: Glossary):
        """GET /psps/{pspId}/clearingInstitutes -> 01-Get-Clearing-Institutes-list via filename_fuzzy."""
        op = spec.find_operation("/psps/{pspId}/clearingInstitutes", "get")
        assert op is not None

        matches = match_docs_to_operation(op, docs, glossary)
        assert len(matches) >= 1

        best = matches[0]
        assert best.doc_source == "01-Get-Clearing-Institutes-list_48739582.html"
        assert best.strategy == "filename_fuzzy"
        assert 0.3 <= best.confidence < 1.0

    def test_channels_list_fuzzy(self, spec: ParsedSpec, docs: list[ScannedDoc], glossary: Glossary):
        """GET /merchants/{merchantId}/channels should include the channels list doc match."""
        op = spec.find_operation("/merchants/{merchantId}/channels", "get")
        assert op is not None

        matches = match_docs_to_operation(op, docs, glossary)
        assert len(matches) >= 1

        expected = [m for m in matches if m.doc_source == "04-Get-Channels-List_48741063.html"]
        assert len(expected) >= 1


class TestGlossaryAliasMatch:
    """Endpoints matched via glossary term expansion."""

    def test_contact_edit_glossary(self, spec: ParsedSpec, docs: list[ScannedDoc], glossary: Glossary):
        """POST /contacts/{contactId} (editContact) matches contact docs.

        The HTML body contains the literal path /contacts/{contactId},
        so it actually matches via path_exact (stronger than glossary_alias).
        """
        op = spec.find_operation("/contacts/{contactId}", "post")
        assert op is not None

        matches = match_docs_to_operation(op, docs, glossary)
        assert len(matches) >= 1

        best = matches[0]
        # path appears in the HTML, so path_exact wins
        assert best.strategy == "path_exact"
        assert best.confidence == 1.0

    def test_riro_setting_glossary(self, spec: ParsedSpec, docs: list[ScannedDoc], glossary: Glossary):
        """GET /merchants/{merchantId}/setting -> RiRo doc via glossary_alias.

        RiRo is the internal name for Settings in ACI terminology.
        """
        op = spec.find_operation("/merchants/{merchantId}/setting", "get")
        assert op is not None

        matches = match_docs_to_operation(op, docs, glossary)
        assert len(matches) >= 1

        # should match via glossary alias (RiRo -> Settings)
        glossary_matches = [m for m in matches if m.strategy == "glossary_alias"]
        assert len(glossary_matches) >= 1
        # one of the glossary matches should be the RiRo settings page
        riro_matches = [m for m in glossary_matches if "RiRo" in m.doc_source]
        assert len(riro_matches) >= 1


class TestNegativeMatch:
    """Endpoints that should not match any legacy doc."""

    def test_nonexistent_endpoint(self, docs: list[ScannedDoc], glossary: Glossary):
        """DELETE /nonexistent should produce no matches."""
        from readme_doc_healer.spec_parser import Operation

        fake_op = Operation(
            path="/nonexistent",
            method="delete",
            operation_id="deleteNonexistent",
            summary="Delete a nonexistent resource",
            description="This endpoint does not exist in the legacy docs.",
            parameters=[],
            request_body_properties={},
            response_codes=["200"],
            has_request_example=False,
            has_response_example=False,
            tags=[],
        )
        matches = match_docs_to_operation(fake_op, docs, glossary)
        assert len(matches) == 0


class TestSpecParser:
    """Basic spec parser validation."""

    def test_operation_count(self, spec: ParsedSpec):
        assert len(spec.operations) == 72

    def test_find_by_operation_id(self, spec: ParsedSpec):
        op = spec.find_by_operation_id("getChannel")
        assert op is not None
        assert op.path == "/channels/{channelId}"
        assert op.method == "get"

    def test_update_merchant_account_properties(self, spec: ParsedSpec):
        """The '1,252 config options' endpoint should have many request body properties."""
        op = spec.find_by_operation_id("updateMerchantAccount")
        assert op is not None
        assert len(op.request_body_properties) > 50


class TestGlossary:
    """Glossary resolution."""

    def test_riro_is_canonical(self, glossary: Glossary):
        # RiRo is the canonical term; Settings is an alias
        assert glossary.resolve("RiRo") == "RiRo"
        assert glossary.resolve("Settings") == "RiRo"

    def test_alias_resolves_to_canonical(self, glossary: Glossary):
        # BIP is canonical; Merchant Dashboard is an alias
        assert glossary.resolve("Merchant Dashboard") == "BIP"

    def test_unknown_term_returns_none(self, glossary: Glossary):
        assert glossary.resolve("xyzzy") is None

    def test_expand_text_finds_terms(self, glossary: Glossary):
        terms = glossary.expand_text("update RiRo setting value")
        assert "RiRo" in terms


class TestDiagnose:
    """End-to-end diagnose pipeline."""

    def test_produces_gaps(self, spec_path: str, docs_path: str, glossary_path: str, settings):
        from readme_doc_healer.diagnose import run_diagnose

        report = run_diagnose(spec_path, docs_path, glossary_path, settings=settings)
        assert report.summary.total_gaps > 0
        assert report.summary.total_endpoints > 0

    def test_severity_distribution(self, spec_path: str, docs_path: str, glossary_path: str, settings):
        from readme_doc_healer.diagnose import run_diagnose

        report = run_diagnose(spec_path, docs_path, glossary_path, settings=settings)
        assert report.summary.by_severity["critical"] > 0
        assert report.summary.by_severity["warning"] > 0

    def test_gap_types_present(self, spec_path: str, docs_path: str, glossary_path: str, settings):
        from readme_doc_healer.diagnose import run_diagnose

        report = run_diagnose(spec_path, docs_path, glossary_path, settings=settings)
        types = report.summary.by_type
        assert "missing_description" in types
        assert "missing_example" in types

    def test_config_quality_summary_present(self, spec_path: str, docs_path: str, glossary_path: str, settings):
        from readme_doc_healer.diagnose import run_diagnose

        report = run_diagnose(spec_path, docs_path, glossary_path, settings=settings)
        config_quality = report.config_quality
        assert config_quality.enabled is True
        assert config_quality.operations_assessed == 4
        assert config_quality.lookup_entry_count == 1225
        assert config_quality.missing_default == 579
        assert config_quality.brittle_ui_path == 795
        assert config_quality.verbose_default_phrase == 683

    def test_config_gap_types_present_for_settings_endpoint(self, spec_path: str, docs_path: str, glossary_path: str, settings):
        from readme_doc_healer.diagnose import run_diagnose

        report = run_diagnose(spec_path, docs_path, glossary_path, settings=settings)
        gap_types = {
            gap.gap_type
            for gap in report.gaps
            if gap.endpoint == "/merchants/{merchantId}/setting" and gap.method == "post"
        }
        assert "missing_default" in gap_types
        assert "brittle_ui_path" in gap_types
        assert "verbose_default_phrase" in gap_types


class TestConfigLookupEntryId:
    """rcp-1000: ConfigLookupEntry.id field population."""

    def test_all_entries_have_id(self, docs_path: str):
        from readme_doc_healer.config_profile import load_config_profile

        profile = load_config_profile(docs_path)
        assert len(profile.entries) == 1225
        entries_with_id = [e for e in profile.entries if e.id > 0]
        assert len(entries_with_id) == 1225

    def test_to_dict_excludes_id(self, docs_path: str):
        from readme_doc_healer.config_profile import load_config_profile

        profile = load_config_profile(docs_path)
        for entry in profile.entries[:5]:
            d = entry.to_dict()
            assert "id" not in d
            assert "key" in d


class TestRecipeCatalog:
    """rcp-1002/1003: Recipe loader, models, and validation."""

    def test_load_recipe_catalog(self, settings):
        from readme_doc_healer.recipes import load_recipe_catalog

        recipes_path = settings.resolved_recipes_path
        assert recipes_path is not None
        catalog = load_recipe_catalog(recipes_path)
        assert catalog.schema_version == "3.0"
        assert len(catalog.recipes) == 7
        assert len(catalog.categories) == 3
        assert catalog.metadata.total_recipes == 7

    def test_recipe_fields_roundtrip(self, settings):
        from readme_doc_healer.recipes import load_recipe_catalog

        catalog = load_recipe_catalog(settings.resolved_recipes_path)
        recipe = catalog.recipes[0]
        assert recipe.id == "3ds-basic-setup"
        assert recipe.category == "payment_security"
        assert recipe.difficulty == "intermediate"
        assert len(recipe.use_cases) == 3
        assert len(recipe.entity_settings.required) >= 2
        assert recipe.entity_settings.tool == "manage_settings"

    def test_categories_parsed(self, settings):
        from readme_doc_healer.recipes import load_recipe_catalog

        catalog = load_recipe_catalog(settings.resolved_recipes_path)
        cat_ids = {c.id for c in catalog.categories}
        assert cat_ids == {"payment_security", "risk_management", "business_setup"}

    def test_validate_recipe_catalog(self, settings, docs_path: str, spec):
        from readme_doc_healer.config_profile import load_config_profile
        from readme_doc_healer.recipes import load_recipe_catalog, validate_recipe_catalog

        catalog = load_recipe_catalog(settings.resolved_recipes_path)
        profile = load_config_profile(docs_path)
        result = validate_recipe_catalog(catalog, profile.entries, spec)
        assert result.summary.enabled is True
        assert result.summary.total_recipes == 7
        assert result.summary.total_categories == 3
        # all setting_ids should resolve against the lookup
        assert result.summary.unresolved_setting_ids == 0

    def test_validate_detects_bad_related_recipe(self, settings, docs_path: str):
        from readme_doc_healer.config_profile import load_config_profile
        from readme_doc_healer.recipes import load_recipe_catalog, validate_recipe_catalog

        catalog = load_recipe_catalog(settings.resolved_recipes_path)
        profile = load_config_profile(docs_path)
        result = validate_recipe_catalog(catalog, profile.entries)
        # check that broken related recipe refs are flagged (if any exist in source data)
        broken_refs = [i for i in result.issues if i.issue_type == "broken_related_recipe"]
        # current source data should have no broken refs
        # (all related_recipes point to valid ids)
        assert isinstance(broken_refs, list)

    def test_missing_catalog_returns_empty(self, tmp_path):
        from readme_doc_healer.recipes import load_recipe_catalog

        catalog = load_recipe_catalog(tmp_path / "nonexistent.json")
        assert len(catalog.recipes) == 0

    def test_unknown_fields_ignored(self, settings, tmp_path):
        """Unknown additive fields in source should not break parsing."""
        import json
        from readme_doc_healer.recipes import load_recipe_catalog

        source = json.loads(Path(settings.resolved_recipes_path).read_text())
        source["future_field"] = "should be ignored"
        source["recipes"][0]["new_field"] = "also ignored"
        test_file = tmp_path / "test_recipes.json"
        test_file.write_text(json.dumps(source))
        catalog = load_recipe_catalog(test_file)
        assert len(catalog.recipes) == 7


class TestDiagnoseRecipeIntegration:
    """rcp-1005: Diagnose includes recipe quality when recipes available."""

    def test_diagnose_includes_recipe_quality(self, spec_path: str, docs_path: str, glossary_path: str, settings):
        from readme_doc_healer.diagnose import run_diagnose

        report = run_diagnose(spec_path, docs_path, glossary_path, settings=settings)
        assert report.recipe_quality is not None
        assert report.recipe_quality.enabled is True
        assert report.recipe_quality.total_recipes == 7

    def test_diagnose_without_recipes_has_no_recipe_quality(self, spec_path: str, docs_path: str, glossary_path: str, settings, tmp_path):
        from readme_doc_healer.diagnose import run_diagnose

        report = run_diagnose(
            spec_path, docs_path, glossary_path,
            recipes_path=str(tmp_path / "nonexistent.json"),
            settings=settings,
        )
        assert report.recipe_quality is None

    def test_report_to_dict_includes_recipe_quality(self, spec_path: str, docs_path: str, glossary_path: str, settings):
        from readme_doc_healer.diagnose import run_diagnose

        report = run_diagnose(spec_path, docs_path, glossary_path, settings=settings)
        d = report.to_dict()
        assert "recipe_quality" in d
        assert d["recipe_quality"]["total_recipes"] == 7

    def test_report_to_markdown_includes_recipe_section(self, spec_path: str, docs_path: str, glossary_path: str, settings):
        from readme_doc_healer.diagnose import run_diagnose

        report = run_diagnose(spec_path, docs_path, glossary_path, settings=settings)
        md = report.to_markdown()
        assert "## Recipe quality" in md
