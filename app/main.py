from app.memory import read_shared_memory
from app.decision_log import read_decision_log
from app.handoff_log import read_handoff_log
from app.compaction import read_compact_memory
from app.context_policy import build_context_bundle
from app.retry_policy import initialize_retry_state
from app.run_registry import generate_run_id, append_run_start, append_run_end
from app.observability import build_run_config
from app.operational_store import metrics_snapshot, record_run

import sys
from time import perf_counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.graph import graph


if __name__ == "__main__":
    user_goal = input("Digite o objetivo do projeto: ")
    run_id = generate_run_id()
    append_run_start(run_id=run_id, user_goal=user_goal)

    manual_validation_result = """
Validação manual realizada pelo usuário.

Status geral: aprovado.

Testes aprovados:
- MVP abriu diretamente no navegador, sem login e sem wizard.
- Campo de tarefa apareceu com placeholder em português.
- Estado vazio apareceu corretamente.
- Tarefa foi criada via Enter.
- Tarefa foi criada via botão Adicionar.
- Campo vazio não criou tarefa.
- Checkbox marcou tarefa como concluída.
- Checkbox desmarcou tarefa concluída.
- Tarefa concluída permaneceu visível com texto riscado.
- Recarregar a página manteve as tarefas salvas.
- localStorage criou a chave tasks.
- localStorage criou a chave analytics.
- analytics registrou first_visit_at.
- analytics registrou last_visit_at.
- analytics registrou visit_count.
- analytics registrou visit_dates.
- visit_count incrementou ao recarregar.
- visit_dates não duplicou a mesma data no mesmo dia.
- Não foram identificados erros críticos no uso básico.

Ressalva opcional:
- A geração de ID poderia ser reforçada com sufixo aleatório, mas não bloqueia o MVP Sujo.
"""
    shared_memory = read_shared_memory()
    decision_log = read_decision_log()
    handoff_log = read_handoff_log()
    compact_memory = read_compact_memory()
    context_bundle = build_context_bundle(mode="normal")

    started_at = perf_counter()
    result = graph.invoke({
        "run_id": run_id,
        "user_goal": user_goal,
        "workspace_root": ".",
        "context_mode": context_bundle["context_mode"],
        "context_policy": context_bundle["context_policy"],
        "retry_policy": context_bundle["retry_policy"],
        "compact_memory": context_bundle["compact_memory"],
        "shared_memory": context_bundle["shared_memory"],
        "decision_log": context_bundle["decision_log"],
        "handoff_log": context_bundle["handoff_log"],
        "manual_validation_result": manual_validation_result,
        "retry_count": 0,
        "max_retries": 2,
        "retry_state": initialize_retry_state(),
        "confidence_by_agent": {},
        "summaries_by_agent": {},
        "orchestrator_checks": {},
        "structured_outputs": {},
        "raw_model_outputs": {},
        "escalations": []
    }, config=build_run_config(run_id=run_id))
    duration_ms = round((perf_counter() - started_at) * 1000)

    append_run_end(
        run_id=run_id,
        final_status=result.get("route_status", "unknown"),
        cos_decision=result.get("cos_decision", ""),
        route_action=result.get("cos_route_action", ""),
        route_decision=result.get("route_decision", ""),
        human_escalation_created=result.get("human_escalation_created", False),
    )
    record_run(
        result,
        run_id=run_id,
        user_goal=user_goal,
        duration_ms=duration_ms,
    )
    print("\nDEBUG KEYS:", result.keys())

    print("\n=== DISCOVERY ===\n")
    print(result.get("discovery_output", ""))

    print("\n=== INTAKE ===\n")
    print(result.get("active_flow", ""))
    print(result.get("intake_rationale", ""))

    print("\n=== WORKSPACE CONTEXT ===\n")
    print(result.get("workspace_context", ""))

    print("\n=== PRODUCT ===\n")
    print(result.get("product_output", ""))

    print("\n=== QA PLANNING ===\n")
    print(result.get("qa_plan_output", ""))

    print("\n=== ENGINEERING ===\n")
    print(result.get("engineering_output", ""))

    print("\n=== IMPLEMENTATION OPERATOR ===\n")
    print(result.get("operator_output", ""))

    print("\n=== ENGINEERING REVIEW ===\n")
    print(result.get("engineering_review_output", ""))

    print("\n=== WRITING / DOCUMENTATION ===\n")
    print(result.get("writing_output", ""))

    print("\n=== QA EXECUTION ===\n")
    print(result.get("qa_exec_output", ""))

    print("\n=== COS / ORCHESTRATOR ===\n")
    print(result.get("cos_output", ""))

    print("\n=== OPERATIONAL PACKET ===\n")
    print(result.get("operational_packet", ""))

    print("\n=== COS DECISION ===\n")
    print(result.get("cos_decision", ""))

    print("\n=== COS ROUTE ACTION ===\n")
    print(result.get("cos_route_action", ""))

    print("\n=== COS ECE ===\n")
    print(result.get("cos_ece", ""))

    print("\n=== ROUTE STATUS ===\n")
    print(result.get("route_status", ""))

    print("\n=== ROUTE TARGET ===\n")
    print(result.get("route_target", ""))

    print("\n=== ROUTE REASON ===\n")
    print(result.get("route_reason", ""))
    
    print("\n=== ROUTE DECISION ===\n")
    print(result.get("route_decision", ""))

    print("\n=== RETRY ALLOWED ===\n")
    print(result.get("retry_allowed", ""))

    print("\n=== RETRY TARGET ===\n")
    print(result.get("retry_target", ""))

    print("\n=== RETRY BLOCKED ===\n")
    print(result.get("retry_blocked", ""))

    print("\n=== RETRY BLOCK REASON ===\n")
    print(result.get("retry_block_reason", ""))

    print("\n=== HUMAN ESCALATION CREATED ===\n")
    print(result.get("human_escalation_created", ""))

    print("\n=== HUMAN ESCALATION REASON ===\n")
    print(result.get("human_escalation_reason", ""))

    print("\n=== HUMAN REQUIRED DECISION ===\n")
    print(result.get("human_required_decision", ""))

    print("\n=== RUN ID ===\n")
    print(result.get("run_id", ""))

    print("\n=== DURATION MS ===\n")
    print(duration_ms)

    print("\n=== METRICS SNAPSHOT ===\n")
    print(metrics_snapshot())
