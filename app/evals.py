"""Evals deterministicas para os fluxos operacionais da squad."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import json
from typing import Any

from app.observability import build_run_config
from app.operational_store import DEFAULT_DB_PATH, metrics_snapshot, record_eval_result, record_run
from app.retry_policy import initialize_retry_state
from app.run_registry import generate_run_id
from app.intake import decide_intake


@dataclass(frozen=True)
class EvalScenario:
    scenario_id: str
    user_goal: str
    expected_flow: str
    required_agents: tuple[str, ...]
    forbidden_agents: tuple[str, ...] = ()
    expected_c3_agents: tuple[str, ...] = ()


SCENARIOS = (
    EvalScenario(
        "feature_delivery",
        "Criar feature de cadastro e validar release.",
        "delivery_core",
        ("product", "qa_planning", "engineering", "operator", "engineering_review", "qa_execution", "cos"),
        ("discovery",),
    ),
    EvalScenario(
        "bugfix",
        "Corrigir bug no login e validar a falha.",
        "bugfix",
        ("engineering", "operator", "engineering_review", "qa_execution", "cos"),
        ("product", "discovery"),
    ),
    EvalScenario(
        "documentation",
        "Documentar a arquitetura no README.",
        "docs",
        ("writing", "cos"),
        ("engineering", "discovery"),
    ),
    EvalScenario(
        "code_review",
        "Fazer code review do PR atual.",
        "review",
        ("engineering_review", "cos"),
        ("operator", "qa_execution"),
    ),
    EvalScenario(
        "product_decision",
        "Decidir prioridade entre duas features.",
        "decision_only",
        ("product", "cos"),
        ("engineering", "operator"),
    ),
    EvalScenario(
        "market_research",
        "Fazer benchmark de concorrentes para validar hipotese de mercado.",
        "research_only",
        ("discovery", "cos"),
        ("engineering", "operator"),
    ),
    EvalScenario(
        "discovery_delivery",
        "Criar MVP com benchmark de concorrentes para validar posicionamento.",
        "delivery_with_discovery",
        ("discovery", "product", "qa_planning", "engineering", "operator", "engineering_review", "qa_execution", "cos"),
    ),
    EvalScenario(
        "sensitive_authenticated_ui",
        "Criar interface de cadastro de paciente com dados pessoais, login e autenticacao.",
        "delivery_core",
        ("product", "ux_ui", "qa_planning", "engineering", "privacy", "appsec", "operator", "engineering_review", "qa_execution", "cos"),
        ("discovery",),
    ),
    EvalScenario(
        "security_code_review",
        "Fazer code review de autenticacao e permissoes do login.",
        "review",
        ("engineering_review", "appsec", "cos"),
        ("operator", "qa_execution", "product"),
    ),
)


def _initial_state(scenario: EvalScenario, run_id: str, workspace_root: str | Path) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "user_goal": scenario.user_goal,
        "workspace_root": str(workspace_root),
        "manual_validation_result": "Validacao mock concluida para eval deterministica.",
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


def assess_scenario(scenario: EvalScenario, result: dict[str, Any]) -> tuple[float, list[str]]:
    findings: list[str] = []
    outputs = set(result.get("structured_outputs", {}))
    packet = result.get("operational_packet", {})
    checks = result.get("orchestrator_checks", {})

    checks_passed = 0
    checks_total = 6

    if result.get("active_flow") == scenario.expected_flow:
        checks_passed += 1
    else:
        findings.append(f"Fluxo esperado {scenario.expected_flow}; recebido {result.get('active_flow')}.")

    missing = sorted(set(scenario.required_agents) - outputs)
    if not missing:
        checks_passed += 1
    else:
        findings.append(f"Agentes requeridos ausentes: {', '.join(missing)}.")

    unexpected = sorted(
        (outputs - set(scenario.required_agents))
        | (set(scenario.forbidden_agents) & outputs)
    )
    if not unexpected:
        checks_passed += 1
    else:
        findings.append(f"Agentes desnecessarios acionados: {', '.join(unexpected)}.")

    if packet.get("execution_ready") and packet.get("recommended_actions") and packet.get("verification_steps"):
        checks_passed += 1
    else:
        findings.append("Pacote operacional final nao esta pronto ou acionavel.")

    if outputs and outputs == set(checks) and all(check.get("schema_valid", False) for check in checks.values()):
        checks_passed += 1
    else:
        findings.append("Ao menos um agente falhou no schema estruturado.")

    c3_agents = {
        agent_name
        for agent_name, output in result.get("structured_outputs", {}).items()
        if output.get("summary", {}).get("ece") == "C3"
    }
    if c3_agents == set(scenario.expected_c3_agents):
        checks_passed += 1
    else:
        findings.append(
            "Outputs C3 esperados "
            f"{sorted(scenario.expected_c3_agents)}; recebidos {sorted(c3_agents)}."
        )

    return round(checks_passed / checks_total * 10, 2), findings


def run_evaluation(
    *,
    workspace_root: str | Path = ".",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    from app.graph import graph

    evaluation_id = f"eval_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    scenario_results: list[dict[str, Any]] = []

    for scenario in SCENARIOS:
        run_id = generate_run_id()
        preflight = decide_intake(scenario.user_goal)
        started_at = perf_counter()
        result = graph.invoke(
            _initial_state(scenario, run_id, workspace_root),
            config=build_run_config(
                run_id=run_id,
                active_flow=scenario.expected_flow,
                mode="eval",
                on_demand_agents=preflight.on_demand_agents,
            ),
        )
        duration_ms = round((perf_counter() - started_at) * 1000)
        score, findings = assess_scenario(scenario, result)
        passed = score >= 8.7

        record_run(
            result,
            run_id=run_id,
            user_goal=scenario.user_goal,
            duration_ms=duration_ms,
            db_path=db_path,
        )
        record_eval_result(
            evaluation_id=evaluation_id,
            scenario_id=scenario.scenario_id,
            run_id=run_id,
            passed=passed,
            score=score,
            findings=findings,
            db_path=db_path,
        )
        scenario_results.append(
            {
                "scenario_id": scenario.scenario_id,
                "active_flow": result.get("active_flow", ""),
                "agents": sorted(result.get("structured_outputs", {}).keys()),
                "score": score,
                "passed": passed,
                "findings": findings,
                "duration_ms": duration_ms,
            }
        )

    average_score = round(sum(item["score"] for item in scenario_results) / len(scenario_results), 2)
    return {
        "evaluation_id": evaluation_id,
        "scenario_count": len(scenario_results),
        "average_score": average_score,
        "passed": average_score >= 8.7 and all(item["passed"] for item in scenario_results),
        "scenarios": scenario_results,
        "metrics": metrics_snapshot(db_path),
    }


if __name__ == "__main__":
    report = run_evaluation()
    print(json.dumps(report, ensure_ascii=False, indent=2))
