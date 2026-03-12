"""Recipe catalog -- data models, loader, validator, and join helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config_profile import ConfigLookupEntry
from .spec_parser import Operation, ParsedSpec


_RECIPES_FILENAME = "settings_recipes.json"
_SUPPORTED_MAJOR_VERSION = 3


# -- data models --


@dataclass
class EntitySetting:
    setting_id: int
    human_path: str = ""
    description: str = ""
    typical_value: str = ""


@dataclass
class MerchantAccountField:
    field_name: str
    description: str = ""
    typical_value: str = ""


@dataclass
class EntitySettingsBlock:
    description: str = ""
    tool: str = ""
    action: str = ""
    note: str = ""
    required: list[EntitySetting] = field(default_factory=list)
    optional: list[EntitySetting] = field(default_factory=list)


@dataclass
class MerchantAccountFieldsBlock:
    description: str = ""
    tool: str = ""
    action: str = ""
    note: str = ""
    required: list[MerchantAccountField] = field(default_factory=list)
    optional: list[MerchantAccountField] = field(default_factory=list)


@dataclass
class Recipe:
    id: str
    name: str
    description: str = ""
    category: str = ""
    use_cases: list[str] = field(default_factory=list)
    entity_settings: EntitySettingsBlock = field(default_factory=EntitySettingsBlock)
    merchant_account_fields: MerchantAccountFieldsBlock = field(default_factory=MerchantAccountFieldsBlock)
    execution_order: list[str] = field(default_factory=list)
    related_recipes: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    estimated_time: str = ""
    difficulty: str = ""


@dataclass
class RecipeCategory:
    id: str
    name: str
    description: str = ""
    icon: str = ""
    recipe_count: int = 0


@dataclass
class RecipeMetadata:
    total_recipes: int = 0
    total_entity_settings: int = 0
    total_ma_fields: int = 0
    uses_setting_ids: bool = False
    lookup_file: str = ""
    multi_layer: bool = False


@dataclass
class RecipeCatalog:
    schema_version: str = ""
    last_updated: str = ""
    description: str = ""
    recipes: list[Recipe] = field(default_factory=list)
    categories: list[RecipeCategory] = field(default_factory=list)
    metadata: RecipeMetadata = field(default_factory=RecipeMetadata)


# -- data models for validation output (also used by gap_report) --


@dataclass
class RecipeIssue:
    recipe_id: str
    severity: str
    issue_type: str
    message: str
    field: str | None = None


@dataclass
class RecipeQualitySummary:
    enabled: bool = False
    source_path: str = ""
    total_recipes: int = 0
    total_categories: int = 0
    valid_recipes: int = 0
    invalid_recipes: int = 0
    unresolved_setting_ids: int = 0
    unresolved_ma_fields: int = 0
    unmapped_recipes: int = 0
    missing_verification_steps: int = 0
    duplicate_recipe_ids: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    sample_unresolved_setting_ids: list[int] = field(default_factory=list)
    sample_unresolved_ma_fields: list[str] = field(default_factory=list)
    sample_unmapped_recipe_ids: list[str] = field(default_factory=list)


@dataclass
class RecipeValidationResult:
    summary: RecipeQualitySummary = field(default_factory=RecipeQualitySummary)
    issues: list[RecipeIssue] = field(default_factory=list)


@dataclass
class RecipeOperationMap:
    recipe_id: str = ""
    matched_operations: list[Operation] = field(default_factory=list)
    unmatched_setting_ids: list[int] = field(default_factory=list)
    unmatched_ma_fields: list[str] = field(default_factory=list)
    confidence: float = 0.0


# -- parsing helpers --


def _parse_entity_setting(raw: dict[str, Any]) -> EntitySetting:
    return EntitySetting(
        setting_id=int(raw.get("setting_id", 0)),
        human_path=str(raw.get("human_path", "")),
        description=str(raw.get("description", "")),
        typical_value=str(raw.get("typical_value", "")),
    )


def _parse_ma_field(raw: dict[str, Any]) -> MerchantAccountField:
    return MerchantAccountField(
        field_name=str(raw.get("field_name", "")),
        description=str(raw.get("description", "")),
        typical_value=str(raw.get("typical_value", "")),
    )


def _parse_entity_settings_block(raw: dict[str, Any]) -> EntitySettingsBlock:
    return EntitySettingsBlock(
        description=raw.get("description", ""),
        tool=raw.get("tool", ""),
        action=raw.get("action", ""),
        note=raw.get("note", ""),
        required=[_parse_entity_setting(s) for s in raw.get("required", [])],
        optional=[_parse_entity_setting(s) for s in raw.get("optional", [])],
    )


def _parse_ma_fields_block(raw: dict[str, Any]) -> MerchantAccountFieldsBlock:
    return MerchantAccountFieldsBlock(
        description=raw.get("description", ""),
        tool=raw.get("tool", ""),
        action=raw.get("action", ""),
        note=raw.get("note", ""),
        required=[_parse_ma_field(f) for f in raw.get("required", [])],
        optional=[_parse_ma_field(f) for f in raw.get("optional", [])],
    )


def _parse_recipe(raw: dict[str, Any]) -> Recipe:
    return Recipe(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        description=str(raw.get("description", "")),
        category=str(raw.get("category", "")),
        use_cases=raw.get("use_cases", []),
        entity_settings=_parse_entity_settings_block(raw.get("entity_settings", {})),
        merchant_account_fields=_parse_ma_fields_block(raw.get("merchant_account_fields", {})),
        execution_order=raw.get("execution_order", []),
        related_recipes=raw.get("related_recipes", []),
        prerequisites=raw.get("prerequisites", []),
        estimated_time=str(raw.get("estimated_time", "")),
        difficulty=str(raw.get("difficulty", "")),
    )


def _parse_category(raw: dict[str, Any]) -> RecipeCategory:
    return RecipeCategory(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        description=str(raw.get("description", "")),
        icon=str(raw.get("icon", "")),
        recipe_count=int(raw.get("recipe_count", 0)),
    )


def _parse_metadata(raw: dict[str, Any]) -> RecipeMetadata:
    return RecipeMetadata(
        total_recipes=int(raw.get("total_recipes", 0)),
        total_entity_settings=int(raw.get("total_entity_settings", 0)),
        total_ma_fields=int(raw.get("total_ma_fields", 0)),
        uses_setting_ids=bool(raw.get("uses_setting_ids", False)),
        lookup_file=str(raw.get("lookup_file", "")),
        multi_layer=bool(raw.get("multi_layer", False)),
    )


# -- loader --


def load_recipe_catalog(recipes_path: str | Path) -> RecipeCatalog:
    """Load and parse a settings_recipes.json file into a typed catalog."""
    path = Path(recipes_path)
    if not path.is_file():
        return RecipeCatalog()

    raw = json.loads(path.read_text(encoding="utf-8"))

    # schema version check – error on major mismatch
    version_str = str(raw.get("schema_version", ""))
    if version_str:
        try:
            major = int(version_str.split(".")[0])
        except (ValueError, IndexError):
            major = 0
        if major != _SUPPORTED_MAJOR_VERSION:
            raise ValueError(
                f"Unsupported recipe schema major version {major} "
                f"(expected {_SUPPORTED_MAJOR_VERSION}). File: {path}"
            )

    return RecipeCatalog(
        schema_version=version_str,
        last_updated=str(raw.get("last_updated", "")),
        description=str(raw.get("description", "")),
        recipes=[_parse_recipe(r) for r in raw.get("recipes", [])],
        categories=[_parse_category(c) for c in raw.get("categories", [])],
        metadata=_parse_metadata(raw.get("metadata", {})),
    )


# -- validation --


def validate_recipe_catalog(
    catalog: RecipeCatalog,
    lookup_entries: list[ConfigLookupEntry],
    spec: ParsedSpec | None = None,
) -> RecipeValidationResult:
    """Validate a recipe catalog against lookup entries and optional spec.

    Checks performed:
    - duplicate recipe ids
    - setting_id joins against lookup entries
    - related_recipes references
    - category cross-validation against top-level categories
    - operation mapping via tool field hints
    """
    issues: list[RecipeIssue] = []
    lookup_ids = {e.id for e in lookup_entries if e.id}
    recipe_ids = [r.id for r in catalog.recipes]
    recipe_id_set = set(recipe_ids)
    category_ids = {c.id for c in catalog.categories}

    # check for duplicate recipe ids
    seen_ids: set[str] = set()
    for rid in recipe_ids:
        if rid in seen_ids:
            issues.append(RecipeIssue(
                recipe_id=rid,
                severity="warning",
                issue_type="duplicate_recipe_id",
                message=f"Duplicate recipe id: {rid}",
            ))
        seen_ids.add(rid)

    unresolved_setting_ids: list[int] = []
    unresolved_ma_fields: list[str] = []
    unmapped_recipe_ids: list[str] = []

    for recipe in catalog.recipes:
        # validate category against top-level categories list
        if recipe.category and category_ids and recipe.category not in category_ids:
            issues.append(RecipeIssue(
                recipe_id=recipe.id,
                severity="warning",
                issue_type="unknown_category",
                message=f"Category '{recipe.category}' not in top-level categories list",
                field="category",
            ))

        # validate setting_id joins
        all_settings = recipe.entity_settings.required + recipe.entity_settings.optional
        for setting in all_settings:
            if setting.setting_id and setting.setting_id not in lookup_ids:
                issues.append(RecipeIssue(
                    recipe_id=recipe.id,
                    severity="warning",
                    issue_type="unresolved_setting_id",
                    message=f"setting_id {setting.setting_id} not found in lookup entries",
                    field=f"entity_settings.setting_id={setting.setting_id}",
                ))
                if setting.setting_id not in unresolved_setting_ids:
                    unresolved_setting_ids.append(setting.setting_id)

        # validate related_recipes references
        for ref in recipe.related_recipes:
            if ref not in recipe_id_set:
                issues.append(RecipeIssue(
                    recipe_id=recipe.id,
                    severity="info",
                    issue_type="broken_related_recipe",
                    message=f"Related recipe '{ref}' not found in catalog",
                    field="related_recipes",
                ))

        # check for missing execution_order (verification steps)
        if not recipe.execution_order:
            issues.append(RecipeIssue(
                recipe_id=recipe.id,
                severity="info",
                issue_type="missing_execution_order",
                message="Recipe has no execution_order steps defined",
                field="execution_order",
            ))

        # operation mapping via spec (if available)
        if spec:
            op_map = map_recipe_to_operations(recipe, spec, lookup_entries)
            if not op_map.matched_operations:
                unmapped_recipe_ids.append(recipe.id)
                issues.append(RecipeIssue(
                    recipe_id=recipe.id,
                    severity="info",
                    issue_type="unmapped_recipe",
                    message="No spec operations could be linked to this recipe",
                ))
            for sid in op_map.unmatched_setting_ids:
                if sid not in unresolved_setting_ids:
                    unresolved_setting_ids.append(sid)
            for mf in op_map.unmatched_ma_fields:
                if mf not in unresolved_ma_fields:
                    unresolved_ma_fields.append(mf)

    # count issues per recipe to determine valid/invalid
    issues_per_recipe: dict[str, int] = {}
    for issue in issues:
        if issue.severity in ("warning", "critical"):
            issues_per_recipe[issue.recipe_id] = issues_per_recipe.get(issue.recipe_id, 0) + 1

    # category distribution
    by_category: dict[str, int] = {}
    for recipe in catalog.recipes:
        cat = recipe.category or "(uncategorized)"
        by_category[cat] = by_category.get(cat, 0) + 1

    _SAMPLE_LIMIT = 5
    invalid_count = len([r for r in catalog.recipes if r.id in issues_per_recipe])
    summary = RecipeQualitySummary(
        enabled=True,
        source_path="",
        total_recipes=len(catalog.recipes),
        total_categories=len(catalog.categories),
        valid_recipes=len(catalog.recipes) - invalid_count,
        invalid_recipes=invalid_count,
        unresolved_setting_ids=len(unresolved_setting_ids),
        unresolved_ma_fields=len(unresolved_ma_fields),
        unmapped_recipes=len(unmapped_recipe_ids),
        missing_verification_steps=sum(
            1 for r in catalog.recipes if not r.execution_order
        ),
        duplicate_recipe_ids=len(recipe_ids) - len(recipe_id_set),
        by_category=by_category,
        sample_unresolved_setting_ids=unresolved_setting_ids[:_SAMPLE_LIMIT],
        sample_unresolved_ma_fields=unresolved_ma_fields[:_SAMPLE_LIMIT],
        sample_unmapped_recipe_ids=unmapped_recipe_ids[:_SAMPLE_LIMIT],
    )

    return RecipeValidationResult(summary=summary, issues=issues)


# -- operation mapping --


def map_recipe_to_operations(
    recipe: Recipe,
    spec: ParsedSpec,
    lookup_entries: list[ConfigLookupEntry],
) -> RecipeOperationMap:
    """Map a recipe to spec operations using tool field hints and setting paths.

    The entity_settings.tool and merchant_account_fields.tool values are treated
    as operationId hints for linking recipes to spec operations.
    """
    matched_ops: list[Operation] = []
    unmatched_sids: list[int] = []
    unmatched_mafs: list[str] = []

    # use tool fields as operationId hints
    tool_hints: set[str] = set()
    if recipe.entity_settings.tool:
        tool_hints.add(recipe.entity_settings.tool)
    if recipe.merchant_account_fields.tool:
        tool_hints.add(recipe.merchant_account_fields.tool)

    for hint in tool_hints:
        op = spec.find_by_operation_id(hint)
        if op:
            matched_ops.append(op)

    # also try matching via /setting path pattern for entity settings
    if not any(o for o in matched_ops if "/setting" in o.path.lower()):
        for op in spec.operations:
            if "/setting" in op.path.lower() and op not in matched_ops:
                matched_ops.append(op)
                break

    # track unmatched setting ids (ones not in lookup)
    lookup_ids = {e.id for e in lookup_entries if e.id}
    for s in recipe.entity_settings.required + recipe.entity_settings.optional:
        if s.setting_id and s.setting_id not in lookup_ids:
            if s.setting_id not in unmatched_sids:
                unmatched_sids.append(s.setting_id)

    # track unmatched ma fields (heuristic – no canonical join)
    for f in recipe.merchant_account_fields.required + recipe.merchant_account_fields.optional:
        if f.field_name:
            unmatched_mafs.append(f.field_name)

    # if we matched operations via tool hints, clear ma fields from unmatched
    if recipe.merchant_account_fields.tool:
        ma_op = spec.find_by_operation_id(recipe.merchant_account_fields.tool)
        if ma_op:
            unmatched_mafs.clear()

    # compute overall confidence
    total_links = len(tool_hints)
    matched_links = sum(1 for h in tool_hints if spec.find_by_operation_id(h))
    confidence = matched_links / total_links if total_links else 0.0

    return RecipeOperationMap(
        recipe_id=recipe.id,
        matched_operations=matched_ops,
        unmatched_setting_ids=unmatched_sids,
        unmatched_ma_fields=unmatched_mafs,
        confidence=confidence,
    )
