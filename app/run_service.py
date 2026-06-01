"""Submission service for manual project runs from the operations console."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any
from uuid import UUID, uuid4

from app.context_policy import build_context_bundle
from app.execution_engine import create_execution_request
from app.intake import decide_intake
from app.observability import build_run_config, langsmith_ready
from app.operational_store import (
    DEFAULT_DB_PATH,
    bind_initiative_document,
    initiative_document_snapshot,
    record_run,
    record_run_started,
    update_run_status,
)
from app.project_scope import resolve_work_scope
from app.reference_documents import list_reference_documents, load_reference_document
from app.retry_policy import initialize_retry_state
from app.run_registry import append_run_end, append_run_start, generate_run_id
from app.scoped_storage import scoped_path
from app.workspace_context import capture_workspace_context

_ESCALATION_STATE_FILE = "escalation_state.json"


def _save_escalation_state(result: dict[str, Any], memory_namespace: str) -> None:
    """Persist retry_state and confidence_by_agent when a run ends with human escalation.

    On the next run for the same namespace, _load_escalation_state restores this
    state so retry counters and agent confidence levels survive the human pause.
    """
    if not result.get("human_escalation_created"):
        return
    path = scoped_path(_ESCALATION_STATE_FILE, memory_namespace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "source_run_id": result.get("run_id", ""),
        "retry_state": result.get("retry_state") or {},
        "confidence_by_agent": result.get("confidence_by_agent") or {},
    }
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")


def _load_escalation_state(memory_namespace: str) -> dict[str, Any]:
    """Load persisted state from a previous run that ended with human escalation."""
    path = scoped_path(_ESCALATION_STATE_FILE, memory_namespace)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "retry_state": data.get("retry_state") or {},
            "confidence_by_agent": data.get("confidence_by_agent") or {},
        }
    except Exception:
        return {}


def _invoke_graph_with_budget(
    graph: Any,
    initial_state: dict[str, Any],
    config: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Invoke the graph with optional hard budget enforcement.

    enforcement_mode="advisory"  — runs to completion.
    enforcement_mode="enforcing" — stops early and sets budget_exceeded=True when
                                   any of these limits is hit:
                                     • structured_outputs count >= max_agent_outputs
                                     • elapsed wall time   >= max_duration_seconds
                                     • retry_state total_retries >= max_agent_outputs
    """
    enforcement = policy.get("enforcement_mode", "advisory")
    max_outputs = int(policy.get("max_agent_outputs", 10))
    max_seconds = float(policy.get("max_duration_seconds", 3600))

    if enforcement != "enforcing":
        return graph.invoke(initial_state, config=config)

    start = monotonic()
    final_state: dict[str, Any] = {}
    for snapshot in graph.stream(initial_state, config=config, stream_mode="values"):
        final_state = snapshot
        output_count = len(snapshot.get("structured_outputs") or {})
        total_retries = int((snapshot.get("retry_state") or {}).get("total_retries", 0))
        elapsed = monotonic() - start
        if output_count >= max_outputs or elapsed >= max_seconds or total_retries >= max_outputs:
            final_state = {**snapshot, "budget_exceeded": True}
            break
    return final_state or {}


RUN_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="squad-run")


def available_reference_documents(payload: dict[str, Any]) -> dict[str, Any]:
    workspace_root = str(payload.get("workspace_root", ".")).strip() or "."
    return {
        "workspace_root": str(Path(workspace_root).expanduser().resolve()),
        "documents": list_reference_documents(workspace_root),
    }


def _reference_document_for_run(
    payload: dict[str, Any],
    *,
    workspace_root: str,
    memory_namespace: str,
    include_content: bool,
    db_path: str | Path,
) -> dict[str, Any] | None:
    requested_ref = str(payload.get("reference_document_ref", "")).strip()
    source = "selected"
    if not requested_ref:
        bound = initiative_document_snapshot(memory_namespace, db_path=db_path)
        if bound and str(Path(workspace_root).resolve()) == bound["workspace_root"]:
            requested_ref = bound["document_ref"]
            source = "initiative_binding"
    if not requested_ref:
        return None
    document = load_reference_document(workspace_root, requested_ref)
    document["source"] = source
    if not include_content:
        document.pop("content", None)
    return document


