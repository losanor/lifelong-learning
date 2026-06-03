"""Vision Agent node — clarifies ambiguous direction before any operational brief."""

from __future__ import annotations

from app.handoff_log import append_handoff
from app.state import SquadState


def vision_node(state: SquadState) -> dict:
    # Lazy imports from graph to avoid circular dependency.
    from app.graph import (
        cmo_block,
        invoke_validated,
        read_prompt,
        with_orchestrator_check,
        workspace_context_block,
    )

    prompt = read_prompt("vision.txt")
    run_id = state.get("run_id", "")
    cmo = cmo_block(
        state,
        "vision",
        "Clarificar direção nebulosa e produzir Mapa de Visão antes do brief operacional.",
        state.get("user_goal", ""),
        "Mapa de Visão com problema, restrições, suposições, dados faltantes, riscos e ECE.",
    )

    response = invoke_validated(
        "vision",
        f"[[AGENT:VISION]]\n\n"
        f"{prompt}\n\n"
        f"{cmo}\n\n"
        f"Objetivo do projeto:\n{state['user_goal']}\n\n"
        f"{workspace_context_block(state, 'vision')}",
    )

    append_handoff(
        run_id=run_id,
        memory_namespace=state.get("memory_namespace", ""),
        db_path=state.get("operational_db_path") or None,
        from_agent="Vision Agent",
        to_agent="CoS / Intake Gate",
        artifact="Mapa de Visão",
        summary=response.content[:500],
        ece=response.envelope.summary.ece,
        blockers=(
            "Visão ainda ambígua (ECE=C3): CoS Intake deve escalar para decisão humana antes de qualquer execução."
            if response.envelope.summary.ece == "C3"
            else "Sem bloqueios operacionais identificados neste ponto."
        ),
        next_step="CoS Intake incorpora o Mapa de Visão no brief e enquadra o ciclo seguinte.",
        escalate_to_cos="Sim. CoS Intake enquadra o brief a partir do Mapa de Visão.",
    )

    return with_orchestrator_check(state, "vision", response, {
        "vision_output": response.content,
        "confidence_by_agent": {
            **state.get("confidence_by_agent", {}),
            "vision": response.envelope.summary.ece,
        },
        "summaries_by_agent": {
            **state.get("summaries_by_agent", {}),
            "vision": response.content[:300],
        },
    })
