"""Intake deterministico para escolher fluxo e agentes sob demanda."""

from __future__ import annotations

from dataclasses import dataclass


FIXED_DELIVERY_AGENTS = (
    "product",
    "qa_planning",
    "engineering",
    "operator",
    "engineering_review",
    "qa_execution",
    "cos",
)

FLOW_AGENTS = {
    "delivery_core": FIXED_DELIVERY_AGENTS,
    "delivery_with_discovery": ("discovery", *FIXED_DELIVERY_AGENTS),
    "bugfix": ("engineering", "operator", "engineering_review", "qa_execution", "cos"),
    "docs": ("writing", "cos"),
    "review": ("engineering_review", "cos"),
    "decision_only": ("product", "cos"),
    "research_only": ("discovery", "cos"),
}

FLOW_TRIGGERS = {
    "bugfix": ("bug", "bugfix", "corrigir", "fix", "erro", "falha", "regressao", "regressão", "hotfix"),
    "docs": ("documentar", "documentacao", "documentação", "readme", "memo", "release note", "guia", "manual"),
    "review": ("review", "revisar codigo", "revisar código", "code review", "auditar codigo", "auditar código", "avaliar pr"),
    "decision_only": ("decidir", "decisao", "decisão", "trade-off", "tradeoff", "priorizar", "prioridade"),
    "research_only": ("pesquisar", "research", "benchmark", "concorrente", "mercado", "discovery", "evidencia", "evidência"),
}

DELIVERY_TRIGGERS = ("implementar", "construir", "criar", "feature", "mvp", "release", "entregar", "desenvolver")

ON_DEMAND_AGENTS = {
    "discovery": (
        "discovery",
        "mercado",
        "benchmark",
        "concorrente",
        "pesquisa",
        "evidencia",
        "evidência",
        "icp",
        "hipotese",
        "hipótese",
        "posicionamento",
        "validar problema",
    ),
    "ux_ui": ("ux", "ui", "interface", "jornada", "wireframe", "usabilidade"),
    "privacy": ("privacidade", "lgpd", "dados pessoais", "compliance"),
    "appsec": ("seguranca", "segurança", "auth", "autenticacao", "permissao", "permissão"),
    "writing": ("documentacao", "documentação", "memo", "release note", "handoff"),
    "data_scientist": ("scoring", "previsao", "previsão", "otimizacao", "otimização", "modelo quantitativo"),
}


@dataclass(frozen=True)
class IntakeDecision:
    active_flow: str
    needs_discovery: bool
    fixed_agents: tuple[str, ...]
    on_demand_agents: tuple[str, ...]
    rationale: str

    def as_state(self) -> dict:
        return {
            "active_flow": self.active_flow,
            "needs_discovery": self.needs_discovery,
            "fixed_agents": list(self.fixed_agents),
            "on_demand_agents": list(self.on_demand_agents),
            "intake_rationale": self.rationale,
        }


def _matches(text: str, triggers: tuple[str, ...]) -> bool:
    return any(trigger in text for trigger in triggers)


def decide_intake(
    user_goal: str,
    *,
    needs_discovery_override: bool | None = None,
    active_flow_override: str | None = None,
) -> IntakeDecision:
    """Escolhe fluxo sem gastar chamada de LLM."""
    normalized = user_goal.casefold()
    triggered = tuple(
        agent_name
        for agent_name, triggers in ON_DEMAND_AGENTS.items()
        if _matches(normalized, triggers)
    )

    flow = (
        active_flow_override
        if active_flow_override in FLOW_AGENTS
        else _choose_flow(normalized)
    )
    needs_discovery = (
        needs_discovery_override
        if needs_discovery_override is not None
        else flow in {"delivery_with_discovery", "research_only"}
        or ("discovery" in triggered and flow == "delivery_core")
    )

    if flow == "delivery_core" and needs_discovery:
        flow = "delivery_with_discovery"
    if flow == "delivery_with_discovery" and needs_discovery_override is False:
        flow = "delivery_core"

    if needs_discovery and "discovery" not in triggered:
        triggered = ("discovery", *triggered)
    if not needs_discovery:
        triggered = tuple(agent for agent in triggered if agent != "discovery")

    rationale = (
        f"Fluxo {flow} selecionado. Discovery acionado por gatilho ou override de intake."
        if needs_discovery
        else f"Fluxo {flow} selecionado. Discovery omitido no intake."
    )
    if triggered:
        rationale = f"{rationale} Sob demanda sinalizados: {', '.join(triggered)}."

    return IntakeDecision(
        active_flow=flow,
        needs_discovery=needs_discovery,
        fixed_agents=tuple(agent for agent in FLOW_AGENTS[flow] if agent not in ON_DEMAND_AGENTS),
        on_demand_agents=triggered,
        rationale=rationale,
    )


def _choose_flow(normalized: str) -> str:
    if _matches(normalized, FLOW_TRIGGERS["bugfix"]):
        return "bugfix"
    if _matches(normalized, FLOW_TRIGGERS["review"]):
        return "review"
    if _matches(normalized, FLOW_TRIGGERS["docs"]):
        return "docs"
    if _matches(normalized, FLOW_TRIGGERS["decision_only"]):
        return "decision_only"
    if _matches(normalized, FLOW_TRIGGERS["research_only"]):
        if _matches(normalized, DELIVERY_TRIGGERS):
            return "delivery_with_discovery"
        return "research_only"
    return "delivery_core"
