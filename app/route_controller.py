from typing import Literal
from app.routing import resolve_route
from app.retry_policy import resolve_retry_permission


RouteDecision = Literal[
    "end_cycle",
    "human_escalation",
    "return_requested",
    "unknown"
]


def decide_next_step(state: dict) -> RouteDecision:
    """
    Decide o próximo passo do grafo com base no output do CoS.

    Versao segura:
    - END_CYCLE encerra
    - ESCALATE_HUMAN encerra com escalada humana
    - ROUTE_TO_* retorna ao agente enquanto a politica de retry permitir
    - UNKNOWN encerra como escalada implícita
    """

    route_action = state.get("cos_route_action", "UNKNOWN")
    retry_state = state.get("retry_state", {})

    route = resolve_route(route_action)
    retry = resolve_retry_permission(route_action, retry_state)

    if route["route_status"] == "ended":
        return "end_cycle"

    if route["route_status"] in {"human_escalation", "unknown"}:
        return "human_escalation"

    if route["route_status"] == "return_requested":
        if retry.get("retry_allowed", False):
            return "return_requested"
        return "human_escalation"

    return "unknown"


def get_route_target(state: dict) -> str:
    """
    Retorna o alvo solicitado pelo CoS, se houver.
    """
    return state.get("route_target", "")


def get_route_summary(state: dict) -> dict:
    """
    Retorna um resumo auditável da decisão de rota.
    """
    return {
        "cos_decision": state.get("cos_decision", ""),
        "cos_route_action": state.get("cos_route_action", ""),
        "route_status": state.get("route_status", ""),
        "route_target": state.get("route_target", ""),
        "route_reason": state.get("route_reason", ""),
        "route_decision": state.get("route_decision", ""),
        "retry_allowed": state.get("retry_allowed", False),
        "retry_target": state.get("retry_target", ""),
        "retry_blocked": state.get("retry_blocked", False),
        "retry_block_reason": state.get("retry_block_reason", ""),
        "consistency_error": state.get("consistency_error", ""),
    }
