"""Configuracao de tracing para execucoes da squad."""

from __future__ import annotations

import os


def langsmith_enabled() -> bool:
    return os.getenv("LANGSMITH_TRACING", "").lower() == "true"


def langsmith_ready() -> bool:
    return langsmith_enabled() and bool(os.getenv("LANGSMITH_API_KEY", "").strip())


def build_run_config(
    *,
    run_id: str,
    active_flow: str = "",
    mode: str = "runtime",
    on_demand_agents: list[str] | tuple[str, ...] = (),
    git_repo: bool | None = None,
) -> dict:
    """Anexa metadata/tags consumidas pelo tracing do LangGraph/LangSmith."""
    tags = ["squad-v5-lite", mode]
    if active_flow:
        tags.append(active_flow)
    tags.extend(f"gate:{agent}" for agent in on_demand_agents)

    return {
        "run_name": f"squad:{run_id}",
        "tags": tags,
        "metadata": {
            "run_id": run_id,
            "active_flow": active_flow or "intake_pending",
            "on_demand_agents": list(on_demand_agents),
            "gate_count": len(on_demand_agents),
            "git_repo": git_repo,
            "structured_output": True,
            "langsmith_enabled": langsmith_enabled(),
            "langsmith_ready": langsmith_ready(),
        },
    }
