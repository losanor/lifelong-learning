import re


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


def normalize_text(text: str) -> str:
    """
    Normaliza texto para parsing robusto.
    """
    return text.strip().upper()


def extract_section(
    text: str,
    section_title: str,
) -> str:
    """
    Extrai conteúdo de uma seção markdown.
    """
    pattern = rf"##\s*{re.escape(section_title)}\s*(.*?)(?=\n##|\Z)"

    match = re.search(
        pattern,
        text,
        re.DOTALL | re.IGNORECASE
    )

    if not match:
        return ""

    return match.group(1).strip()


def strict_match(
    candidate: str,
    valid_options: set[str],
) -> str:
    """
    Faz matching exato e seguro.
    """
    normalized = normalize_text(candidate)

    for option in valid_options:
        if normalized == option:
            return option

    return "UNKNOWN"


def find_first_valid_line(
    section_content: str,
    valid_options: set[str],
) -> str:
    """
    Procura primeira linha válida na seção.
    """
    lines = [
        normalize_text(line)
        for line in section_content.splitlines()
        if line.strip()
    ]

    matches = []

    for line in lines:
        for option in valid_options:
            if line == option:
                matches.append(option)

    matches = list(dict.fromkeys(matches))

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        return "AMBIGUOUS"

    return "UNKNOWN"


def extract_cos_decision(cos_output: str) -> str:
    """
    Extrai decisão executiva do CoS de forma robusta.
    """
    section = extract_section(
        cos_output,
        "1. Decisão Executiva"
    )

    if not section:
        return "UNKNOWN"

    result = find_first_valid_line(
        section,
        VALID_DECISIONS
    )

    return result


def extract_route_action(cos_output: str) -> str:
    """
    Extrai ação de roteamento do CoS de forma robusta.
    """
    section = extract_section(
        cos_output,
        "9. Ação de Roteamento"
    )

    if not section:
        return "UNKNOWN"

    result = find_first_valid_line(
        section,
        VALID_ROUTE_ACTIONS
    )

    return result


def extract_ece(cos_output: str) -> str:
    """
    Extrai classificação ECE.
    """
    match = re.search(r"\bC[123]\b", cos_output.upper())

    if match:
        return match.group(0)

    return "UNKNOWN"


def is_ambiguous_decision(decision: str) -> bool:
    return decision == "AMBIGUOUS"


def is_unknown_decision(decision: str) -> bool:
    return decision == "UNKNOWN"

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