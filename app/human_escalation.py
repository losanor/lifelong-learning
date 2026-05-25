from pathlib import Path
from datetime import datetime
from typing import Any

from app.scoped_storage import read_scoped_or_seed, scoped_path


BASE_DIR = Path(__file__).resolve().parent.parent
HUMAN_ESCALATIONS_PATH = BASE_DIR / "data" / "human_escalations.md"


def append_human_escalation(
    user_goal: str,
    reason: str,
    required_decision: str,
    run_id: str = "",
    memory_namespace: str = "",
    source_agent: str = "CoS / Orchestrator",
    blockers: str = "Não informado.",
    recommendation: str = "Aguardar decisão humana antes de continuar.",
    status: str = "Aberta",
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Registra uma escalada humana no Human Escalations Log.
    """
    path = scoped_path("human_escalations.md", memory_namespace)
    path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    metadata = metadata or {}

    metadata_lines = "\n".join(
        f"- {key}: {value}" for key, value in metadata.items()
    ) or "Nenhum metadado adicional."

    entry = f"""

---

## Human Escalation — {timestamp}

### Run ID
{run_id}

### Status
{status}

### Objetivo do ciclo
{user_goal}

### Fonte da escalada
{source_agent}

### Motivo da escalada
{reason}

### Decisão humana necessária
{required_decision}

### Bloqueios envolvidos
{blockers}

### Recomendação da squad
{recommendation}

### Metadados
{metadata_lines}
"""

    with path.open("a", encoding="utf-8") as file:
        file.write(entry)


def read_human_escalations(memory_namespace: str = "") -> str:
    """
    Lê o log de escaladas humanas.
    """
    return read_scoped_or_seed("human_escalations.md", memory_namespace)


def should_create_human_escalation(state: dict[str, Any]) -> bool:
    """
    Decide se deve registrar escalada humana com base no state final do ciclo.
    """
    cos_decision = state.get("cos_decision", "")
    cos_route_action = state.get("cos_route_action", "")
    route_status = state.get("route_status", "")
    route_decision = state.get("route_decision", "")
    retry_blocked = state.get("retry_blocked", False)

    if cos_decision in {"ESCALATE_TO_HUMAN", "NO_GO"}:
        return True

    if cos_route_action == "ESCALATE_HUMAN":
        return True

    if route_status in {"human_escalation", "unknown"}:
        return True

    if route_decision in {"human_escalation", "unknown"}:
        return True

    if retry_blocked:
        return True

    return False


def build_escalation_reason(state: dict[str, Any]) -> str:
    """
    Monta motivo padronizado de escalada humana.
    
    """
    consistency_error = state.get("consistency_error", "")

    if consistency_error:
        return (
            f"Escalada gerada por inconsistência estrutural no output do CoS: "
            f"{consistency_error}"
         )
    
    if state.get("retry_blocked", False):
        return state.get(
            "retry_block_reason",
            "Retry bloqueado pela política de tentativas."
        )

    route_status = state.get("route_status", "")
    route_reason = state.get("route_reason", "")
    cos_decision = state.get("cos_decision", "")
    cos_route_action = state.get("cos_route_action", "")

    return (
        f"CoS Decision: {cos_decision}. "
        f"CoS Route Action: {cos_route_action}. "
        f"Route Status: {route_status}. "
        f"Route Reason: {route_reason}."
    )


def build_required_decision(state: dict[str, Any]) -> str:
    """
    Define qual decisão humana é necessária.
    """
    retry_target = state.get("retry_target", "")
    route_target = state.get("route_target", "")
    cos_decision = state.get("cos_decision", "")

    if state.get("retry_blocked", False):
        return (
            f"Decidir se o ciclo deve continuar apesar do limite de retries "
            f"ou se deve ser encerrado. Alvo relacionado: {retry_target or route_target}."
        )

    if cos_decision == "NO_GO":
        return "Decidir se o ciclo deve ser encerrado definitivamente ou redesenhado."

    return (
        "Decidir o próximo passo estratégico do ciclo: continuar, retornar para agente, "
        "alterar escopo ou encerrar."
    )
