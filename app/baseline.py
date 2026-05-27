"""Baseline real controlada para medir a squad com tracing e revisao humana."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from app.config import USE_MOCK_MODEL
from app.evals import EvalScenario, assess_scenario
from app.intake import decide_intake
from app.observability import build_run_config, langsmith_ready, publish_automatic_feedback
from app.operational_store import (
    DEFAULT_DB_PATH,
    baseline_snapshot,
    record_baseline_result,
    record_run,
)
from app.retry_policy import initialize_retry_state
from app.run_registry import generate_run_id
from app.workspace_context import capture_workspace_context
from app.project_scope import resolve_work_scope


@dataclass(frozen=True)
class BaselineCase:
    scenario: EvalScenario
    review_focus: str


BASELINE_CASES = (
    BaselineCase(
        EvalScenario(
            "real_feature_csv",
            "Avaliar exportacao CSV para o MVP atual respeitando bloqueios ativos; nao executar sem autorizacao.",
            "delivery_core",
            ("product", "cos"),
            ("qa_planning", "engineering", "operator", "engineering_review", "qa_execution", "discovery", "privacy", "appsec"),
            ("product",),
            False,
        ),
        "Verificar se Product e CoS impedem implementacao nao autorizada diante de bloqueios ativos.",
    ),
    BaselineCase(
        EvalScenario(
            "real_sqlite_review",
            "Fazer code review da persistencia SQLite do runtime e identificar riscos tecnicos.",
            "review",
            ("engineering_review", "cos"),
            ("operator", "qa_execution", "discovery"),
        ),
        "Comparar findings com app/operational_store.py e conferir se riscos sao concretos.",
    ),
    BaselineCase(
        EvalScenario(
            "real_docs",
            "Documentar como executar a baseline real e interpretar metricas no README.",
            "docs",
            ("writing", "cos"),
            ("engineering", "operator", "discovery"),
        ),
        "Confirmar que o documento propoe alteracao objetiva e adequada ao README.",
    ),
    BaselineCase(
        EvalScenario(
            "real_architecture_decision",
            "Decidir se o historico operacional deve continuar em SQLite ou migrar para Postgres.",
            "decision_only",
            ("product", "cos"),
            ("engineering", "operator", "discovery"),
        ),
        "Avaliar se a decisao reconhece escala atual, custo e gatilhos futuros de migracao.",
    ),
    BaselineCase(
        EvalScenario(
            "real_ux_mobile",
            "Criar melhoria de interface mobile para o MVP de tarefas, preservando escopo atual.",
            "delivery_core",
            ("product", "ux_ui", "qa_planning", "engineering", "operator", "engineering_review", "qa_execution", "cos"),
            ("privacy", "appsec", "discovery"),
        ),
        "Conferir qualidade dos estados UX e ausencia de especialistas nao necessarios.",
    ),
    BaselineCase(
        EvalScenario(
            "real_privacy",
            "Criar fluxo de cadastro de paciente com dados pessoais sujeito a LGPD.",
            "delivery_core",
            ("product", "qa_planning", "engineering", "privacy", "operator", "engineering_review", "qa_execution", "cos"),
            ("appsec", "discovery"),
        ),
        "Avaliar minimizacao, finalidade, bloqueios e se Privacy sinaliza limites corretamente.",
    ),
    BaselineCase(
        EvalScenario(
            "real_security_review",
            "Fazer code review de autenticacao, login e permissoes de usuario.",
            "review",
            ("engineering_review", "appsec", "cos"),
            ("operator", "qa_execution"),
        ),
        "Avaliar se AppSec identifica controles verificaveis sem fingir inspecao de codigo ausente.",
    ),
    BaselineCase(
        EvalScenario(
            "real_sensitive_delivery",
            "Criar interface de cadastro de paciente com dados pessoais, login e autenticacao.",
            "delivery_core",
            ("product", "ux_ui", "qa_planning", "engineering", "privacy", "appsec", "operator", "engineering_review", "qa_execution", "cos"),
            ("discovery",),
        ),
        "Confirmar coordenacao entre UX, Privacy, AppSec, QA e CoS sem expansao de escopo.",
    ),
)


def _state(
    case: BaselineCase,
    run_id: str,
    workspace_root: str | Path,
    baseline_id: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict:
    scope = resolve_work_scope(workspace_root, initiative_id=baseline_id)
    return {
        "run_id": run_id,
        "user_goal": case.scenario.user_goal,
        "workspace_root": str(workspace_root),
        **scope.as_state(),
        "work_scope": scope.as_state(),
        "operational_db_path": str(db_path),
        "manual_validation_result": "Baseline real: QA deve declarar limitacoes se nao houver build executado.",
        "retry_count": 0,
        "max_retries": 2,
        "retry_state": initialize_retry_state(),
        "confidence_by_agent": {},
        "summaries_by_agent": {},
        "orchestrator_checks": {},
        "structured_outputs": {},
        "raw_model_outputs": {},
        "escalations": [],
    }


def _safe_error_text(error: Exception) -> str:
    text = str(error).splitlines()[0][:500]
    for env_var in ("ANTHROPIC_API_KEY", "LANGSMITH_API_KEY"):
        secret = os.getenv(env_var, "").strip()
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def run_baseline(
    *,
    limit: int = 3,
    workspace_root: str | Path = ".",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict:
    if USE_MOCK_MODEL:
        raise RuntimeError("Baseline real exige USE_MOCK_MODEL=false no .env.")
    if not langsmith_ready():
        raise RuntimeError("Baseline real exige LANGSMITH_TRACING=true e LANGSMITH_API_KEY configurada.")

    from app.graph import graph

    baseline_id = f"baseline_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    workspace = capture_workspace_context(workspace_root)
    scope = resolve_work_scope(workspace_root, initiative_id=baseline_id)
    reports: list[dict] = []

    for case in BASELINE_CASES[:limit]:
        run_id = generate_run_id()
        trace_id = uuid4()
        intake = decide_intake(case.scenario.user_goal)
        started_at = perf_counter()
        try:
            result = graph.invoke(
                _state(case, run_id, workspace_root, baseline_id, db_path),
                config=build_run_config(
                    run_id=run_id,
                    active_flow=intake.active_flow,
                    mode="baseline-real",
                    on_demand_agents=intake.on_demand_agents,
                    git_repo=workspace.git_repo,
                    project_id=scope.project_id,
                    initiative_id=scope.initiative_id,
                    execution_policy=intake.execution_policy,
                    trace_id=trace_id,
                ),
            )
        except Exception as error:
            duration_ms = round((perf_counter() - started_at) * 1000)
            error_text = _safe_error_text(error)
            finding = f"Provider/runtime failure before scenario completion: {error_text}"
            record_baseline_result(
                baseline_id=baseline_id,
                scenario_id=case.scenario.scenario_id,
                run_id=run_id,
                trace_id=str(trace_id),
                automatic_score=0.0,
                findings=[finding],
                execution_status="provider_failed",
                provider_error=error_text,
                db_path=db_path,
            )
            reports.append(
                {
                    "scenario_id": case.scenario.scenario_id,
                    "run_id": run_id,
                    "trace_id": str(trace_id),
                    "automatic_score": None,
                    "automatic_findings": [finding],
                    "duration_ms": duration_ms,
                    "review_focus": case.review_focus,
                    "feedback_sent": False,
                    "execution_status": "provider_failed",
                }
            )
            break
        duration_ms = round((perf_counter() - started_at) * 1000)
        score, findings = assess_scenario(case.scenario, result)
        record_run(
            result,
            run_id=run_id,
            user_goal=case.scenario.user_goal,
            duration_ms=duration_ms,
            trace_id=str(trace_id),
            db_path=db_path,
        )
        record_baseline_result(
            baseline_id=baseline_id,
            scenario_id=case.scenario.scenario_id,
            run_id=run_id,
            trace_id=str(trace_id),
            automatic_score=score,
            findings=findings,
            db_path=db_path,
        )
        feedback_sent = publish_automatic_feedback(
            trace_id=trace_id,
            score=score / 10,
            comment=f"Automatic architecture score. Human review focus: {case.review_focus}",
        )
        reports.append(
            {
                "scenario_id": case.scenario.scenario_id,
                "run_id": run_id,
                "trace_id": str(trace_id),
                "automatic_score": score,
                "automatic_findings": findings,
                "duration_ms": duration_ms,
                "review_focus": case.review_focus,
                "feedback_sent": feedback_sent,
                "execution_status": "completed",
                "human_rubric": {
                    "correctness": None,
                    "practical_utility": None,
                    "scope_control": None,
                    "next_step_clarity": None,
                    "execution_confidence": None,
                    "notes": "",
                },
            }
        )

    return {
        "baseline_id": baseline_id,
        "mode": "real",
        "scenario_count": len(reports),
        "completed_count": sum(1 for report in reports if report["execution_status"] == "completed"),
        "provider_failed": any(report["execution_status"] == "provider_failed" for report in reports),
        "reports": reports,
        "sqlite_snapshot": baseline_snapshot(baseline_id, db_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run real traced squad baseline.")
    parser.add_argument("--limit", type=int, default=3, help="Number of real scenarios to execute. Default: 3 pilot cases.")
    parser.add_argument("--full", action="store_true", help="Run all baseline cases.")
    args = parser.parse_args()
    limit = len(BASELINE_CASES) if args.full else args.limit
    print(json.dumps(run_baseline(limit=limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
