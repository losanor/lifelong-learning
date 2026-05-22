from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
RETRY_POLICY_PATH = BASE_DIR / "data" / "retry_policy.md"


RETURN_ACTION_TO_AGENT = {
    "ROUTE_TO_PRODUCT": "product",
    "ROUTE_TO_QA": "qa_planning",
    "ROUTE_TO_ENGINEERING": "engineering",
    "ROUTE_TO_OPERATOR": "operator",
}


def read_retry_policy() -> str:
    """
    Lê a política de retry da squad.
    """
    if not RETRY_POLICY_PATH.exists():
        return ""

    return RETRY_POLICY_PATH.read_text(encoding="utf-8")


def initialize_retry_state() -> dict[str, Any]:
    """
    Estado inicial de retry para um ciclo.
    """
    return {
        "total_retries": 0,
        "max_total_retries": 3,
        "max_retries_per_agent": 2,
        "retries_by_agent": {
            "product": 0,
            "qa_planning": 0,
            "engineering": 0,
            "operator": 0,
        },
        "last_retry_target": "",
        "last_retry_reason": "",
        "retry_blocked": False,
        "retry_block_reason": "",
    }


def resolve_retry_permission(
    route_action: str,
    retry_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Decide se um retorno solicitado pelo CoS pode ser permitido.

    Nesta etapa, isso só calcula permissão.
    Não executa loop automático.
    """

    if retry_state is None:
        retry_state = initialize_retry_state()

    if route_action not in RETURN_ACTION_TO_AGENT:
        return {
            **retry_state,
            "retry_allowed": False,
            "retry_target": "",
            "retry_reason": "Ação não é retorno para agente.",
        }

    target = RETURN_ACTION_TO_AGENT[route_action]

    total_retries = retry_state.get("total_retries", 0)
    max_total_retries = retry_state.get("max_total_retries", 3)
    max_retries_per_agent = retry_state.get("max_retries_per_agent", 2)
    retries_by_agent = retry_state.get("retries_by_agent", {})
    current_agent_retries = retries_by_agent.get(target, 0)

    if total_retries >= max_total_retries:
        return {
            **retry_state,
            "retry_allowed": False,
            "retry_target": target,
            "retry_blocked": True,
            "retry_block_reason": "Limite total de retries do ciclo atingido.",
            "retry_reason": "Escalar para humano.",
        }

    if current_agent_retries >= max_retries_per_agent:
        return {
            **retry_state,
            "retry_allowed": False,
            "retry_target": target,
            "retry_blocked": True,
            "retry_block_reason": f"Limite de retries para o agente {target} atingido.",
            "retry_reason": "Escalar para humano.",
        }

    updated_retries_by_agent = {
        **retries_by_agent,
        target: current_agent_retries + 1,
    }

    return {
        **retry_state,
        "total_retries": total_retries + 1,
        "retries_by_agent": updated_retries_by_agent,
        "last_retry_target": target,
        "last_retry_reason": f"CoS solicitou retorno via {route_action}.",
        "retry_allowed": True,
        "retry_target": target,
        "retry_blocked": False,
        "retry_block_reason": "",
        "retry_reason": f"Retorno permitido para {target}.",
    }