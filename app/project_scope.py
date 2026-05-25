"""Identidade de projeto e iniciativa para isolar trabalho operacional."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized or "default"


@dataclass(frozen=True)
class WorkScope:
    project_id: str
    initiative_id: str
    memory_namespace: str
    workspace_root: str

    def as_state(self) -> dict:
        return asdict(self)


def resolve_work_scope(
    workspace_root: str | Path = ".",
    *,
    project_id: str | None = None,
    initiative_id: str | None = None,
) -> WorkScope:
    """Cria namespace deterministico; armazenamento particionado vem na etapa seguinte."""
    root = Path(workspace_root).resolve()
    project = _slug(project_id or root.name)
    initiative = _slug(initiative_id or "default")
    return WorkScope(
        project_id=project,
        initiative_id=initiative,
        memory_namespace=f"{project}:{initiative}",
        workspace_root=str(root),
    )
