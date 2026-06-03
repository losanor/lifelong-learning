VALID_DECISIONS = {
    "GO",
    "GO_WITH_RESTRICTIONS",
    "RETURN_TO_PRODUCT",
    "RETURN_TO_QA",
    "RETURN_TO_ENGINEERING",
    "RETURN_TO_OPERATOR",
    "ESCALATE_TO_HUMAN",
    "NO_GO",
}


VALID_ROUTE_ACTIONS = {
    "END_CYCLE",
    "ROUTE_TO_PRODUCT",
    "ROUTE_TO_QA",
    "ROUTE_TO_ENGINEERING",
    "ROUTE_TO_OPERATOR",
    "ESCALATE_HUMAN",
}


def validate_decision_route_consistency(
    decision: str,
    route_action: str,
) -> tuple[bool, str]:
    """
    Valida se a decisão executiva e a ação de roteamento do CoS são coerentes.
    """

    allowed_pairs = {
        "GO": {"END_CYCLE"},
        "GO_WITH_RESTRICTIONS": {"END_CYCLE"},
        "RETURN_TO_PRODUCT": {"ROUTE_TO_PRODUCT"},
        "RETURN_TO_QA": {"ROUTE_TO_QA"},
        "RETURN_TO_ENGINEERING": {"ROUTE_TO_ENGINEERING"},
        "RETURN_TO_OPERATOR": {"ROUTE_TO_OPERATOR"},
        "ESCALATE_TO_HUMAN": {"ESCALATE_HUMAN"},
        "NO_GO": {"ESCALATE_HUMAN"},
    }

    valid_routes = allowed_pairs.get(decision, set())

    if route_action in valid_routes:
        return True, ""

    return (
        False,
        f"Inconsistência entre decisão '{decision}' e rota '{route_action}'."
    )