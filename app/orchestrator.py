"""Checks operacionais leves do Orchestrator para o fluxo do LangGraph."""

from __future__ import annotations

from typing import Any

from contracts import Agente, CMO, ECE, Fase
from app.structured_output import AgentOutputEnvelope


AGENT_META = {
    "discovery": (Agente.DISCOVERY, Fase.DISCOVERY),
    "product": (Agente.PRODUCT, Fase.PRODUTO),
    "qa_planning": (Agente.QA, Fase.QA),
    "engineering": (Agente.ENGINEERING, Fase.ENGENHARIA),
    "operator": (Agente.OPERATOR, Fase.IMPLEMENTACAO),
    "engineering_review": (Agente.ENGINEERING, Fase.ENGENHARIA),
    "qa_execution": (Agente.QA, Fase.QA),
    "writing": (Agente.WRITING, Fase.RELEASE),
    "ux_ui": (Agente.UX_UI, Fase.PRODUTO),
    "privacy": (Agente.PRIVACY, Fase.ENGENHARIA),
    "appsec": (Agente.APPSEC, Fase.ENGENHARIA),
    "cos": (Agente.COS, Fase.RELEASE),
}


def _compact(text: str, max_chars: int) -> str:
    value = " ".join(text.split())
    return value[:max_chars]


def build_cmo_block(
    *,
    agent_name: str,
    user_goal: str,
    task: str,
    primary_input: str,
    expected_output: str,
    restrictions: list[str] | None = None,
    decisions: list[str] | None = None,
    ece_minimum: ECE = ECE.C2,
) -> str:
    """Monta o CMO textual que entra no prompt do agente."""
    agente, fase = AGENT_META[agent_name]
    cmo = CMO(
        agente_destino=agente,
        objetivo=_compact(user_goal, 300),
        fase_atual=fase,
        tarefa=_compact(task, 400),
        input_principal=_compact(primary_input or "Sem artefato anterior.", 600),
        restricoes=(restrictions or [])[:5],
        decisoes_previas=(decisions or [])[:5],
        output_esperado=_compact(expected_output, 200),
        ece_minimo=ece_minimum,
    )
    return cmo.resumo_tokens()


def inspect_agent_output(output: AgentOutputEnvelope, validation_errors: list[str] | None = None) -> dict[str, Any]:
    """Verifica o resumo estruturado ja validado pelo schema Pydantic."""
    summary_received = output.summary is not None
    ece = output.summary.ece if summary_received else "UNKNOWN"
    validation_errors = validation_errors or []
    issues: list[str] = []
    issues.extend(
        f"Schema invalido: {' '.join(error.split())[:500]}"
        for error in validation_errors
    )
    if not summary_received:
        issues.append("Resumo Estruturado ausente.")
    if ece == "UNKNOWN":
        issues.append("ECE ausente no Resumo Estruturado.")

    c3_detected = ece == "C3"
    if c3_detected:
        issues.append("Output C3 nao pode alimentar execucao sem destravamento.")

    return {
        "structured_summary_received": summary_received,
        "ece": ece,
        "c3_detected": c3_detected,
        "needs_cos_attention": bool(issues),
        "issues": issues,
    }


def update_orchestrator_checks(
    state: dict[str, Any],
    *,
    agent_name: str,
    output: AgentOutputEnvelope,
    validation_errors: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    check = {
        "cmo_injected": True,
        "schema_valid": not bool(validation_errors),
        **inspect_agent_output(output, validation_errors),
    }
    return {
        **state.get("orchestrator_checks", {}),
        agent_name: check,
    }


def format_orchestrator_checks(state: dict[str, Any]) -> str:
    checks = state.get("orchestrator_checks", {})
    if not checks:
        return "Nenhum check operacional registrado."

    linhas = []
    for agent_name, check in checks.items():
        issues = " | ".join(check.get("issues", [])) or "sem alertas"
        linhas.append(
            f"- {agent_name}: CMO={check.get('cmo_injected', False)}; "
            f"resumo={check.get('structured_summary_received', False)}; "
            f"ECE={check.get('ece', 'UNKNOWN')}; alertas={issues}"
        )
    return "\n".join(linhas)
