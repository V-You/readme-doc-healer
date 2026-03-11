"""Shared fixtures for tests -- loads spec, docs, and glossary once."""

import pytest
from pathlib import Path

from readme_doc_healer.spec_parser import parse_spec, ParsedSpec
from readme_doc_healer.doc_scanner import scan_docs_directory, ScannedDoc
from readme_doc_healer.glossary import load_glossary, Glossary
from readme_doc_healer.config import get_settings, Settings

_BASE = Path(__file__).resolve().parents[1] / "base_data"
_SPEC_PATH = _BASE / "ACI Merchant Onboarding API.best.openapi.yaml"
_DOCS_PATH = _BASE / "Legacy-Documentation"
_GLOSSARY_PATH = _BASE / "glossary.json"


@pytest.fixture(scope="session")
def spec() -> ParsedSpec:
    return parse_spec(str(_SPEC_PATH))


@pytest.fixture(scope="session")
def docs() -> list[ScannedDoc]:
    return scan_docs_directory(str(_DOCS_PATH))


@pytest.fixture(scope="session")
def glossary() -> Glossary:
    return load_glossary(str(_GLOSSARY_PATH))


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def spec_path() -> str:
    return str(_SPEC_PATH)


@pytest.fixture(scope="session")
def docs_path() -> str:
    return str(_DOCS_PATH)


@pytest.fixture(scope="session")
def glossary_path() -> str:
    return str(_GLOSSARY_PATH)
