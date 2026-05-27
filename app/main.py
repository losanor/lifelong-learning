from app.context_policy import build_context_bundle
from app.retry_policy import initialize_retry_state
from app.run_registry import generate_run_id, append_run_start, append_run_end
from app.observability import build_run_config, langsmith_ready
from app.operational_store import DEFAULT_DB_PATH, metrics_snapshot, record_run
from app.execution_engine import create_execution_request
from app.intake import decide_intake
from app.workspace_context import capture_workspace_context
from app.project_scope import resolve_work_scope

import sys
from time import perf_counter
from uuid import uuid4

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.graph import graph


if __name__ == "__main__":
    user_goal = input("Digite o objetivo do projeto: ")
    run_id = generate_run_id()
    trace_id = uuid4() if langsmith_ready() else None
    append_run_start(run_id=run_id, user_goal=user_goal)

    manual_validation_result = input(
        "Informe evidencia de validacao ja executada, se houver (opcional): "
    ).strip()
    preflight_intake = decide_intake(user_goal)
    preflight_workspace = capture_workspace_context(".")
    preflight_scope = resolve_work_scope(".")
    context_bundle = build_context_bundle(
        mode="normal",
        memory_namespace=preflight_scope.memory_namespace,
    )

    started_at = perf_counter()
    result = graph.invoke({
        "run_id": run_id,
        "user_goal": user_goal,
        "workspace_root": ".",
        **preflight_scope.as_state(),
        "operational_db_path": str(DEFAULT_DB_PATH),
        "work_scope": preflight_scope.as_state(),
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
    }, config=build_run_config(
        run_id=run_id,
        active_flow=preflight_intake.active_flow,
        on_demand_agents=preflight_intake.on_demand_agents,
        git_repo=preflight_workspace.git_repo,
        project_id=preflight_scope.project_id,
        initiative_id=preflight_scope.initiative_id,
        execution_policy=preflight_intake.execution_policy,
        trace_id=trace_id,
    ))
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
        trace_id=str(trace_id) if trace_id else "",
    )
    execution_request = create_execution_request(result, run_id=run_id)
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

    print("\n=== UX/UI GATE ===\n")
    print(result.get("ux_ui_output", ""))

    print("\n=== PRIVACY GATE ===\n")
    print(result.get("privacy_output", ""))

    print("\n=== APPSEC GATE ===\n")
    print(result.get("appsec_output", ""))

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

    print("\n=== EXECUTION REQUEST ===\n")
    print(execution_request)

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
