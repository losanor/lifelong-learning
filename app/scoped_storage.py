"""Filesystem paths partitioned by project and initiative namespace."""

from __future__ import annotations

from pathlib import Path
import re


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SCOPED_DATA_DIR = DATA_DIR / "namespaces"


def _safe_part(value: str) -> str:
    safe = re.sub(r"[^a-z0-9-]+", "-", value.casefold()).strip("-")
    return safe or "default"


def scoped_path(filename: str, memory_namespace: str = "") -> Path:
    if not memory_namespace:
        return DATA_DIR / filename
    project_id, _, initiative_id = memory_namespace.partition(":")
    return SCOPED_DATA_DIR / _safe_part(project_id) / _safe_part(initiative_id) / filename


def read_scoped_or_seed(filename: str, memory_namespace: str = "") -> str:
    path = scoped_path(filename, memory_namespace)
    if path.exists():
        return path.read_text(encoding="utf-8")
    seed = DATA_DIR / filename
    return seed.read_text(encoding="utf-8") if seed.exists() else ""
