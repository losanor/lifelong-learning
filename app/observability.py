"""Configuracao de tracing para execucoes da squad."""

from __future__ import annotations

import os
from uuid import UUID


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
    project_id: str = "",
    initiative_id: str = "",
    execution_policy: dict | None = None,
    trace_id: UUID | None = None,
) -> dict:
    """Anexa metadata/tags consumidas pelo tracing do LangGraph/LangSmith."""
    tags = ["squad-v5-lite", mode]
    if active_flow:
        tags.append(active_flow)
    tags.extend(f"gate:{agent}" for agent in on_demand_agents)

    policy = execution_policy or {}
    execution_tier = policy.get("execution_tier", "")
    if execution_tier:
        tags.append(f"tier:{execution_tier}")
    config = {
        "run_name": f"squad:{run_id}",
        "tags": tags,
        "metadata": {
            "run_id": run_id,
            "active_flow": active_flow or "intake_pending",
            "on_demand_agents": list(on_demand_agents),
            "gate_count": len(on_demand_agents),
            "git_repo": git_repo,
            "project_id": project_id,
            "initiative_id": initiative_id,
            "execution_tier": execution_tier,
            "budget_enforcement": policy.get("enforcement_mode", ""),
            "max_cost_usd": policy.get("max_cost_usd"),
            "max_duration_seconds": policy.get("max_duration_seconds"),
            "planned_agent_count": policy.get("planned_agent_count"),
            "structured_output": True,
            "langsmith_enabled": langsmith_enabled(),
            "langsmith_ready": langsmith_ready(),
        },
    }
    if trace_id is not None:
        config["run_id"] = trace_id
    return config


def publish_automatic_feedback(
    *,
    trace_id: UUID,
    score: float,
    comment: str,
) -> bool:
    """Publica feedback no LangSmith somente quando tracing e chave existem."""
    if not langsmith_ready():
        return False

    from langsmith import Client

    Client().create_feedback(
        key="baseline_auto_score",
        score=score,
        trace_id=trace_id,
        comment=comment,
    )
    return True
