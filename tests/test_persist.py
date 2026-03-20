"""Tests for opt-in local persistence of tool output."""

from __future__ import annotations

import json
from pathlib import Path

from readme_doc_healer.config import Settings
from readme_doc_healer.persist import persist_result
from readme_doc_healer import persist as persist_module
from readme_doc_healer import server as server_module


def _resolve_persisted_path(persisted_to: str) -> Path:
    candidate = Path(persisted_to)
    if candidate.is_absolute():
        return candidate
    return persist_module.PROJECT_ROOT / candidate


def test_persist_result_respects_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(persist_module, "RESULTS_ROOT", tmp_path / "result_data")
    settings = Settings(persist_results=False)

    written = persist_result("heal", {"ok": True}, endpoint="GET /channels/{channelId}", settings=settings)

    assert written is None
    assert not (tmp_path / "result_data").exists()


def test_persist_result_writes_atomic_json(tmp_path, monkeypatch):
    monkeypatch.setattr(persist_module, "RESULTS_ROOT", tmp_path / "result_data")
    settings = Settings(persist_results=True)

    written = persist_result(
        "heal",
        {"ok": True, "value": 123},
        endpoint="GET /channels/{channelId}",
        suffix=".push",
        settings=settings,
    )

    assert written is not None
    assert written.is_file()
    assert written.parent == tmp_path / "result_data" / "heal"
    assert "GET_channels_channelId" in written.name
    assert written.name.endswith(".push.json")
    assert json.loads(written.read_text()) == {"ok": True, "value": 123}
    assert not any(written.parent.glob("*.tmp"))


def test_heal_persists_when_enabled(tmp_path, monkeypatch, spec_path: str, docs_path: str, glossary_path: str):
    monkeypatch.setattr(persist_module, "RESULTS_ROOT", tmp_path / "result_data")
    monkeypatch.setattr(
        server_module,
        "get_settings",
        lambda **_: Settings(
            persist_results=True,
            spec_path=spec_path,
            docs_path=docs_path,
            glossary_path=glossary_path,
        ),
    )

    payload = json.loads(server_module.heal(endpoint="GET /channels/{channelId}"))

    persisted_to = payload.get("persisted_to")
    assert isinstance(persisted_to, str)
    persisted_path = _resolve_persisted_path(persisted_to)
    assert persisted_path.is_file()
    persisted_payload = json.loads(persisted_path.read_text())
    assert "spec_fragment" in persisted_payload
    assert persisted_payload["summary"]["operation_id"] == "getChannel"


def test_heal_push_persists_when_enabled(tmp_path, monkeypatch, spec_path: str, docs_path: str, glossary_path: str):
    monkeypatch.setattr(persist_module, "RESULTS_ROOT", tmp_path / "result_data")
    monkeypatch.setattr(
        server_module,
        "get_settings",
        lambda **_: Settings(
            persist_results=True,
            spec_path=spec_path,
            docs_path=docs_path,
            glossary_path=glossary_path,
        ),
    )

    payload = json.loads(
        server_module.heal(
            endpoint="GET /channels/{channelId}",
            push=True,
            dry_run=True,
            content_markdown="# Example\n\nPersist this.",
        )
    )

    persisted_to = payload.get("persisted_to")
    assert isinstance(persisted_to, str)
    assert persisted_to.endswith(".push.json")
    persisted_path = _resolve_persisted_path(persisted_to)
    assert persisted_path.is_file()
    persisted_payload = json.loads(persisted_path.read_text())
    assert persisted_payload["dry_run"] is True
    assert "payload_preview" in persisted_payload


def test_diagnose_persists_when_enabled(tmp_path, monkeypatch, spec_path: str, docs_path: str, glossary_path: str):
    monkeypatch.setattr(persist_module, "RESULTS_ROOT", tmp_path / "result_data")
    monkeypatch.setattr(
        server_module,
        "get_settings",
        lambda **_: Settings(
            persist_results=True,
            spec_path=spec_path,
            docs_path=docs_path,
            glossary_path=glossary_path,
        ),
    )

    payload = json.loads(server_module.diagnose(summary_only=True))

    persisted_to = payload.get("persisted_to")
    assert isinstance(persisted_to, str)
    persisted_path = _resolve_persisted_path(persisted_to)
    assert persisted_path.is_file()
    persisted_payload = json.loads(persisted_path.read_text())
    assert "summary" in persisted_payload
    assert "worst_endpoints" in persisted_payload


def test_audit_persists_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(persist_module, "RESULTS_ROOT", tmp_path / "result_data")
    monkeypatch.setattr(
        server_module,
        "get_settings",
        lambda **_: Settings(persist_results=True),
    )

    payload = json.loads(server_module.audit(offline=True))

    persisted_to = payload.get("persisted_to")
    assert isinstance(persisted_to, str)
    persisted_path = _resolve_persisted_path(persisted_to)
    assert persisted_path.is_file()
    persisted_payload = json.loads(persisted_path.read_text())
    assert "report" in persisted_payload
    assert "markdown" in persisted_payload