ROUTE_MAP = {
    "END_CYCLE": {
        "status": "ended",
        "target": "END",
        "reason": "CoS autorizou encerramento do ciclo."
    },
    "ESCALATE_HUMAN": {
        "status": "human_escalation",
        "target": "HUMAN",
        "reason": "CoS indicou necessidade de decisão humana."
    },
    "ROUTE_TO_PRODUCT": {
        "status": "return_requested",
        "target": "product",
        "reason": "CoS solicitou retorno ao Product Lead."
    },
    "ROUTE_TO_QA": {
        "status": "return_requested",
        "target": "qa_planning",
        "reason": "CoS solicitou retorno ao QA Planning."
    },
    "ROUTE_TO_ENGINEERING": {
        "status": "return_requested",
        "target": "engineering",
        "reason": "CoS solicitou retorno ao Engineering Lead."
    },
    "ROUTE_TO_OPERATOR": {
        "status": "return_requested",
        "target": "operator",
        "reason": "CoS solicitou retorno ao Implementation Operator."
    },
    "UNKNOWN": {
        "status": "unknown",
        "target": "HUMAN",
        "reason": "Ação de roteamento não reconhecida. Requer análise humana."
    }
}


def resolve_route(route_action: str) -> dict[str, str | bool]:
    """
    Resolve a ação de roteamento indicada pelo CoS.

    O controle detalhado de retries fica em retry_policy.py.
    """

    route = ROUTE_MAP.get(route_action, ROUTE_MAP["UNKNOWN"])

    return {
        "route_status": route["status"],
        "route_target": route["target"],
        "route_reason": route["reason"],
        "should_end": route["status"] in {"ended", "human_escalation", "unknown"},
        "should_escalate": route["status"] in {"human_escalation", "unknown"},
        "is_return_requested": route["status"] == "return_requested",
    }