def preview_manual_run(
    payload: dict[str, Any],
    *,
    include_document_content: bool = False,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    user_goal = str(payload.get("user_goal", "")).strip()
    if not user_goal:
        raise ValueError("Descreva o objetivo da demanda.")
    if len(user_goal) > 4000:
        raise ValueError("O objetivo deve ter no maximo 4000 caracteres.")

    workspace_root = str(payload.get("workspace_root", ".")).strip() or "."
    workspace = Path(workspace_root).expanduser()
    if not workspace.exists() or not workspace.is_dir():
        raise ValueError("O workspace informado nao existe ou nao e um diretorio.")

    scope = resolve_work_scope(
        workspace,
        project_id=str(payload.get("project_id", "")).strip() or None,
        initiative_id=str(payload.get("initiative_id", "")).strip() or None,
    )
    reference_document = _reference_document_for_run(
        payload,
        workspace_root=str(workspace.resolve()),
        memory_namespace=scope.memory_namespace,
        include_content=include_document_content,
        db_path=db_path,
    )
    intake = decide_intake(user_goal)
    return {
        "user_goal": user_goal,
        **scope.as_state(),
        "active_flow": intake.active_flow,
        "needs_discovery": intake.needs_discovery,
        "fixed_agents": list(intake.fixed_agents),
        "on_demand_agents": list(intake.on_demand_agents),
        "rationale": intake.rationale,
        "execution_policy": intake.execution_policy,
        "reference_document": reference_document,
    }


def enqueue_manual_run(
    payload: dict[str, Any],
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    if payload.get("cost_confirmed") is not True:
        raise ValueError("Confirme o tier e o limite estimado antes de iniciar a run.")

    preview = preview_manual_run(payload, include_document_content=True, db_path=db_path)
    if preview.get("reference_document"):
        bind_initiative_document(
            preview["memory_namespace"],
            preview["workspace_root"],
            preview["reference_document"],
            db_path=db_path,
        )
    run_id = generate_run_id()
    trace_id = uuid4() if langsmith_ready() else None
    record_run_started(
        run_id=run_id,
        user_goal=preview["user_goal"],
        project_id=preview["project_id"],
        initiative_id=preview["initiative_id"],
        memory_namespace=preview["memory_namespace"],
        active_flow=preview["active_flow"],
        execution_policy=preview["execution_policy"],
        trace_id=str(trace_id) if trace_id else "",
        db_path=db_path,
    )
    RUN_EXECUTOR.submit(_execute_manual_run, run_id, preview, Path(db_path), trace_id)
    return {
        "run_id": run_id,
        "status": "queued",
        "active_flow": preview["active_flow"],
        "project_id": preview["project_id"],
        "initiative_id": preview["initiative_id"],
        "execution_policy": preview["execution_policy"],
        "trace_id": str(trace_id) if trace_id else "",
        "reference_document": {
            key: value
            for key, value in (preview.get("reference_document") or {}).items()
            if key != "content"
        } or None,
    }


def _execute_manual_run(
    run_id: str,
    preview: dict[str, Any],
    db_path: Path,
    trace_id: UUID | None = None,
) -> None:
    append_run_start(run_id=run_id, user_goal=preview["user_goal"])
    update_run_status(run_id, status="running", db_path=db_path)
    started_at = perf_counter()
    try:
        from app.graph import graph

        workspace = capture_workspace_context(preview["workspace_root"])
        context = build_context_bundle(
            mode="normal",
            memory_namespace=preview["memory_namespace"],
        )
        # Restore state from a previous human-escalated run for this namespace
        escalation_state = _load_escalation_state(preview["memory_namespace"])
        retry_state = escalation_state.get("retry_state") or initialize_retry_state()
        confidence_by_agent = escalation_state.get("confidence_by_agent") or {}

        policy = preview["execution_policy"]
        run_config = build_run_config(
            run_id=run_id,
            active_flow=preview["active_flow"],
            on_demand_agents=preview["on_demand_agents"],
            git_repo=workspace.git_repo,
            project_id=preview["project_id"],
            initiative_id=preview["initiative_id"],
            execution_policy=policy,
            trace_id=trace_id,
        )
        initial_state = {
            "run_id": run_id,
            "user_goal": preview["user_goal"],
            "workspace_root": preview["workspace_root"],
            "project_id": preview["project_id"],
            "initiative_id": preview["initiative_id"],
            "memory_namespace": preview["memory_namespace"],
            "operational_db_path": str(db_path),
            "reference_document": preview.get("reference_document"),
            "work_scope": {
                "project_id": preview["project_id"],
                "initiative_id": preview["initiative_id"],
                "memory_namespace": preview["memory_namespace"],
                "workspace_root": preview["workspace_root"],
            },
            **context,
            "manual_validation_result": "",
            "retry_count": 0,
            "max_retries": 2,
            "retry_state": retry_state,
            "confidence_by_agent": confidence_by_agent,
            "summaries_by_agent": {},
            "orchestrator_checks": {},
            "structured_outputs": {},
            "raw_model_outputs": {},
            "escalations": [],
        }
        result = _invoke_graph_with_budget(graph, initial_state, run_config, policy)

        if result.get("budget_exceeded"):
            # Stamp the route_status so record_run stores the correct terminal state.
            result = {**result, "route_status": "budget_exceeded"}
            update_run_status(run_id, status="budget_exceeded", db_path=db_path)

        # Persist state so a resumed run after human escalation inherits context
        _save_escalation_state(result, preview["memory_namespace"])

        duration_ms = round((perf_counter() - started_at) * 1000)
        record_run(
            result,
            run_id=run_id,
            user_goal=preview["user_goal"],
            duration_ms=duration_ms,
            trace_id=str(trace_id) if trace_id else "",
            db_path=db_path,
        )
        create_execution_request(result, run_id=run_id, db_path=db_path)
        append_run_end(
            run_id=run_id,
            final_status=result.get("route_status", "unknown"),
            cos_decision=result.get("cos_decision", ""),
            route_action=result.get("cos_route_action", ""),
            route_decision=result.get("route_decision", ""),
            human_escalation_created=result.get("human_escalation_created", False),
        )
    except Exception as error:
        update_run_status(
            run_id,
            status="failed",
            operational_packet={
                "error": "A run falhou antes de produzir pacote operacional.",
                "error_type": type(error).__name__,
            },
            db_path=db_path,
        )
        append_run_end(run_id=run_id, final_status="failed")
