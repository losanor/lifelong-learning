import unittest
from pathlib import Path

from app.evals import EvalScenario, assess_scenario
from app.observability import build_run_config
from app.operational_store import (
    baseline_snapshot,
    metrics_snapshot,
    record_baseline_result,
    record_run,
    update_human_review,
)
from app.orchestrator import inspect_agent_output, update_orchestrator_checks
from app.retry_policy import initialize_retry_state, resolve_retry_permission
from app.structured_output import CoSOutputEnvelope, mock_agent_output, safe_parse_agent_output
from app.intake import decide_intake
from app.workspace_context import capture_workspace_context, format_workspace_context
from cmo_especializado import CMOFactory
from contracts import ECE, Fase, SharedMemory


class OperationalContractsTest(unittest.TestCase):
    def test_structured_summary_check_detects_valid_c3(self):
        payload = mock_agent_output("discovery", "# Discovery", ece=ECE.C3)
        output, errors = safe_parse_agent_output(payload, "discovery")
        check = inspect_agent_output(output, errors)

        self.assertTrue(check["structured_summary_received"])
        self.assertEqual(check["ece"], "C3")
        self.assertTrue(check["c3_detected"])
        self.assertTrue(check["needs_cos_attention"])

    def test_structured_summary_check_rejects_invalid_json(self):
        output, errors = safe_parse_agent_output(
            "# Product Brief\nSem envelope JSON.",
            "product",
        )
        check = inspect_agent_output(output, errors)

        self.assertTrue(check["structured_summary_received"])
        self.assertEqual(check["ece"], "C3")
        self.assertTrue(errors)
        self.assertTrue(check["needs_cos_attention"])

        state = update_orchestrator_checks(
            {},
            agent_name="product",
            output=output,
            validation_errors=errors,
        )
        self.assertFalse(state["product"]["schema_valid"])

    def test_cos_route_is_read_from_validated_envelope(self):
        payload = mock_agent_output(
            "cos",
            "# CoS",
            ece=ECE.C1,
            decision="RETURN_TO_ENGINEERING",
            route_action="ROUTE_TO_ENGINEERING",
        )
        output, errors = safe_parse_agent_output(payload, "cos")

        self.assertFalse(errors)
        self.assertIsInstance(output, CoSOutputEnvelope)
        self.assertEqual(output.decision, "RETURN_TO_ENGINEERING")
        self.assertEqual(output.route_action, "ROUTE_TO_ENGINEERING")

    def test_structured_output_has_actionable_operational_artifact(self):
        payload = mock_agent_output("writing", "# Doc", ece=ECE.C1)
        output, errors = safe_parse_agent_output(payload, "writing")

        self.assertFalse(errors)
        self.assertTrue(output.operational_artifact.execution_ready)
        self.assertTrue(output.operational_artifact.recommended_actions)
        self.assertTrue(output.operational_artifact.verification_steps)

    def test_workspace_context_reports_docs_and_git_policy(self):
        context = capture_workspace_context()
        formatted = format_workspace_context(context)

        self.assertIn("README.md", context.documentation_refs)
        self.assertTrue(any(ref.endswith(".py") for ref in context.project_refs))
        if context.git_repo:
            self.assertIn("Git local detectado", formatted)
        else:
            self.assertIn("nao presuma PR", formatted)

    def test_operational_store_records_run_metrics(self):
        db_path = Path("data") / "test_runtime.sqlite3"
        if db_path.exists():
            db_path.unlink()
        try:
            output, _ = safe_parse_agent_output(
                mock_agent_output("writing", "# Doc", ece=ECE.C1),
                "writing",
            )
            result = {
                "active_flow": "docs",
                "route_status": "ended",
                "cos_decision": "GO",
                "cos_route_action": "END_CYCLE",
                "route_decision": "end_cycle",
                "human_escalation_created": False,
                "operational_packet": {"execution_ready": True},
                "structured_outputs": {"writing": output.model_dump(mode="json")},
                "orchestrator_checks": {"writing": {"schema_valid": True}},
            }
            record_run(result, run_id="unit_run", user_goal="Documentar README", duration_ms=12, db_path=db_path)
            metrics = metrics_snapshot(db_path)

            self.assertEqual(metrics["run_count"], 1)
            self.assertEqual(metrics["execution_ready_rate"], 1.0)
            self.assertEqual(metrics["average_agents_per_run"], 1.0)
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_eval_assessment_rewards_actionable_correct_flow(self):
        output, _ = safe_parse_agent_output(
            mock_agent_output("writing", "# Doc", ece=ECE.C1),
            "writing",
        )
        scenario = EvalScenario("docs", "Documentar README.", "docs", ("writing",), ("engineering",))
        result = {
            "active_flow": "docs",
            "structured_outputs": {"writing": output.model_dump(mode="json")},
            "operational_packet": {
                "execution_ready": True,
                "recommended_actions": ["Atualizar README."],
                "verification_steps": ["Revisar conteudo."],
            },
            "orchestrator_checks": {"writing": {"schema_valid": True}},
        }

        score, findings = assess_scenario(scenario, result)

        self.assertEqual(score, 10.0)
        self.assertFalse(findings)

    def test_baseline_review_stores_human_rubric(self):
        db_path = Path("data") / "test_baseline.sqlite3"
        if db_path.exists():
            db_path.unlink()
        try:
            record_baseline_result(
                baseline_id="baseline_unit",
                scenario_id="case_unit",
                run_id="run_unit",
                trace_id="trace_unit",
                automatic_score=9.2,
                findings=[],
                db_path=db_path,
            )
            update_human_review(
                baseline_id="baseline_unit",
                scenario_id="case_unit",
                correctness=9,
                practical_utility=8,
                scope_control=10,
                next_step_clarity=9,
                execution_confidence=8,
                notes="Aprovado com pequena ressalva.",
                db_path=db_path,
            )
            report = baseline_snapshot("baseline_unit", db_path)

            self.assertEqual(report["scenario_count"], 1)
            self.assertEqual(report["results"][0]["human_average_score"], 8.8)
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_operator_cmo_rejects_c3_spec(self):
        memoria = SharedMemory(
            projeto="squad",
            objetivo_atual="Entregar um MVP testavel.",
            fase_atual=Fase.IMPLEMENTACAO,
        )

        with self.assertRaises(ValueError):
            CMOFactory.para_operator(
                memoria=memoria,
                tarefa="Implementar build.",
                spec_tecnica_ref="spec.md",
                spec_ece=ECE.C3,
                test_cases_ref="qa.md",
                tarefa_implementacao="Criar a tela.",
            )

    def test_operator_retry_blocks_after_two_returns(self):
        state = initialize_retry_state()
        state = resolve_retry_permission("ROUTE_TO_OPERATOR", state)
        state = resolve_retry_permission("ROUTE_TO_OPERATOR", state)
        blocked = resolve_retry_permission("ROUTE_TO_OPERATOR", state)

        self.assertFalse(blocked["retry_allowed"])
        self.assertTrue(blocked["retry_blocked"])

    def test_intake_skips_discovery_for_delivery_goal(self):
        decision = decide_intake("Criar feature de cadastro e validar release.")

        self.assertFalse(decision.needs_discovery)
        self.assertEqual(decision.active_flow, "delivery_core")
        self.assertIn("product", decision.fixed_agents)

    def test_intake_calls_discovery_for_market_goal(self):
        decision = decide_intake("Fazer benchmark de concorrentes para validar hipotese de mercado.")

        self.assertTrue(decision.needs_discovery)
        self.assertEqual(decision.active_flow, "research_only")
        self.assertIn("discovery", decision.on_demand_agents)

    def test_intake_selects_operational_flows(self):
        self.assertEqual(
            decide_intake("Corrigir bug no login e validar a falha.").active_flow,
            "bugfix",
        )
        self.assertEqual(
            decide_intake("Documentar a arquitetura no README.").active_flow,
            "docs",
        )
        self.assertEqual(
            decide_intake("Fazer code review do PR atual.").active_flow,
            "review",
        )
        self.assertEqual(
            decide_intake("Decidir prioridade entre duas features.").active_flow,
            "decision_only",
        )
        self.assertEqual(
            decide_intake("Criar MVP com benchmark de concorrentes.").active_flow,
            "delivery_with_discovery",
        )

    def test_intake_signals_specialist_gates_for_sensitive_interface(self):
        decision = decide_intake(
            "Criar interface de cadastro de paciente com dados pessoais, login e autenticacao."
        )

        self.assertEqual(decision.active_flow, "delivery_core")
        self.assertIn("ux_ui", decision.on_demand_agents)
        self.assertIn("privacy", decision.on_demand_agents)
        self.assertIn("appsec", decision.on_demand_agents)

    def test_langsmith_config_tags_specialist_gates(self):
        config = build_run_config(
            run_id="run_observe",
            active_flow="delivery_core",
            on_demand_agents=("privacy", "appsec"),
            git_repo=True,
        )

        self.assertIn("gate:privacy", config["tags"])
        self.assertIn("gate:appsec", config["tags"])
        self.assertEqual(config["metadata"]["gate_count"], 2)
        self.assertTrue(config["metadata"]["git_repo"])

    def test_review_intake_signals_appsec_without_delivery_flow(self):
        decision = decide_intake("Fazer code review de autenticacao e permissoes do login.")

        self.assertEqual(decision.active_flow, "review")
        self.assertIn("appsec", decision.on_demand_agents)


if __name__ == "__main__":
    unittest.main()
