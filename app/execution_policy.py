"""Politica deterministica de custo e autonomia por fluxo da squad."""

from __future__ import annotations

from typing import Iterable


FLOW_TIER = {
    "docs": "quick",
    "review": "quick",
    "decision_only": "quick",
    "research_only": "standard",
    "bugfix": "standard",
    "delivery_core": "full",
    "delivery_with_discovery": "full",
}

TIER_BUDGETS = {
    "quick": {
        "max_cost_usd": 0.15,
        "max_duration_seconds": 90,
        "max_agent_outputs": 3,
    },
    "standard": {
        "max_cost_usd": 0.30,
        "max_duration_seconds": 180,
        "max_agent_outputs": 6,
    },
    "full": {
        "max_cost_usd": 0.60,
        "max_duration_seconds": 360,
        "max_agent_outputs": 10,
    },
    "controlled": {
        "max_cost_usd": 0.80,
        "max_duration_seconds": 480,
        "max_agent_outputs": 12,
    },
}

SENSITIVE_GATES = {"privacy", "appsec"}


def _ordered_unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def build_execution_policy(
    active_flow: str,
    *,
    fixed_agents: tuple[str, ...] | list[str],
    on_demand_agents: tuple[str, ...] | list[str],
) -> dict:
    agents = _ordered_unique((*fixed_agents, *on_demand_agents))
    tier = FLOW_TIER.get(active_flow, "full")
    if SENSITIVE_GATES.intersection(on_demand_agents):
        tier = "controlled"
    budget = TIER_BUDGETS[tier]
    enforcing_tiers = {"quick", "standard", "full"}
    return {
        "execution_tier": tier,
        "enforcement_mode": "enforcing" if tier in enforcing_tiers else "advisory",
        "planned_agents": agents,
        "planned_agent_count": len(agents),
        **budget,
        "auto_allowed_actions": [
            "read_workspace",
            "draft_artifact",
            "run_local_validation",
        ],
        "approval_required_actions": [
            "write_files",
            "git_commit",
            "git_push",
            "dependency_change",
            "secret_change",
            "destructive_action",
        ],
        "rationale": (
            "Fluxo sensivel exige gates e aprovacao reforcada."
            if tier == "controlled"
            else f"Fluxo {active_flow} classificado como tier {tier}."
        ),
    }
