"""Utilities for opt-in local persistence of tool output."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import Settings, get_settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_ROOT = PROJECT_ROOT / "result_data"


def persist_result(
    tool_name: str,
    result: dict,
    *,
    endpoint: str | None = None,
    suffix: str = "",
    settings: Settings | None = None,
) -> Path | None:
    """Write tool output to result_data/<tool_name>/ when persistence is enabled."""
    active_settings = settings or get_settings()
    if not active_settings.persist_results:
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H_%M_%S")
    stem = _sanitize_stem(endpoint or tool_name) or tool_name
    normalized_suffix = _sanitize_suffix(suffix)
    filename = f"{timestamp}_{stem}{normalized_suffix}.json"

    output_dir = RESULTS_ROOT / tool_name
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    tmp_path = output_dir / f".{filename}.{uuid4().hex}.tmp"

    try:
        tmp_path.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
        tmp_path.replace(output_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return output_path


def format_persisted_path(path: Path) -> str:
    """Return a project-relative path string when possible."""
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _sanitize_stem(value: str) -> str:
    sanitized = value.strip().replace("{", "").replace("}", "")
    sanitized = re.sub(r"[\\/:]+", "_", sanitized)
    sanitized = re.sub(r"\s+", "_", sanitized)
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", sanitized)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("._-")


def _sanitize_suffix(value: str) -> str:
    if not value:
        return ""

    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    sanitized = sanitized.strip()
    if not sanitized:
        return ""
    if not sanitized.startswith("."):
        sanitized = f".{sanitized.lstrip('._-')}"
    return sanitized