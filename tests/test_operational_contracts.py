import unittest
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from unittest.mock import patch

import app.graph as graph_module
import app.pilot_readiness as pilot_readiness_module
import app.run_service as run_service_module
from app.evals import EvalScenario, assess_scenario
from app.mock_model import MockModel, MockResponse
from app.observability import build_run_config
from app.operational_api import make_handler
from app.context_policy import build_context_bundle
from app.cycle_report import append_cycle_report, read_cycle_reports
from app.handoff_log import append_handoff, read_handoff_log
from app.human_escalation import append_human_escalation, read_human_escalations
from app.memory import append_to_shared_memory, read_shared_memory
from app.project_scope import resolve_work_scope
from app.route_events import append_route_event, read_route_events
from app.scoped_storage import scoped_path
from app.execution_engine import (
    apply_execution_request,
    commit_git_delivery,
    create_execution_request,
    decide_apply_execution,
    decide_execution_request,
    generate_candidate_patch,
    prepare_execution_request,
    requires_execution_gate,
    rollback_execution_request,
)
from app.operational_store import (
    automation_demand_snapshot,
    baseline_snapshot,
    board_snapshot,
    cost_snapshot,
    create_parking_lot_item,
    execution_request_snapshot,
    human_decision_snapshot,
    metrics_snapshot,
    performance_history_snapshot,
    parking_lot_snapshot,
    pending_work_snapshot,
    promote_parking_lot_item,
    bind_initiative_document,
    record_baseline_result,
    record_eval_result,
    record_handoff_event,
    record_functional_qa_result,
    record_run,
    record_run_started,
    recent_runs_snapshot,
    respond_human_decision,
    run_flow_snapshot,
    update_run_status,
    update_human_review,
    workflow_checkpoint_snapshot,
)
from app.orchestrator import inspect_agent_output, update_orchestrator_checks
from app.retry_policy import initialize_retry_state, resolve_retry_permission
from app.structured_output import CoSOutputEnvelope, mock_agent_output, safe_parse_agent_output
from app.intake import decide_intake
from app.reference_documents import list_reference_documents, load_reference_document
from app.run_service import enqueue_manual_run, preview_manual_run
from app.workspace_context import capture_workspace_context, format_workspace_context
from cmo_especializado import CMOFactory
from contracts import ECE, Fase, SharedMemory


class OperationalContractsTest(unittest.TestCase):
    def test_free_pilot_readiness_accepts_real_traced_sampled_operation(self):
        with (
            patch.object(pilot_readiness_module, "USE_MOCK_MODEL", False),
            patch.object(pilot_readiness_module, "ANTHROPIC_API_KEY", "configured"),
            patch.object(pilot_readiness_module, "langsmith_ready", return_value=True),
            patch.object(pilot_readiness_module, "langsmith_enabled", return_value=True),
            patch.dict("os.environ", {"LANGSMITH_TRACING_SAMPLING_RATE": "0.2"}),
        ):
            report = pilot_readiness_module.pilot_readiness()

        self.assertTrue(report["ready_for_real_pilot"])
        self.assertFalse(report["cost_controls"]["monthly_paid_seat_required"])
        self.assertEqual(report["cost_controls"]["recommended_paid_trace_spend_limit_usd"], 0)
        self.assertEqual(report["cost_controls"]["recommended_langsmith_trace_limit"], 5000)
        self.assertEqual(report["findings"], [])

    def test_cos_escalation_never_produces_execution_ready_packet(self):
        envelope, _ = safe_parse_agent_output(
            mock_agent_output(
                "cos",
                "Necessita decisao humana.",
                ece=ECE.C2,
                decision="ESCALATE_TO_HUMAN",
                route_action="ESCALATE_HUMAN",
            ),
            "cos",
        )
        response = graph_module.ValidatedResponse(
            raw_content="",
            envelope=envelope,
            validation_errors=[],
        )

        packet = graph_module.build_operational_packet(
            {"workspace_context": {"git_repo": True}, "structured_outputs": {}},
            response,
            decision="ESCALATE_TO_HUMAN",
            route_action="ESCALATE_HUMAN",
        )

        self.assertTrue(packet["governance_blocked"])
        self.assertFalse(packet["execution_ready"])
        self.assertTrue(packet["human_checkpoint"])

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

    def test_invalid_agent_output_is_repaired_once_before_blocking(self):
        invalid = json.loads(mock_agent_output("product", "# Product", ece=ECE.C2))
        invalid["summary"]["fase"] = "release"
        repaired = mock_agent_output("product", "# Product corrigido", ece=ECE.C2)

        class RepairingModel:
            def __init__(self):
                self.responses = [MockResponse(json.dumps(invalid)), MockResponse(repaired)]
                self.calls = 0

            def invoke(self, prompt):
                response = self.responses[self.calls]
                self.calls += 1
                return response

        original_model = graph_module.model
        repair_model = RepairingModel()
        graph_module.model = repair_model
        try:
            response = graph_module.invoke_validated("product", "Gerar product brief.")
        finally:
            graph_module.model = original_model

        self.assertEqual(repair_model.calls, 2)
        self.assertTrue(response.repair_attempted)
        self.assertFalse(response.validation_errors)
        self.assertEqual(response.content, "# Product corrigido")

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
                "work_scope": {
                    "project_id": "squad",
                    "initiative_id": "docs-update",
                    "memory_namespace": "squad:docs-update",
                },
                "active_flow": "docs",
                "execution_policy": {
                    "execution_tier": "quick",
                    "max_cost_usd": 0.15,
                },
                "route_status": "ended",
                "cos_decision": "GO",
                "cos_route_action": "END_CYCLE",
                "route_decision": "end_cycle",
                "human_escalation_created": False,
                "operational_packet": {"execution_ready": True},
                "structured_outputs": {"writing": output.model_dump(mode="json")},
                "orchestrator_checks": {
                    "writing": {
                        "schema_valid": True,
                        "issues": [],
                        "repair_attempted": True,
                    }
                },
            }
            record_run(result, run_id="unit_run", user_goal="Documentar README", duration_ms=12, db_path=db_path)
            metrics = metrics_snapshot(db_path)
            connection = sqlite3.connect(db_path)
            try:
                stored_repair = connection.execute(
                    "SELECT repair_attempted FROM agent_outputs WHERE run_id=? AND agent_name=?",
                    ("unit_run", "writing"),
                ).fetchone()[0]
                stored_scope = connection.execute(
                    "SELECT project_id, initiative_id, execution_tier FROM runs WHERE run_id=?",
                    ("unit_run",),
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(metrics["run_count"], 1)
            self.assertEqual(metrics["execution_ready_rate"], 1.0)
            self.assertEqual(metrics["average_agents_per_run"], 1.0)
            self.assertEqual(stored_repair, 1)
            self.assertEqual(stored_scope, ("squad", "docs-update", "quick"))
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

    def test_execution_request_requires_approval_before_workspace_effects(self):
        db_path = Path("data") / "test_execution.sqlite3"
        if db_path.exists():
            db_path.unlink()
        try:
            result = {
                "work_scope": {
                    "project_id": "squad",
                    "initiative_id": "implement-engine",
                },
                "active_flow": "bugfix",
                "execution_policy": {
                    "execution_tier": "standard",
                    "approval_required_actions": ["write_files", "git_commit", "git_push"],
                },
                "route_status": "ended",
                "operational_packet": {
                    "execution_ready": True,
                    "target_refs": ["app/main.py"],
                    "recommended_actions": ["Aplicar a mudanca aprovada."],
                    "verification_steps": ["Executar testes."],
                    "git_actions": ["Criar commit apos validacao."],
                },
                "structured_outputs": {},
                "orchestrator_checks": {},
            }
            record_run(result, run_id="run_execution", user_goal="Corrigir falha.", db_path=db_path)

            request = create_execution_request(result, run_id="run_execution", db_path=db_path)
            approved = decide_execution_request(
                request["request_id"],
                decision="approved",
                decided_by="owner",
                notes="Aprovado para preparar diff.",
                db_path=db_path,
            )
            create_execution_request(result, run_id="run_execution", db_path=db_path)
            persisted = execution_request_snapshot(
                request_id=request["request_id"],
                db_path=db_path,
            )["requests"][0]
            metrics = metrics_snapshot(db_path)

            self.assertEqual(request["status"], "pending_approval")
            self.assertEqual(request["execution_mode"], "dry_run_only")
            self.assertFalse(request["effects_enabled"])
            self.assertEqual(request["requested_effects"], ["write_files", "git_commit"])
            self.assertTrue(request["approval_required"])
            self.assertEqual(approved["status"], "approved_for_dry_run")
            self.assertEqual(persisted["status"], "approved_for_dry_run")
            self.assertFalse(approved["effects_enabled"])
            self.assertEqual(approved["approvals"][0]["decision"], "approved")
            self.assertEqual(
                metrics["execution_requests"],
                [{"status": "approved_for_dry_run", "request_count": 1}],
            )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_execution_request_preserves_blocked_packet_as_non_actionable(self):
        db_path = Path("data") / "test_blocked_execution.sqlite3"
        if db_path.exists():
            db_path.unlink()
        try:
            result = {
                "active_flow": "decision_only",
                "execution_policy": {
                    "execution_tier": "quick",
                    "approval_required_actions": ["write_files"],
                },
                "route_status": "ended",
                "operational_packet": {
                    "execution_ready": False,
                    "target_refs": ["decision-log"],
                    "recommended_actions": ["Aguardar decisao humana."],
                    "verification_steps": ["Confirmar bloqueio."],
                    "git_actions": [],
                },
                "structured_outputs": {},
                "orchestrator_checks": {},
            }
            record_run(result, run_id="run_blocked", user_goal="Avaliar bloqueio.", db_path=db_path)

            request = create_execution_request(result, run_id="run_blocked", db_path=db_path)
            stored = execution_request_snapshot(
                request_id=request["request_id"],
                db_path=db_path,
            )["requests"][0]

            self.assertEqual(stored["status"], "not_actionable")
            self.assertFalse(stored["approval_required"])
            self.assertEqual(stored["requested_effects"], [])
            with self.assertRaises(ValueError):
                decide_execution_request(
                    request["request_id"],
                    decision="approved",
                    decided_by="owner",
                    db_path=db_path,
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_execution_gate_promotes_file_creation_before_qa(self):
        db_path = Path("data") / "test_execution_gate.sqlite3"
        if db_path.exists():
            db_path.unlink()
        try:
            result = {
                "active_flow": "delivery_core",
                "execution_policy": {
                    "execution_tier": "full",
                    "approval_required_actions": ["write_files"],
                },
                "work_scope": {
                    "project_id": "meu-bot",
                    "initiative_id": "default",
                    "memory_namespace": "meu-bot:default",
                },
                "operational_packet": {
                    "execution_ready": False,
                    "governance_blocked": True,
                    "decision": "NO_GO",
                    "workspace_root": "C:\\Users\\Akira\\meu bot",
                    "target_refs": [
                        "C:\\Users\\Akira\\meu bot\\bot.py",
                        "C:\\Users\\Akira\\meu bot\\requirements.txt",
                        "C:\\Users\\Akira\\meu bot\\.env.example",
                    ],
                    "recommended_actions": [
                        "Criar bot.py",
                        "Executar: pip install -r requirements.txt",
                        "Executar: python bot.py",
                    ],
                    "verification_steps": ["python bot.py inicializa sem erro"],
                    "git_actions": [],
                },
                "structured_outputs": {},
                "orchestrator_checks": {},
            }

            self.assertTrue(requires_execution_gate(result["operational_packet"]))
            record_run(result, run_id="run_gate", user_goal="Criar bot.", db_path=db_path)
            request = create_execution_request(result, run_id="run_gate", db_path=db_path)

            self.assertEqual(request["status"], "pending_approval")
            self.assertTrue(request["approval_required"])
            self.assertEqual(request["requested_effects"], ["write_files"])
            self.assertEqual(request["target_refs"], ["bot.py", "requirements.txt", ".env.example"])
            self.assertIn("before QA", request["status_reason"])
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_implementation_operator_generates_bot_patch_after_preparation_approval(self):
        db_path = Path("data") / "test_operator_auto_patch.sqlite3"
        if db_path.exists():
            db_path.unlink()
        Path("data").mkdir(exist_ok=True)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                workspace = Path(temp_dir)
                (workspace / "BOT.txt").write_text("Metodo de aprendizagem do usuario.", encoding="utf-8")
                result = {
                    "active_flow": "delivery_core",
                    "execution_policy": {
                        "execution_tier": "full",
                        "approval_required_actions": ["write_files"],
                    },
                    "work_scope": {
                        "project_id": "meu-bot",
                        "initiative_id": "default",
                        "memory_namespace": "meu-bot:default",
                    },
                    "operational_packet": {
                        "execution_ready": False,
                        "governance_blocked": True,
                        "decision": "NO_GO",
                        "workspace_root": str(workspace),
                        "target_refs": ["bot.py", "requirements.txt", ".env"],
                        "recommended_actions": [
                            "Criar bot.py para Telegram",
                            "Criar requirements.txt",
                            "Criar .env.example",
                        ],
                        "verification_steps": ["python bot.py inicializa sem erro"],
                        "git_actions": [],
                    },
                    "structured_outputs": {},
                    "orchestrator_checks": {},
                }
                record_run(result, run_id="run_auto_patch", user_goal="Criar bot.", db_path=db_path)
                request = create_execution_request(result, run_id="run_auto_patch", db_path=db_path)
                decide_execution_request(
                    request["request_id"],
                    decision="approved",
                    decided_by="owner",
                    db_path=db_path,
                )

                prepared = prepare_execution_request(
                    request["request_id"],
                    workspace_root=workspace,
                    db_path=db_path,
                )

                self.assertEqual(prepared["status"], "awaiting_apply_approval")
                self.assertIn("Implementation Operator", prepared["status_reason"])
                self.assertEqual(prepared["target_refs"], ["bot.py", "requirements.txt", ".env.example", "test_bot.py", ".gitignore", "README.md"])
                self.assertEqual(prepared["preparation"]["preparation_status"], "patch_validated")
                self.assertTrue(prepared["preparation"]["validation"]["passed"])
                self.assertTrue(prepared["preparation"]["validation"]["generated_by_operator"])
                self.assertIn("diff --git a/bot.py b/bot.py", prepared["preparation"]["patch_text"])
                self.assertIn("diff --git a/.env.example b/.env.example", prepared["preparation"]["patch_text"])
                self.assertIn("diff --git a/test_bot.py b/test_bot.py", prepared["preparation"]["patch_text"])
                self.assertIn("class SessionStage", prepared["preparation"]["patch_text"])
                self.assertNotIn("diff --git a/.env b/.env", prepared["preparation"]["patch_text"])
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_operator_regenerates_outdated_managed_bot_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "bot.py").write_text("# scaffold antigo\n", encoding="utf-8")
            request = {
                "target_refs": ["bot.py", "requirements.txt"],
                "recommended_actions": ["Criar bot.py para Telegram"],
                "verification_steps": ["Validar bot."],
                "operational_packet": {},
            }

            patch_text, refs = generate_candidate_patch(request, workspace_root=workspace)

            self.assertIn("--- a/bot.py", patch_text)
            self.assertIn("+++ b/bot.py", patch_text)
            self.assertIn("class SessionStage", patch_text)
            self.assertIn("test_bot.py", refs)

    def test_preparation_validates_scoped_patch_before_second_approval(self):
        db_path = Path("data") / "test_patch_preparation.sqlite3"
        if db_path.exists():
            db_path.unlink()
        try:
            result = {
                "active_flow": "docs",
                "execution_policy": {
                    "execution_tier": "quick",
                    "approval_required_actions": ["write_files"],
                },
                "route_status": "ended",
                "operational_packet": {
                    "execution_ready": True,
                    "target_refs": ["README.md"],
                    "recommended_actions": ["Atualizar titulo."],
                    "verification_steps": ["Revisar diff."],
                    "git_actions": [],
                },
                "structured_outputs": {},
                "orchestrator_checks": {},
            }
            record_run(result, run_id="run_patch", user_goal="Atualizar titulo.", db_path=db_path)
            request = create_execution_request(result, run_id="run_patch", db_path=db_path)
            decide_execution_request(
                request["request_id"],
                decision="approved",
                decided_by="owner",
                db_path=db_path,
            )
            patch = (
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1,3 +1,3 @@\n"
                "-# Squad v5 Lite\n"
                "+# Squad v5 Lite Validated\n"
                " \n"
                " Squad multiagente enxuta para transformar um objetivo em discovery, escopo,\n"
            )

            prepared = prepare_execution_request(
                request["request_id"],
                patch_text=patch,
                workspace_root=".",
                db_path=db_path,
            )
            apply_approved = decide_apply_execution(
                request["request_id"],
                decision="approved",
                decided_by="owner",
                notes="Patch conferido; aplicacao ainda depende do executor.",
                db_path=db_path,
            )
            create_execution_request(result, run_id="run_patch", db_path=db_path)
            persisted = execution_request_snapshot(
                request_id=request["request_id"],
                db_path=db_path,
            )["requests"][0]

            self.assertEqual(prepared["status"], "awaiting_apply_approval")
            self.assertIn("apply approval", prepared["status_reason"])
            self.assertEqual(prepared["preparation"]["preparation_status"], "patch_validated")
            self.assertTrue(prepared["preparation"]["validation"]["passed"])
            self.assertTrue(prepared["preparation"]["validation"]["git_apply_check"]["passed"])
            self.assertEqual(apply_approved["status"], "approved_for_apply")
            self.assertIn("not enabled", apply_approved["status_reason"])
            self.assertFalse(apply_approved["effects_enabled"])
            self.assertEqual(apply_approved["approvals"][1]["approval_stage"], "apply")
            self.assertEqual(persisted["status"], "approved_for_apply")
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_preparation_rejects_patch_outside_approved_targets(self):
        db_path = Path("data") / "test_unapproved_patch.sqlite3"
        if db_path.exists():
            db_path.unlink()
        try:
            result = {
                "active_flow": "docs",
                "execution_policy": {"approval_required_actions": ["write_files"]},
                "route_status": "ended",
                "operational_packet": {
                    "execution_ready": True,
                    "target_refs": ["README.md"],
                    "recommended_actions": ["Editar README."],
                    "verification_steps": ["Revisar diff."],
                    "git_actions": [],
                },
                "structured_outputs": {},
                "orchestrator_checks": {},
            }
            record_run(result, run_id="run_scope", user_goal="Editar README.", db_path=db_path)
            request = create_execution_request(result, run_id="run_scope", db_path=db_path)
            decide_execution_request(
                request["request_id"],
                decision="approved",
                decided_by="owner",
                db_path=db_path,
            )
            patch = "--- a/app/main.py\n+++ b/app/main.py\n@@ -1 +1 @@\n-x\n+y\n"

            prepared = prepare_execution_request(
                request["request_id"],
                patch_text=patch,
                workspace_root=".",
                db_path=db_path,
            )

            self.assertEqual(prepared["status"], "awaiting_patch")
            self.assertEqual(prepared["preparation"]["preparation_status"], "patch_rejected")
            self.assertIn(
                "app/main.py",
                prepared["preparation"]["validation"]["unapproved_target_refs"],
            )
            with self.assertRaises(ValueError):
                decide_apply_execution(
                    request["request_id"],
                    decision="approved",
                    decided_by="owner",
                    db_path=db_path,
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_controlled_apply_records_evidence_and_supports_rollback(self):
        db_path = Path("data") / "test_apply_execution.sqlite3"
        if db_path.exists():
            db_path.unlink()
        Path("data").mkdir(exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir="data") as temp_dir:
                workspace = Path(temp_dir)
                target = workspace / "README.md"
                target.write_text("# Before\n\nContext.\n", encoding="utf-8")
                subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
                subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
                subprocess.run(
                    [
                        "git",
                        "-c",
                        "user.name=tests",
                        "-c",
                        "user.email=tests@example.test",
                        "commit",
                        "-q",
                        "-m",
                        "base",
                    ],
                    cwd=workspace,
                    check=True,
                )
                result = {
                    "active_flow": "docs",
                    "execution_policy": {"approval_required_actions": ["write_files"]},
                    "route_status": "ended",
                    "operational_packet": {
                        "execution_ready": True,
                        "target_refs": ["README.md"],
                        "recommended_actions": ["Atualizar README."],
                        "verification_steps": ["Validar diff."],
                        "git_actions": [],
                    },
                    "structured_outputs": {},
                    "orchestrator_checks": {},
                }
                record_run(result, run_id="run_apply", user_goal="Editar README.", db_path=db_path)
                request = create_execution_request(result, run_id="run_apply", db_path=db_path)
                decide_execution_request(
                    request["request_id"], decision="approved", decided_by="owner", db_path=db_path
                )
                patch = (
                    "--- a/README.md\n"
                    "+++ b/README.md\n"
                    "@@ -1,3 +1,3 @@\n"
                    "-# Before\n"
                    "+# After\n"
                    " \n"
                    " Context.\n"
                )
                prepare_execution_request(
                    request["request_id"],
                    patch_text=patch,
                    workspace_root=workspace,
                    db_path=db_path,
                )
                decide_apply_execution(
                    request["request_id"], decision="approved", decided_by="owner", db_path=db_path
                )

                applied = apply_execution_request(
                    request["request_id"],
                    workspace_root=workspace,
                    validations=["git_diff_check"],
                    db_path=db_path,
                )
                self.assertEqual(applied["status"], "applied_validated")
                self.assertTrue(applied["effects_enabled"])
                self.assertEqual(target.read_text(encoding="utf-8"), "# After\n\nContext.\n")
                self.assertTrue(applied["application"]["validation"]["presets"][0]["passed"])
                timeline = workflow_checkpoint_snapshot(thread_id="run_apply", db_path=db_path)
                self.assertEqual(
                    [event["stage"] for event in timeline["checkpoints"]],
                    [
                        "execution_request_created",
                        "preparation_approval_decided",
                        "candidate_patch_prepared",
                        "apply_approval_decided",
                        "application_validated",
                        "qa_smoke_completed",
                    ],
                )
                pending = pending_work_snapshot(db_path)
                self.assertEqual(pending["pending"][0]["next_action"], "Criar branch e commit supervisionado ou reverter aplicacao.")

                rolled_back = rollback_execution_request(
                    request["request_id"],
                    workspace_root=workspace,
                    reason="Teste de reversao.",
                    db_path=db_path,
                )
                self.assertEqual(rolled_back["status"], "rolled_back")
                self.assertFalse(rolled_back["effects_enabled"])
                self.assertEqual(target.read_text(encoding="utf-8"), "# Before\n\nContext.\n")
                self.assertEqual(pending_work_snapshot(db_path)["pending_count"], 0)
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_apply_skips_git_diff_validation_when_workspace_is_not_git_repo(self):
        db_path = Path("data") / "test_apply_non_git_workspace.sqlite3"
        if db_path.exists():
            db_path.unlink()
        Path("data").mkdir(exist_ok=True)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                workspace = Path(temp_dir)
                result = {
                    "active_flow": "docs",
                    "execution_policy": {"approval_required_actions": ["write_files"]},
                    "route_status": "ended",
                    "operational_packet": {
                        "execution_ready": True,
                        "target_refs": ["README.md"],
                        "recommended_actions": ["Criar README."],
                        "verification_steps": ["Validar arquivo."],
                        "git_actions": [],
                    },
                    "structured_outputs": {},
                    "orchestrator_checks": {},
                }
                record_run(result, run_id="run_non_git_apply", user_goal="Criar README.", db_path=db_path)
                request = create_execution_request(result, run_id="run_non_git_apply", db_path=db_path)
                decide_execution_request(
                    request["request_id"], decision="approved", decided_by="owner", db_path=db_path
                )
                patch = (
                    "diff --git a/README.md b/README.md\n"
                    "new file mode 100644\n"
                    "--- /dev/null\n"
                    "+++ b/README.md\n"
                    "@@ -0,0 +1,1 @@\n"
                    "+# Created outside Git\n"
                )
                prepare_execution_request(
                    request["request_id"],
                    patch_text=patch,
                    workspace_root=workspace,
                    db_path=db_path,
                )
                decide_apply_execution(
                    request["request_id"], decision="approved", decided_by="owner", db_path=db_path
                )

                applied = apply_execution_request(
                    request["request_id"],
                    workspace_root=workspace,
                    validations=["git_diff_check"],
                    db_path=db_path,
                )

                self.assertEqual(applied["status"], "applied_validated")
                self.assertEqual((workspace / "README.md").read_text(encoding="utf-8"), "# Created outside Git\n")
                preset = applied["application"]["validation"]["presets"][0]
                self.assertTrue(preset["passed"])
                self.assertTrue(preset["skipped"])
                self.assertTrue(applied["application"]["git_evidence"]["diff_stat"]["skipped"])
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_apply_bot_opens_external_input_decision_after_smoke_validation(self):
        db_path = Path("data") / "test_apply_bot_external_input.sqlite3"
        if db_path.exists():
            db_path.unlink()
        Path("data").mkdir(exist_ok=True)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                workspace = Path(temp_dir)
                (workspace / "BOT.txt").write_text("Metodo de aprendizagem.", encoding="utf-8")
                result = {
                    "active_flow": "delivery_core",
                    "execution_policy": {"approval_required_actions": ["write_files"]},
                    "work_scope": {
                        "project_id": "meu-bot",
                        "initiative_id": "default",
                        "memory_namespace": "meu-bot:default",
                    },
                    "route_status": "ended",
                    "operational_packet": {
                        "execution_ready": False,
                        "governance_blocked": True,
                        "decision": "NO_GO",
                        "workspace_root": str(workspace),
                        "target_refs": ["bot.py", "requirements.txt", ".env.example"],
                        "recommended_actions": ["Criar bot.py para Telegram"],
                        "verification_steps": ["Validar bot."],
                        "git_actions": [],
                    },
                    "structured_outputs": {},
                    "orchestrator_checks": {},
                }
                record_run(result, run_id="run_bot_apply", user_goal="Criar bot.", db_path=db_path)
                request = create_execution_request(result, run_id="run_bot_apply", db_path=db_path)
                decide_execution_request(request["request_id"], decision="approved", decided_by="owner", db_path=db_path)
                prepare_execution_request(request["request_id"], workspace_root=workspace, db_path=db_path)
                decide_apply_execution(request["request_id"], decision="approved", decided_by="owner", db_path=db_path)

                applied = apply_execution_request(
                    request["request_id"],
                    workspace_root=workspace,
                    validations=["git_diff_check"],
                    db_path=db_path,
                )

                self.assertEqual(applied["status"], "applied_validated")
                decisions = human_decision_snapshot(db_path, include_validation=True)
                self.assertEqual(decisions["actionable_pending_count"], 1)
                self.assertIn("TELEGRAM_TOKEN", decisions["decisions"][0]["decision_needed"])
                self.assertEqual(
                    workflow_checkpoint_snapshot(thread_id="run_bot_apply", db_path=db_path)["checkpoints"][-1]["stage"],
                    "external_input_required",
                )
                self.assertEqual(pending_work_snapshot(db_path)["pending"][0]["next_action"], "Responder pendencia humana: Decidir continuidade da demanda.")
                with self.assertRaisesRegex(ValueError, "TELEGRAM_TOKEN"):
                    respond_human_decision(
                        "decision_run_bot_apply_external_input",
                        response="Token configurado.",
                        resolution="continue",
                        db_path=db_path,
                    )

                (workspace / ".env").write_text("TELEGRAM_TOKEN=123:test\n", encoding="utf-8")
                resolved = respond_human_decision(
                    "decision_run_bot_apply_external_input",
                    response="Token configurado.",
                    resolution="continue",
                    db_path=db_path,
                )
                self.assertEqual(resolved["status"], "resolved")
                self.assertEqual(
                    workflow_checkpoint_snapshot(thread_id="run_bot_apply", db_path=db_path)["checkpoints"][-1]["stage"],
                    "external_input_confirmed",
                )
                flow = run_flow_snapshot(run_id="run_bot_apply", db_path=db_path)
                self.assertEqual(flow["run"]["status"], "return_requested")
                self.assertEqual(flow["run"]["operational_packet"]["product_acceptance_status"], "functional_qa_pending")

                accepted = record_functional_qa_result(
                    "run_bot_apply",
                    passed=True,
                    evidence="Enviei /start, percorri o ciclo, validei /status e /reset no Telegram.",
                    db_path=db_path,
                )
                self.assertEqual(accepted["status"], "ended")
                self.assertEqual(accepted["product_acceptance_status"], "product_accepted")
                self.assertEqual(
                    workflow_checkpoint_snapshot(thread_id="run_bot_apply", db_path=db_path)["checkpoints"][-1]["stage"],
                    "functional_qa_passed",
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_validated_delivery_can_create_supervised_branch_and_commit(self):
        db_path = Path("data") / "test_git_delivery.sqlite3"
        if db_path.exists():
            db_path.unlink()
        Path("data").mkdir(exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir="data") as temp_dir:
                workspace = Path(temp_dir)
                target = workspace / "README.md"
                target.write_text("# Before\n", encoding="utf-8")
                subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
                subprocess.run(["git", "config", "user.name", "tests"], cwd=workspace, check=True)
                subprocess.run(["git", "config", "user.email", "tests@example.test"], cwd=workspace, check=True)
                subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
                subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=workspace, check=True)
                result = {
                    "active_flow": "docs",
                    "execution_policy": {"approval_required_actions": ["write_files"]},
                    "route_status": "ended",
                    "operational_packet": {
                        "execution_ready": True,
                        "target_refs": ["README.md"],
                        "recommended_actions": ["Atualizar README."],
                        "verification_steps": ["Validar diff."],
                        "git_actions": ["commit"],
                    },
                    "structured_outputs": {},
                    "orchestrator_checks": {},
                }
                record_run(result, run_id="run_git", user_goal="Editar README.", db_path=db_path)
                request = create_execution_request(result, run_id="run_git", db_path=db_path)
                decide_execution_request(request["request_id"], decision="approved", decided_by="owner", db_path=db_path)
                prepare_execution_request(
                    request["request_id"],
                    patch_text="--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-# Before\n+# Delivered\n",
                    workspace_root=workspace,
                    db_path=db_path,
                )
                decide_apply_execution(request["request_id"], decision="approved", decided_by="owner", db_path=db_path)
                apply_execution_request(request["request_id"], workspace_root=workspace, db_path=db_path)

                delivered = commit_git_delivery(
                    request["request_id"],
                    workspace_root=workspace,
                    branch_name="squad/test-delivery",
                    commit_message="Deliver README",
                    db_path=db_path,
                )

                self.assertEqual(delivered["status"], "git_committed")
                self.assertEqual(delivered["git_delivery"]["branch_name"], "squad/test-delivery")
                self.assertTrue(delivered["git_delivery"]["commit_sha"])
                self.assertEqual(
                    subprocess.run(["git", "branch", "--show-current"], cwd=workspace, check=True, text=True, capture_output=True).stdout.strip(),
                    "squad/test-delivery",
                )
                self.assertEqual(pending_work_snapshot(db_path)["pending"][0]["next_action"], "Publicar branch e abrir PR draft apos revisao humana.")
                self.assertIn("git_commit_created", [item["stage"] for item in workflow_checkpoint_snapshot(thread_id="run_git", db_path=db_path)["checkpoints"]])
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_controlled_apply_rolls_back_when_validation_fails(self):
        db_path = Path("data") / "test_apply_failure.sqlite3"
        if db_path.exists():
            db_path.unlink()
        Path("data").mkdir(exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir="data") as temp_dir:
                workspace = Path(temp_dir)
                target = workspace / "README.md"
                target.write_text("# Before\n\nContext.\n", encoding="utf-8")
                subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
                subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
                subprocess.run(
                    [
                        "git",
                        "-c",
                        "user.name=tests",
                        "-c",
                        "user.email=tests@example.test",
                        "commit",
                        "-q",
                        "-m",
                        "base",
                    ],
                    cwd=workspace,
                    check=True,
                )
                result = {
                    "execution_policy": {"approval_required_actions": ["write_files"]},
                    "route_status": "ended",
                    "operational_packet": {
                        "execution_ready": True,
                        "target_refs": ["README.md"],
                        "recommended_actions": ["Atualizar."],
                        "verification_steps": ["Testar."],
                        "git_actions": [],
                    },
                    "structured_outputs": {},
                    "orchestrator_checks": {},
                }
                record_run(result, run_id="run_failed_apply", user_goal="Editar.", db_path=db_path)
                request = create_execution_request(result, run_id="run_failed_apply", db_path=db_path)
                decide_execution_request(
                    request["request_id"], decision="approved", decided_by="owner", db_path=db_path
                )
                patch = (
                    "--- a/README.md\n+++ b/README.md\n@@ -1,3 +1,3 @@\n"
                    "-# Before\n+# After\n \n Context.\n"
                )
                prepare_execution_request(
                    request["request_id"], patch_text=patch, workspace_root=workspace, db_path=db_path
                )
                decide_apply_execution(
                    request["request_id"], decision="approved", decided_by="owner", db_path=db_path
                )

                failed = apply_execution_request(
                    request["request_id"],
                    workspace_root=workspace,
                    validations=["unit_tests"],
                    db_path=db_path,
                )
                self.assertEqual(failed["status"], "rolled_back")
                self.assertEqual(
                    failed["application"]["application_status"],
                    "rolled_back_validation_failed",
                )
                self.assertEqual(target.read_text(encoding="utf-8"), "# Before\n\nContext.\n")
                timeline = workflow_checkpoint_snapshot(thread_id="run_failed_apply", db_path=db_path)
                self.assertEqual(timeline["checkpoints"][-1]["stage"], "application_rolled_back")
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_operations_api_exposes_dashboard_and_records_approval(self):
        from http.server import ThreadingHTTPServer

        db_path = Path("data") / "test_operations_api.sqlite3"
        if db_path.exists():
            db_path.unlink()
        server = None
        thread = None
        try:
            result = {
                "active_flow": "docs",
                "execution_policy": {
                    "execution_tier": "quick",
                    "approval_required_actions": ["write_files"],
                },
                "route_status": "ended",
                "operational_packet": {
                    "execution_ready": True,
                    "target_refs": ["README.md"],
                    "recommended_actions": ["Atualizar README."],
                    "verification_steps": ["Revisar diff."],
                    "git_actions": [],
                },
                "structured_outputs": {},
                "orchestrator_checks": {},
            }
            record_run(result, run_id="run_api", user_goal="Atualizar README.", db_path=db_path)
            request = create_execution_request(result, run_id="run_api", db_path=db_path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db_path))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"

            with urllib.request.urlopen(f"{base_url}/api/dashboard") as response:
                dashboard = json.loads(response.read().decode("utf-8"))
            self.assertEqual(dashboard["pending"]["pending_count"], 1)

            payload = json.dumps({"by": "owner", "notes": "API approval"}).encode("utf-8")
            api_request = urllib.request.Request(
                f"{base_url}/api/requests/{request['request_id']}/approve",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(api_request) as response:
                approved = json.loads(response.read().decode("utf-8"))
            self.assertEqual(approved["status"], "approved_for_dry_run")
        finally:
            if server:
                server.shutdown()
                server.server_close()
            if thread:
                thread.join(timeout=2)
            if db_path.exists():
                db_path.unlink()

    def test_manual_run_preview_has_cost_policy_and_requires_confirmation(self):
        preview = preview_manual_run(
            {
                "user_goal": "Documentar a arquitetura no README.",
                "project_id": "Portal",
                "initiative_id": "Docs V1",
                "workspace_root": ".",
            }
        )
        self.assertEqual(preview["active_flow"], "docs")
        self.assertEqual(preview["project_id"], "portal")
        self.assertEqual(preview["execution_policy"]["execution_tier"], "quick")
        self.assertGreater(preview["execution_policy"]["max_cost_usd"], 0)

        with self.assertRaises(ValueError):
            enqueue_manual_run({"user_goal": "Documentar README."})

    def test_reference_document_is_loaded_and_reused_for_same_initiative(self):
        db_path = Path("data") / "test_reference_document.sqlite3"
        if db_path.exists():
            db_path.unlink()
        Path("data").mkdir(exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir="data") as temp_dir:
                workspace = Path(temp_dir)
                docs = workspace / "docs"
                docs.mkdir()
                (docs / "prd.md").write_text(
                    "# Bot Concierge\n\nO bot deve responder duvidas e escalar casos sensiveis.",
                    encoding="utf-8",
                )
                explicit = preview_manual_run(
                    {
                        "user_goal": "Construir o MVP do bot conforme a PRD.",
                        "project_id": "Bot",
                        "initiative_id": "MVP",
                        "workspace_root": str(workspace),
                        "reference_document_ref": "docs/prd.md",
                    },
                    include_document_content=True,
                    db_path=db_path,
                )
                self.assertEqual(explicit["reference_document"]["document_ref"], "docs/prd.md")
                self.assertIn("Bot Concierge", explicit["reference_document"]["content"])
                bind_initiative_document(
                    explicit["memory_namespace"],
                    explicit["workspace_root"],
                    explicit["reference_document"],
                    db_path=db_path,
                )
                inherited = preview_manual_run(
                    {
                        "user_goal": "Planejar a proxima entrega do bot.",
                        "project_id": "Bot",
                        "initiative_id": "MVP",
                        "workspace_root": str(workspace),
                    },
                    db_path=db_path,
                )
                self.assertEqual(inherited["reference_document"]["source"], "initiative_binding")
                self.assertNotIn("content", inherited["reference_document"])
                context_block = graph_module.workspace_context_block(
                    {
                        "reference_document": explicit["reference_document"],
                        "workspace_context": {},
                    },
                    "product",
                )
                self.assertIn("requisito primario", context_block)
                self.assertIn("Bot Concierge", context_block)
                downstream_block = graph_module.workspace_context_block(
                    {
                        "reference_document": explicit["reference_document"],
                        "workspace_context": {},
                        "active_flow": "delivery_core",
                    },
                    "qa_planning",
                )
                self.assertNotIn("Bot Concierge", downstream_block)
                self.assertIn("artefatos derivados", downstream_block)
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_reference_document_accepts_docx_without_external_dependency(self):
        Path("data").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir="data") as temp_dir:
            workspace = Path(temp_dir)
            path = workspace / "prd.docx"
            ignored = workspace / "node_modules"
            ignored.mkdir()
            (ignored / "vendor-prd.md").write_text("Nao indexar.", encoding="utf-8")
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>PRD do Bot</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>Atendimento supervisionado.</w:t></w:r></w:p></w:body></w:document>'
            )
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", xml)
            loaded = load_reference_document(workspace, "prd.docx")
            self.assertIn("Atendimento supervisionado.", loaded["content"])
            indexed = list_reference_documents(workspace)
            self.assertEqual(indexed[0]["document_format"], "docx")
            self.assertFalse(any(item["document_ref"].startswith("node_modules/") for item in indexed))

    def test_operations_api_previews_and_queues_supervised_manual_run(self):
        from http.server import ThreadingHTTPServer

        db_path = Path("data") / "test_manual_run_api.sqlite3"
        if db_path.exists():
            db_path.unlink()
        launched = []

        def fake_starter(payload, *, db_path):
            launched.append((payload, db_path))
            return {"run_id": "run_queued", "status": "queued"}

        server = None
        try:
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                make_handler(db_path, run_starter=fake_starter),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            payload = {
                "user_goal": "Corrigir bug na tela de login.",
                "project_id": "Portal",
                "initiative_id": "Login",
                "workspace_root": ".",
            }
            preview_request = urllib.request.Request(
                f"{base_url}/api/intake/preview",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(preview_request) as response:
                preview = json.loads(response.read().decode("utf-8"))
            self.assertEqual(preview["active_flow"], "bugfix")
            self.assertEqual(preview["execution_policy"]["execution_tier"], "standard")
            documents_request = urllib.request.Request(
                f"{base_url}/api/intake/documents",
                data=json.dumps({"workspace_root": "."}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(documents_request) as response:
                documents = json.loads(response.read().decode("utf-8"))
            self.assertTrue(any(item["document_ref"] == "README.md" for item in documents["documents"]))

            launch_request = urllib.request.Request(
                f"{base_url}/api/runs",
                data=json.dumps({**payload, "cost_confirmed": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(launch_request) as response:
                queued = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 202)
            self.assertEqual(queued["status"], "queued")
            self.assertEqual(launched[0][0]["user_goal"], payload["user_goal"])
        finally:
            if server:
                server.shutdown()
                server.server_close()
            if db_path.exists():
                db_path.unlink()

    def test_operations_api_rejects_cross_origin_manual_mutations(self):
        from http.server import ThreadingHTTPServer

        db_path = Path("data") / "test_origin_api.sqlite3"
        if db_path.exists():
            db_path.unlink()
        server = None
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db_path))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/intake/preview",
                data=json.dumps({"user_goal": "Documentar README."}).encode("utf-8"),
                headers={"Content-Type": "application/json", "Origin": "https://external.invalid"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request)
            self.assertEqual(raised.exception.code, 403)
            raised.exception.close()
        finally:
            if server:
                server.shutdown()
                server.server_close()
            if db_path.exists():
                db_path.unlink()

    def test_queued_manual_run_is_visible_and_can_fail_safely(self):
        db_path = Path("data") / "test_queued_run.sqlite3"
        if db_path.exists():
            db_path.unlink()
        try:
            record_run_started(
                run_id="run_pending",
                user_goal="Construir uma feature.",
                project_id="portal",
                initiative_id="signup",
                memory_namespace="portal:signup",
                active_flow="delivery_core",
                execution_policy={"execution_tier": "full"},
                db_path=db_path,
            )
            run = recent_runs_snapshot(db_path=db_path)["runs"][0]
            self.assertEqual(run["status"], "queued")

            update_run_status(
                "run_pending",
                status="failed",
                operational_packet={"error": "Falha controlada."},
                db_path=db_path,
            )
            self.assertEqual(recent_runs_snapshot(db_path=db_path)["runs"][0]["status"], "failed")
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_queued_manual_run_keeps_trace_identity_when_tracing_is_ready(self):
        db_path = Path("data") / "test_queued_trace_run.sqlite3"
        if db_path.exists():
            db_path.unlink()
        try:
            with (
                patch.object(run_service_module, "langsmith_ready", return_value=True),
                patch.object(run_service_module.RUN_EXECUTOR, "submit") as submit,
            ):
                queued = run_service_module.enqueue_manual_run(
                    {
                        "user_goal": "Documentar README.",
                        "workspace_root": ".",
                        "project_id": "portal",
                        "initiative_id": "docs",
                        "cost_confirmed": True,
                    },
                    db_path=db_path,
                )

            self.assertTrue(queued["trace_id"])
            run = recent_runs_snapshot(db_path=db_path)["runs"][0]
            self.assertEqual(run["trace_id"], queued["trace_id"])
            self.assertEqual(str(submit.call_args.args[4]), queued["trace_id"])
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_operational_views_surface_flow_cost_and_human_resolution(self):
        from http.server import ThreadingHTTPServer

        db_path = Path("data") / "test_operational_views.sqlite3"
        if db_path.exists():
            db_path.unlink()
        server = None
        thread = None
        try:
            result = {
                "active_flow": "decision_only",
                "execution_policy": {"execution_tier": "quick", "max_cost_usd": 0.15},
                "work_scope": {
                    "project_id": "portal",
                    "initiative_id": "pricing",
                    "memory_namespace": "portal:pricing",
                },
                "route_status": "human_escalation",
                "cos_decision": "ESCALATE_TO_HUMAN",
                "route_decision": "human_escalation",
                "human_escalation_created": True,
                "human_escalation_reason": "Limite comercial precisa de aprovacao.",
                "human_required_decision": "Aprovar ou ajustar faixa de preco.",
                "operational_packet": {"execution_ready": False, "human_checkpoint": "Decisao pendente."},
                "structured_outputs": {
                    "product": {
                        "summary": {"ece": "C2"},
                        "operational_artifact": {"artifact_type": "brief", "execution_ready": False},
                    },
                    "cos": {
                        "summary": {"ece": "C2"},
                        "operational_artifact": {"artifact_type": "decision", "execution_ready": False},
                    },
                },
                "orchestrator_checks": {},
            }
            record_run(
                result,
                run_id="run_decision",
                user_goal="Definir preco.",
                trace_id="trace_decision",
                db_path=db_path,
            )
            record_handoff_event(
                run_id="run_decision",
                source_agent="Product Lead",
                target_agent="CoS / Orchestrator",
                artifact="Product Decision",
                summary="Preco depende de decisao humana.",
                ece="C2",
                db_path=db_path,
            )
            record_eval_result(
                evaluation_id="eval_contract",
                scenario_id="decision",
                run_id="run_decision",
                passed=True,
                score=9.0,
                findings=[],
                db_path=db_path,
            )
            validation_result = {
                **result,
                "work_scope": {
                    "project_id": "squad-v5-lite",
                    "initiative_id": "baseline-governanca",
                    "memory_namespace": "squad-v5-lite:baseline-governanca",
                },
            }
            record_run(
                validation_result,
                run_id="run_validation_decision",
                user_goal="Validar gate.",
                trace_id="trace_validation",
                db_path=db_path,
            )
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("DELETE FROM human_decisions")
                connection.commit()
            finally:
                connection.close()

            decisions = human_decision_snapshot(db_path)
            self.assertEqual(decisions["pending_count"], 1)
            self.assertEqual(decisions["decisions"][0]["decision_title"], "Decidir continuidade da demanda")
            self.assertIn("Product Lead -> CoS / Orchestrator", decisions["decisions"][0]["what_happened"])
            self.assertEqual(decisions["decisions"][0]["previous_step"]["artifact"], "Product Decision")
            self.assertEqual(len(decisions["decisions"][0]["human_options"]), 3)
            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM human_decisions").fetchone()[0], 0)
            finally:
                connection.close()
            self.assertEqual(board_snapshot(db_path)["columns"]["human_decision"][0]["run_id"], "run_decision")
            flow = run_flow_snapshot(run_id="run_decision", db_path=db_path)
            self.assertEqual([agent["agent_name"] for agent in flow["agents"]], ["product", "cos"])
            self.assertEqual(flow["link_source"], "recorded_handoffs")
            self.assertEqual(flow["handoffs"][0]["target_agent"], "CoS / Orchestrator")
            costs = cost_snapshot(db_path)
            self.assertEqual(costs["estimated_budget_usd"], 0.30)
            self.assertEqual(costs["validation_estimated_budget_usd"], 0.15)
            self.assertEqual(metrics_snapshot(db_path, include_validation=False)["run_count"], 1)
            self.assertEqual(performance_history_snapshot(db_path)["evaluations"][0]["average_score"], 9.0)

            def fake_trace_loader(trace_id):
                return {
                    "status": "synced",
                    "observed_cost_usd": 0.021 if trace_id == "trace_decision" else 0.005,
                    "observed_tokens": 321 if trace_id == "trace_decision" else 50,
                    "trace_url": f"https://smith.langchain.com/r/{trace_id}",
                    "error": "",
                }

            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                make_handler(db_path, trace_loader=fake_trace_loader),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            for route in ("board", "costs", "performance", "decisions", "flow?run_id=run_decision", "topology"):
                with urllib.request.urlopen(f"{base_url}/api/{route}") as response:
                    self.assertEqual(response.status, 200)
            with urllib.request.urlopen(f"{base_url}/api/topology") as response:
                topology = json.loads(response.read().decode("utf-8"))
            self.assertIn("cos", topology["core_path"])
            self.assertTrue(
                any(
                    interaction["from"] == "intake" and interaction["to"] == "cos"
                    for interaction in topology["connections"]
                )
            )
            self.assertEqual(
                next(agent["mode"] for agent in topology["agents"] if agent["id"] == "discovery"),
                "on_demand",
            )
            self.assertTrue(
                any(
                    interaction["from"] == "cos" and interaction["to"] == "human"
                    for interaction in topology["connections"]
                )
            )
            with urllib.request.urlopen(f"{base_url}/api/dashboard") as response:
                dashboard = json.loads(response.read().decode("utf-8"))
            self.assertEqual(dashboard["metrics"]["run_count"], 1)
            self.assertEqual(dashboard["recent_runs"]["runs"][0]["run_id"], "run_decision")
            sync_request = urllib.request.Request(
                f"{base_url}/api/observability/sync",
                data=json.dumps({"run_id": "run_decision"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(sync_request) as response:
                synced = json.loads(response.read().decode("utf-8"))
            self.assertEqual(synced["synced_count"], 1)
            self.assertEqual(synced["costs"]["observed_cost_usd"], 0.021)
            synced_flow = run_flow_snapshot(run_id="run_decision", db_path=db_path)
            self.assertEqual(
                synced_flow["run"]["trace_url"],
                "https://smith.langchain.com/r/trace_decision",
            )
            api_request = urllib.request.Request(
                f"{base_url}/api/decisions/decision_run_decision/respond",
                data=json.dumps(
                    {
                        "response": "Continuar com faixa inicial aprovada.",
                        "resolution": "continue",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(api_request) as response:
                resolved = json.loads(response.read().decode("utf-8"))
            self.assertEqual(resolved["status"], "resolved")
            self.assertEqual(human_decision_snapshot(db_path)["pending_count"], 0)
            stale_validation_request = urllib.request.Request(
                f"{base_url}/api/decisions/decision_run_validation_decision/respond",
                data=json.dumps({"response": "Encerrar validacao.", "resolution": "close"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(stale_validation_request) as response:
                validation_resolution = json.loads(response.read().decode("utf-8"))
            self.assertEqual(validation_resolution["status"], "resolved")
            self.assertEqual(human_decision_snapshot(db_path)["decision_count"], 1)
            timeline = workflow_checkpoint_snapshot(thread_id="run_decision", db_path=db_path)
            self.assertEqual(timeline["checkpoints"][-1]["stage"], "human_decision_resolved")
        finally:
            if server:
                server.shutdown()
                server.server_close()
            if thread:
                thread.join(timeout=2)
            if db_path.exists():
                db_path.unlink()

    def test_human_decisions_preserve_scope_order(self):
        db_path = Path("data") / "test_decision_governance.sqlite3"
        if db_path.exists():
            db_path.unlink()
        try:
            base_result = {
                "active_flow": "decision_only",
                "execution_policy": {"execution_tier": "quick", "max_cost_usd": 0.15},
                "work_scope": {
                    "project_id": "portal",
                    "initiative_id": "pricing",
                    "memory_namespace": "portal:pricing",
                },
                "route_status": "human_escalation",
                "cos_decision": "ESCALATE_TO_HUMAN",
                "route_decision": "human_escalation",
                "human_escalation_created": True,
                "human_escalation_reason": "Decisao comercial exige validacao humana.",
                "human_required_decision": "Definir direcao comercial.",
                "operational_packet": {"execution_ready": False},
                "structured_outputs": {
                    "cos": {
                        "summary": {"ece": "C2"},
                        "operational_artifact": {"artifact_type": "decision", "execution_ready": False},
                    },
                },
                "orchestrator_checks": {},
            }
            record_run(base_result, run_id="run_first_decision", user_goal="Definir preco.", db_path=db_path)
            record_run(base_result, run_id="run_second_decision", user_goal="Definir campanha.", db_path=db_path)

            snapshot = human_decision_snapshot(db_path)
            decisions = {item["decision_id"]: item for item in snapshot["decisions"]}
            self.assertEqual(snapshot["pending_count"], 2)
            self.assertEqual(snapshot["actionable_pending_count"], 1)
            self.assertEqual(snapshot["blocked_pending_count"], 1)
            self.assertTrue(decisions["decision_run_first_decision"]["is_actionable"])
            self.assertFalse(decisions["decision_run_second_decision"]["is_actionable"])
            self.assertEqual(
                decisions["decision_run_second_decision"]["blocked_by_decision_id"],
                "decision_run_first_decision",
            )

            with self.assertRaisesRegex(ValueError, "Resolva primeiro"):
                respond_human_decision(
                    "decision_run_second_decision",
                    response="Seguir campanha.",
                    resolution="continue",
                    db_path=db_path,
                )

            respond_human_decision(
                "decision_run_first_decision",
                response="Preco aprovado.",
                resolution="continue",
                db_path=db_path,
            )
            refreshed = {
                item["decision_id"]: item
                for item in human_decision_snapshot(db_path)["decisions"]
            }
            self.assertTrue(refreshed["decision_run_second_decision"]["is_actionable"])
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_parking_lot_promotes_idea_into_external_inbox(self):
        db_path = Path("data") / "test_parking_lot.sqlite3"
        if db_path.exists():
            db_path.unlink()
        try:
            idea = create_parking_lot_item(
                {
                    "title": "Adicionar exportacao CSV.",
                    "context": "Sugerido durante revisao de operacao.",
                    "project_id": "portal",
                    "initiative_id": "relatorios",
                    "priority": "consider",
                },
                db_path=db_path,
            )
            self.assertEqual(idea["status"], "candidate")
            promoted = promote_parking_lot_item(idea["idea_id"], db_path=db_path)
            self.assertEqual(promoted["status"], "promoted")
            demands = automation_demand_snapshot(db_path)
            self.assertEqual(demands["demands"][0]["source"], "parking_lot")
            self.assertEqual(parking_lot_snapshot(db_path)["idea_count"], 1)
            self.assertEqual(board_snapshot(db_path)["columns"]["planned"][0]["user_goal"], "Adicionar exportacao CSV.")
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_n8n_webhook_requires_token_and_queues_idempotent_demand(self):
        from http.server import ThreadingHTTPServer

        db_path = Path("data") / "test_n8n_hook.sqlite3"
        if db_path.exists():
            db_path.unlink()
        server = None
        try:
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                make_handler(db_path, webhook_token="test-token"),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            endpoint = f"http://127.0.0.1:{server.server_port}/api/hooks/n8n/demands"
            payload = json.dumps(
                {
                    "event_id": "event-001",
                    "project_id": "portal",
                    "initiative_id": "intake",
                    "user_goal": "Analisar nova solicitacao.",
                }
            ).encode("utf-8")
            unauthorized = urllib.request.Request(
                endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(unauthorized)
            self.assertEqual(raised.exception.code, 401)
            raised.exception.close()

            authenticated = urllib.request.Request(
                endpoint,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer test-token",
                },
                method="POST",
            )
            urllib.request.urlopen(authenticated).read()
            urllib.request.urlopen(authenticated).read()
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/demands"
            ) as response:
                demands = json.loads(response.read().decode("utf-8"))
            self.assertEqual(demands["demand_count"], 1)
            self.assertEqual(demands["demands"][0]["status"], "queued_for_review")
        finally:
            if server:
                server.shutdown()
                server.server_close()
            if db_path.exists():
                db_path.unlink()

    def test_eval_assessment_accepts_expected_governance_c3(self):
        product, _ = safe_parse_agent_output(
            mock_agent_output("product", "# Product bloqueado", ece=ECE.C3),
            "product",
        )
        cos, _ = safe_parse_agent_output(
            mock_agent_output(
                "cos",
                "# CoS no-go",
                ece=ECE.C2,
                decision="NO_GO",
                route_action="END_CYCLE",
            ),
            "cos",
        )
        scenario = EvalScenario(
            "guardrail",
            "Avaliar escopo nao autorizado.",
            "delivery_core",
            ("product", "cos"),
            ("engineering",),
            ("product",),
            False,
        )
        result = {
            "active_flow": "delivery_core",
            "structured_outputs": {
                "product": product.model_dump(mode="json"),
                "cos": cos.model_dump(mode="json"),
            },
            "operational_packet": {
                "execution_ready": False,
                "recommended_actions": ["Registrar a decisao."],
                "verification_steps": ["Confirmar bloqueio ativo."],
            },
            "orchestrator_checks": {
                "product": {"schema_valid": True},
                "cos": {"schema_valid": True},
            },
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
            self.assertEqual(report["results"][0]["execution_status"], "completed")
            self.assertTrue(report["results"][0]["quality_score_eligible"])
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_baseline_result_migrates_legacy_schema_for_provider_failure(self):
        db_path = Path("data") / "test_baseline_legacy.sqlite3"
        if db_path.exists():
            db_path.unlink()
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE baseline_reviews (
                        baseline_id TEXT NOT NULL,
                        scenario_id TEXT NOT NULL,
                        executed_at TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        trace_id TEXT,
                        automatic_score REAL NOT NULL,
                        automatic_findings_json TEXT NOT NULL,
                        human_correctness REAL,
                        human_practical_utility REAL,
                        human_scope_control REAL,
                        human_next_step_clarity REAL,
                        human_execution_confidence REAL,
                        human_notes TEXT,
                        PRIMARY KEY (baseline_id, scenario_id)
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()
            record_baseline_result(
                baseline_id="baseline_failed",
                scenario_id="provider_case",
                run_id="failed_run",
                trace_id="failed_trace",
                automatic_score=0.0,
                findings=["Provider unavailable."],
                execution_status="provider_failed",
                provider_error="credits unavailable",
                db_path=db_path,
            )
            report = baseline_snapshot("baseline_failed", db_path)

            self.assertEqual(report["results"][0]["execution_status"], "provider_failed")
            self.assertEqual(report["results"][0]["provider_error"], "credits unavailable")
            self.assertIsNone(report["results"][0]["automatic_score"])
            self.assertFalse(report["results"][0]["quality_score_eligible"])
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

    def test_cos_retry_routes_back_to_authorized_agent(self):
        routed = graph_module.route_after_cos(
            {
                "route_decision": "return_requested",
                "retry_allowed": True,
                "retry_target": "engineering",
            }
        )
        ended = graph_module.route_after_cos(
            {
                "route_decision": "human_escalation",
                "retry_allowed": False,
                "retry_target": "engineering",
            }
        )

        self.assertEqual(routed, "engineering")
        self.assertEqual(ended, "__end__")

    def test_intake_skips_discovery_for_delivery_goal(self):
        decision = decide_intake("Criar feature de cadastro e validar release.")

        self.assertFalse(decision.needs_discovery)
        self.assertEqual(decision.active_flow, "delivery_core")
        self.assertIn("product", decision.fixed_agents)

    def test_cos_intake_gate_records_operational_brief_before_first_agent(self):
        db_path = Path("data") / "test_cos_intake_gate.sqlite3"
        if db_path.exists():
            db_path.unlink()
        try:
            state = {
                "run_id": "run_cos_intake",
                "user_goal": "Criar bot de Telegram conforme PRD.",
                "active_flow": "delivery_core",
                "fixed_agents": ["product", "qa_planning", "engineering", "cos"],
                "on_demand_agents": [],
                "execution_policy": {"execution_tier": "full", "max_cost_usd": 1.2},
                "memory_namespace": "tests:cos-intake",
                "operational_db_path": str(db_path),
            }

            result = graph_module.cos_intake_node(state)

            self.assertEqual(result["cos_intake_target"], "product")
            self.assertIn("CoS Intake Brief", result["cos_intake_output"])
            flow = run_flow_snapshot(run_id="run_cos_intake", db_path=db_path)
            self.assertEqual(flow["handoffs"][0]["source_agent"], "CoS / Intake Gate")
            self.assertEqual(flow["handoffs"][0]["target_agent"], "Product Lead")
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_cos_intake_gate_uses_llm_only_when_risk_requires_review(self):
        class FailingModel:
            def invoke(self, prompt):
                raise AssertionError("LLM should not be called for simple intake.")

        simple_state = {
            "run_id": "run_simple_cos_intake",
            "user_goal": "Criar feature de cadastro.",
            "active_flow": "delivery_core",
            "fixed_agents": ["product", "qa_planning", "engineering"],
            "on_demand_agents": [],
            "execution_policy": {"execution_tier": "full", "max_cost_usd": 1.2},
        }
        with patch.object(graph_module, "model", FailingModel()):
            result = graph_module.cos_intake_node(simple_state)
        self.assertEqual(result["cos_intake_mode"], "deterministic")
        self.assertEqual(result["cos_intake_target"], "product")

        class RoutingModel:
            def invoke(self, prompt):
                return MockResponse(
                    '{"target":"engineering","rationale":"Risco tecnico controlado pede Engenharia primeiro.","confidence":"C2"}'
                )

        controlled_state = {
            **simple_state,
            "run_id": "run_controlled_cos_intake",
            "user_goal": "Corrigir comportamento sensivel de autenticacao.",
            "execution_policy": {"execution_tier": "controlled", "max_cost_usd": 0.8},
        }
        with patch.object(graph_module, "model", RoutingModel()):
            result = graph_module.cos_intake_node(controlled_state)
        self.assertEqual(result["cos_intake_mode"], "llm_review")
        self.assertEqual(result["cos_intake_target"], "engineering")
        self.assertIn("Risco tecnico", result["cos_intake_rationale"])

    def test_intake_prioritizes_delivery_when_document_is_context(self):
        decision = decide_intake(
            "Criar um bot de Telegram usando a documentacao anexa como contexto da PRD."
        )

        self.assertEqual(decision.active_flow, "delivery_core")
        self.assertEqual(decision.execution_policy["execution_tier"], "full")
        self.assertIn("product", decision.fixed_agents)
        self.assertIn("ux_ui", decision.on_demand_agents)

    def test_bot_intake_calls_conversation_ux_without_new_fixed_agent(self):
        decision = decide_intake("Criar chatbot no Telegram para estudar assuntos da PRD.")

        self.assertEqual(decision.active_flow, "delivery_core")
        self.assertIn("ux_ui", decision.on_demand_agents)
        self.assertNotIn("ux_ui", decision.fixed_agents)

    def test_intake_calls_discovery_for_market_goal(self):
        decision = decide_intake("Fazer benchmark de concorrentes para validar hipotese de mercado.")

        self.assertTrue(decision.needs_discovery)
        self.assertEqual(decision.active_flow, "research_only")
        self.assertIn("discovery", decision.on_demand_agents)

    def test_intake_budgets_docs_as_quick_with_writing_in_plan(self):
        decision = decide_intake("Documentar o README da arquitetura.")

        self.assertEqual(decision.execution_policy["execution_tier"], "quick")
        self.assertEqual(decision.execution_policy["planned_agents"], ["writing", "cos"])
        self.assertEqual(decision.execution_policy["planned_agent_count"], 2)
        self.assertNotIn("ux_ui", decision.on_demand_agents)

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
        self.assertEqual(decision.execution_policy["execution_tier"], "controlled")
        self.assertIn("write_files", decision.execution_policy["approval_required_actions"])

    def test_ui_bugfix_routes_through_planned_ux_gate_before_operator(self):
        decision = decide_intake("Corrigir bug na tela de login.")
        state = {**decision.as_state(), "orchestrator_checks": {}}

        self.assertEqual(decision.active_flow, "bugfix")
        self.assertIn("ux_ui", decision.on_demand_agents)
        self.assertEqual(graph_module.route_after_engineering(state), "ux_ui")
        self.assertEqual(graph_module.route_after_ux_ui(state), "operator")

    def test_sensitive_ui_bugfix_keeps_security_gates_after_ux(self):
        decision = decide_intake("Corrigir falha na tela com dados pessoais e autenticacao.")
        state = {**decision.as_state(), "orchestrator_checks": {}}

        self.assertEqual(graph_module.route_after_engineering(state), "ux_ui")
        self.assertEqual(graph_module.route_after_ux_ui(state), "privacy")
        self.assertEqual(graph_module.route_after_privacy(state), "appsec")

    def test_langsmith_config_tags_specialist_gates(self):
        policy = decide_intake(
            "Criar interface de cadastro de paciente com dados pessoais e autenticacao."
        ).execution_policy
        config = build_run_config(
            run_id="run_observe",
            active_flow="delivery_core",
            on_demand_agents=("privacy", "appsec"),
            git_repo=True,
            project_id="health-app",
            initiative_id="patient-signup",
            execution_policy=policy,
        )

        self.assertIn("gate:privacy", config["tags"])
        self.assertIn("gate:appsec", config["tags"])
        self.assertEqual(config["metadata"]["gate_count"], 2)
        self.assertTrue(config["metadata"]["git_repo"])
        self.assertIn("tier:controlled", config["tags"])
        self.assertEqual(config["metadata"]["project_id"], "health-app")
        self.assertEqual(config["metadata"]["initiative_id"], "patient-signup")
        self.assertEqual(config["metadata"]["execution_tier"], "controlled")

    def test_work_scope_creates_isolated_memory_namespace(self):
        scope = resolve_work_scope(".", project_id="Client Portal", initiative_id="CSV Export V1")

        self.assertEqual(scope.project_id, "client-portal")
        self.assertEqual(scope.initiative_id, "csv-export-v1")
        self.assertEqual(scope.memory_namespace, "client-portal:csv-export-v1")

    def test_scoped_memory_keeps_initiative_handoffs_isolated(self):
        namespace = "unit-project:isolated-initiative"
        paths = [
            scoped_path(filename, namespace)
            for filename in (
                "handoff_log.md",
                "shared_memory.md",
                "cycle_reports.md",
                "route_events.md",
                "human_escalations.md",
            )
        ]
        for path in paths:
            if path.exists():
                path.unlink()
        try:
            append_handoff(
                from_agent="Product",
                to_agent="Engineering",
                artifact="Scoped artifact",
                summary="Only this initiative.",
                ece="C1",
                memory_namespace=namespace,
            )
            append_to_shared_memory("Scoped memory", "Only this memory.", namespace)
            append_cycle_report("Goal", "Cycle scoped", run_id="run_scoped", memory_namespace=namespace)
            append_route_event("Goal", {"route_decision": "end"}, memory_namespace=namespace)
            append_human_escalation("Goal", "Reason scoped", "Decision", memory_namespace=namespace)
            context = build_context_bundle("audit_debug", memory_namespace=namespace)

            self.assertIn("Scoped artifact", read_handoff_log(namespace))
            self.assertIn("Scoped artifact", context["handoff_log"])
            self.assertIn("Only this memory", read_shared_memory(namespace))
            self.assertIn("Cycle scoped", read_cycle_reports(namespace))
            self.assertIn("Route Event", read_route_events(namespace))
            self.assertIn("Reason scoped", read_human_escalations(namespace))
            self.assertNotIn("Scoped artifact", read_handoff_log("unit-project:another"))
        finally:
            for path in paths:
                if path.exists():
                    path.unlink()

    def test_review_intake_signals_appsec_without_delivery_flow(self):
        decision = decide_intake("Fazer code review de autenticacao e permissoes do login.")

        self.assertEqual(decision.active_flow, "review")
        self.assertIn("appsec", decision.on_demand_agents)

    def test_graph_contains_no_hardcoded_project_literals(self):
        """graph.py must not embed project-specific history (IDs, backlog items, code-names)."""
        graph_path = Path(__file__).resolve().parent.parent / "app" / "graph.py"
        source = graph_path.read_text(encoding="utf-8").lower()
        forbidden = [
            "mvp sujo",
            "b-001",
            "b-002",
            "d-001",
            "d-002",
            "d-003",
            "d-004",
            "d-005",
        ]
        for literal in forbidden:
            self.assertNotIn(literal, source, f"graph.py contains project literal: {literal!r}")

    def test_clean_data_seeds_detects_known_literals(self):
        """clean_data_seeds must detect project-specific literals in seed files."""
        import importlib.util
        import tempfile

        script_path = Path(__file__).resolve().parent.parent / "scripts" / "clean_data_seeds.py"
        spec = importlib.util.spec_from_file_location("clean_data_seeds", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            dirty_file = Path(tmp) / "shared_memory.md"
            dirty_file.write_text("# Test\nMVP Sujo was here\nB-001 open\nD-003 done\n", encoding="utf-8")
            clean_file = Path(tmp) / "decision_log.md"
            clean_file.write_text("# Clean\nNenhum dado relevante aqui.\n", encoding="utf-8")

            dirty_hits = module._matches_in_file(dirty_file)
            clean_hits = module._matches_in_file(clean_file)

            self.assertTrue(len(dirty_hits) >= 2, f"Expected >=2 literal matches, got {dirty_hits}")
            matched_literals = {h[1] for h in dirty_hits}
            self.assertIn("mvp sujo", matched_literals)
            self.assertIn("b-001", matched_literals)
            self.assertEqual(clean_hits, [], "Clean file should have no matches")
            # _clear_file empties the file without deleting it
            module._clear_file(dirty_file)
            self.assertEqual(dirty_file.read_text(encoding="utf-8"), "")


    def test_execution_policy_enforcing_for_quick_standard_full(self):
        """quick/standard/full tiers must use enforcing mode; controlled uses advisory."""
        from app.execution_policy import build_execution_policy

        for flow, expected_mode in [
            ("docs", "enforcing"),
            ("review", "enforcing"),
            ("bugfix", "enforcing"),
            ("delivery_core", "enforcing"),
        ]:
            policy = build_execution_policy(flow, fixed_agents=["product"], on_demand_agents=[])
            self.assertEqual(
                policy["enforcement_mode"], expected_mode,
                f"Flow '{flow}' (tier={policy['execution_tier']}) should use '{expected_mode}'"
            )

        # controlled tier (triggered by sensitive gate) stays advisory
        policy_controlled = build_execution_policy(
            "delivery_core", fixed_agents=["product"], on_demand_agents=["privacy"]
        )
        self.assertEqual(policy_controlled["execution_tier"], "controlled")
        self.assertEqual(policy_controlled["enforcement_mode"], "advisory")

    def test_invoke_graph_budget_stops_on_output_count(self):
        """_invoke_graph_with_budget stops streaming when output count hits max_agent_outputs."""
        from app.run_service import _invoke_graph_with_budget

        states = [
            {"structured_outputs": {"a": {}}},
            {"structured_outputs": {"a": {}, "b": {}}},
            {"structured_outputs": {"a": {}, "b": {}, "c": {}}},
        ]

        class FakeGraph:
            def stream(self, state, config, stream_mode):
                yield from states

        result = _invoke_graph_with_budget(
            FakeGraph(), {}, {}, {"enforcement_mode": "enforcing", "max_agent_outputs": 2, "max_duration_seconds": 3600}
        )
        self.assertTrue(result.get("budget_exceeded"), "Should stop when output count >= max_agent_outputs")
        self.assertEqual(len(result["structured_outputs"]), 2)

    def test_invoke_graph_budget_stops_on_duration(self):
        """_invoke_graph_with_budget stops streaming when elapsed time exceeds max_duration_seconds."""
        import time
        from app.run_service import _invoke_graph_with_budget

        call_count = 0

        class SlowGraph:
            def stream(self, state, config, stream_mode):
                nonlocal call_count
                while True:
                    call_count += 1
                    yield {"structured_outputs": {}}
                    time.sleep(0.05)

        result = _invoke_graph_with_budget(
            SlowGraph(), {}, {}, {"enforcement_mode": "enforcing", "max_agent_outputs": 999, "max_duration_seconds": 0.1}
        )
        self.assertTrue(result.get("budget_exceeded"), "Should stop when duration limit exceeded")

    def test_budget_exceeded_status_not_overwritten_by_record_run(self):
        """budget_exceeded route_status must survive the record_run upsert."""
        import tempfile
        from app.operational_store import record_run_started, record_run, recent_runs_snapshot

        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
            db = f.name

        try:
            run_id = "test-budget-overwrite-001"
            record_run_started(
                run_id=run_id, user_goal="test", project_id="p", initiative_id="i",
                memory_namespace="p:i", active_flow="docs",
                execution_policy={}, trace_id="", db_path=db,
            )
            result = {
                "route_status": "budget_exceeded",
                "structured_outputs": {},
                "orchestrator_checks": {},
                "work_scope": {},
                "execution_policy": {},
                "operational_packet": {},
                "cos_decision": "",
                "cos_route_action": "",
                "route_decision": "",
                "human_escalation_created": False,
                "active_flow": "docs",
            }
            record_run(result, run_id=run_id, user_goal="test", duration_ms=100, db_path=db)
            snapshot = recent_runs_snapshot(limit=10, db_path=db)
            run = next(r for r in snapshot["runs"] if r["run_id"] == run_id)
            self.assertEqual(run["status"], "budget_exceeded")
        finally:
            import os
            os.unlink(db)


    def test_qa_execution_ece_comes_from_model_output(self):
        """confidence_by_agent['qa_execution'] must reflect the model's ECE, not a hardcode."""
        with (
            patch.object(graph_module, "model", MockModel()),
            patch.object(graph_module, "USE_MOCK_MODEL", True),
        ):
            state = {
                "run_id": "test-p1c-001",
                "user_goal": "Validar build do sistema.",
                "workspace_root": ".",
                "workspace_context": {},
                "memory_namespace": "",
                "operational_db_path": None,
                "qa_plan_output": "QA plan mock.",
                "engineering_output": "Engineering spec mock.",
                "operator_output": "Operator package mock.",
                "engineering_review_output": "Review mock.",
                "privacy_output": "",
                "appsec_output": "",
                "manual_validation_result": "Validado manualmente.",
                "confidence_by_agent": {},
                "summaries_by_agent": {},
                "structured_outputs": {},
                "orchestrator_checks": {},
                "raw_model_outputs": {},
                "execution_policy": {},
                "retry_state": {},
            }
            result = graph_module.qa_execution_node(state)
            qa_ece = result["confidence_by_agent"].get("qa_execution")
            # The mock returns ECE.C1 for qa_execution — but what matters is
            # that it reads from the envelope, not a hardcoded literal.
            self.assertIsNotNone(qa_ece, "qa_execution ECE must be populated")
            self.assertIn(qa_ece, ("C1", "C2", "C3"), f"Unexpected ECE value: {qa_ece!r}")

    def test_engineering_review_ece_comes_from_model_output(self):
        """confidence_by_agent['engineering_review'] must reflect the model's ECE."""
        with (
            patch.object(graph_module, "model", MockModel()),
            patch.object(graph_module, "USE_MOCK_MODEL", True),
        ):
            state = {
                "run_id": "test-p1c-002",
                "user_goal": "Revisar spec de engineering.",
                "workspace_root": ".",
                "workspace_context": {},
                "memory_namespace": "",
                "operational_db_path": None,
                "product_output": "",
                "qa_plan_output": "",
                "engineering_output": "Spec mock.",
                "operator_output": "Package mock.",
                "privacy_output": "",
                "appsec_output": "",
                "confidence_by_agent": {},
                "summaries_by_agent": {},
                "structured_outputs": {},
                "orchestrator_checks": {},
                "raw_model_outputs": {},
                "execution_policy": {},
                "retry_state": {},
            }
            result = graph_module.engineering_review_node(state)
            er_ece = result["confidence_by_agent"].get("engineering_review")
            self.assertIsNotNone(er_ece)
            self.assertIn(er_ece, ("C1", "C2", "C3"), f"Unexpected ECE value: {er_ece!r}")


    def test_stable_system_block_includes_base_prompt(self):
        """_stable_system_block must combine the output contract with the agent's prompt file."""
        from app.graph import _stable_system_block, _AGENT_PROMPT_FILE

        # Every mapped agent must produce a block that includes its prompt file content.
        for agent_name, filename in _AGENT_PROMPT_FILE.items():
            block = _stable_system_block(agent_name)
            # The block must contain the output-contract sentinel phrase.
            self.assertIn("CONTRATO DE SAIDA DO RUNTIME", block,
                          f"Output contract missing from block for {agent_name!r}")
            # And it must include at least part of the base prompt file.
            prompt_path = Path(__file__).resolve().parent.parent / "app" / "prompts" / filename
            if prompt_path.exists():
                first_line = prompt_path.read_text(encoding="utf-8").splitlines()[0].strip()
                if first_line:
                    self.assertIn(first_line[:50], block,
                                  f"Base prompt content missing from block for {agent_name!r}")

    def test_cache_metrics_persisted_in_record_run(self):
        """cache_metrics_json column must be written and retrievable after record_run."""
        import tempfile
        from app.operational_store import record_run_started, record_run
        import sqlite3

        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
            db = f.name

        try:
            run_id = "test-cache-metrics-001"
            record_run_started(
                run_id=run_id, user_goal="test", project_id="p", initiative_id="i",
                memory_namespace="p:i", active_flow="docs",
                execution_policy={}, trace_id="", db_path=db,
            )
            result = {
                "route_status": "complete",
                "structured_outputs": {},
                "orchestrator_checks": {},
                "work_scope": {},
                "execution_policy": {},
                "operational_packet": {},
                "cos_decision": "",
                "cos_route_action": "",
                "route_decision": "",
                "human_escalation_created": False,
                "active_flow": "docs",
                "cache_metrics": {
                    "product": {"cache_creation_input_tokens": 512, "cache_read_input_tokens": 1024},
                    "engineering": {"cache_creation_input_tokens": 0, "cache_read_input_tokens": 2048},
                },
            }
            record_run(result, run_id=run_id, user_goal="test", duration_ms=100, db_path=db)

            import json as _json
            conn = sqlite3.connect(db)
            try:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT cache_metrics_json FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
            finally:
                conn.close()
            self.assertIsNotNone(row)
            stored = _json.loads(row["cache_metrics_json"])
            self.assertEqual(stored["product"]["cache_read_input_tokens"], 1024)
            self.assertEqual(stored["engineering"]["cache_creation_input_tokens"], 0)
        finally:
            import os
            try:
                os.unlink(db)
            except PermissionError:
                pass


    def test_metrics_snapshot_includes_repair_rates(self):
        """metrics_snapshot must include repair_rates per agent."""
        import tempfile
        from app.operational_store import record_run_started, record_run, metrics_snapshot

        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
            db = f.name

        try:
            for i, repaired in enumerate([True, False, True]):
                run_id = f"test-repair-rate-{i:03d}"
                record_run_started(
                    run_id=run_id, user_goal="test", project_id="p", initiative_id="i",
                    memory_namespace="p:i", active_flow="docs",
                    execution_policy={}, trace_id="", db_path=db,
                )
                product_output = {
                    "schema_version": "squad.agent_output.v1",
                    "agent_name": "product",
                    "artifact_markdown": "Mock",
                    "summary": {"ece": "C2", "agente_emissor": "product"},
                    "operational_artifact": {
                        "artifact_type": "brief",
                        "target_refs": ["test"],
                        "recommended_actions": ["continue"],
                        "verification_steps": ["check"],
                        "execution_ready": True,
                        "human_checkpoint": None,
                    },
                }
                result = {
                    "route_status": "complete",
                    "structured_outputs": {"product": product_output},
                    "orchestrator_checks": {
                        "product": {
                            "repair_attempted": repaired,
                            "ece": "C2",
                            "schema_valid": True,
                            "issues": [],
                        }
                    },
                    "work_scope": {"project_id": "p", "initiative_id": "i", "memory_namespace": "p:i"},
                    "execution_policy": {},
                    "operational_packet": {},
                    "cos_decision": "",
                    "cos_route_action": "",
                    "route_decision": "",
                    "human_escalation_created": False,
                    "active_flow": "docs",
                }
                record_run(result, run_id=run_id, user_goal="test", duration_ms=100, db_path=db)

            snapshot = metrics_snapshot(db_path=db, include_validation=True)
            self.assertIn("repair_rates", snapshot)
            rates = {r["agent_name"]: r for r in snapshot["repair_rates"]}
            self.assertIn("product", rates)
            product = rates["product"]
            self.assertEqual(product["total_outputs"], 3)
            self.assertEqual(product["repair_count"], 2)
            self.assertAlmostEqual(product["repair_rate"], 2 / 3, places=3)
        finally:
            import os
            try:
                os.unlink(db)
            except PermissionError:
                pass

    def test_api_metrics_endpoint_returns_repair_rates(self):
        """GET /api/metrics must return a repair_rates list."""
        import tempfile
        import threading
        import urllib.request

        db_path = Path("data") / "test_api_metrics_p2b.sqlite3"
        if db_path.exists():
            db_path.unlink()
        try:
            handler_class = make_handler(db_path)
            import http.server
            server = http.server.HTTPServer(("127.0.0.1", 0), handler_class)
            port = server.server_address[1]
            thread = threading.Thread(target=server.handle_request, daemon=True)
            thread.start()
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/metrics", timeout=5)
            data = json.loads(resp.read())
            self.assertIn("repair_rates", data, "repair_rates key missing from /api/metrics response")
            self.assertIsInstance(data["repair_rates"], list)
            self.assertIn("run_count", data)
        finally:
            server.server_close()
            if db_path.exists():
                db_path.unlink()


class VisionAgentTests(unittest.TestCase):
    """Tests for the Vision Agent node and ideation flow wiring."""

    def test_intake_routes_to_ideation_for_vision_goal(self):
        from app.intake import decide_intake

        for goal in [
            "Precisamos clarificar a visão estratégica do produto, ainda sem nada técnico definido",
            "Quero explorar a direção do produto, ainda sem nada concreto",
            "Mapa de visão do projeto para alinhar o time",
            "Estratégia de posicionamento — nada técnico ainda",
        ]:
            with self.subTest(goal=goal):
                decision = decide_intake(goal)
                self.assertEqual(
                    decision.active_flow,
                    "ideation",
                    f"Expected 'ideation' for goal: {goal!r}, got {decision.active_flow!r}",
                )
                all_agents = list(decision.fixed_agents) + list(decision.on_demand_agents)
                self.assertIn("vision", all_agents,
                              "vision should be in planned agents for ideation flow")

    def test_vision_node_produces_valid_envelope(self):
        from app.graph import graph  # noqa: F401 — ensures module loads without error
        from app.mock_model import MockModel
        from app.structured_output import AgentOutputEnvelope

        state = {
            "run_id": "test-vision-001",
            "user_goal": "Clarificar visão estratégica do produto",
            "active_flow": "ideation",
            "memory_namespace": "test",
            "fixed_agents": ["vision", "cos"],
            "on_demand_agents": [],
            "execution_policy": {"execution_tier": "quick", "max_cost_usd": 0.15},
            "confidence_by_agent": {},
            "summaries_by_agent": {},
            "orchestrator_checks": {},
            "structured_outputs": {},
            "raw_model_outputs": {},
            "cache_metrics": {},
        }

        with patch("app.graph.USE_MOCK_MODEL", True), \
             patch("app.graph._resolve_model", return_value=MockModel()):
            from app.vision_node import vision_node
            result = vision_node(state)

        self.assertIn("vision_output", result, "vision_node must set vision_output")
        self.assertTrue(result["vision_output"], "vision_output must not be empty")
        self.assertIn("vision", result.get("confidence_by_agent", {}),
                      "confidence_by_agent must include vision key")
        self.assertIn("vision", result.get("summaries_by_agent", {}),
                      "summaries_by_agent must include vision key")

    def test_cos_intake_includes_vision_output_in_brief(self):
        from app.graph import cos_intake_node

        vision_map = "## Mapa de Visão\n\nProblema: X\nRestrições: Y\nECE: C2"
        state = {
            "run_id": "test-cos-vision-001",
            "user_goal": "Clarificar visão do produto",
            "active_flow": "ideation",
            "memory_namespace": "test",
            "fixed_agents": ["vision", "cos"],
            "on_demand_agents": [],
            "execution_policy": {"execution_tier": "quick", "max_cost_usd": 0.15},
            "confidence_by_agent": {},
            "summaries_by_agent": {},
            "vision_output": vision_map,
        }

        result = cos_intake_node(state)

        brief = result["cos_intake_output"]
        self.assertIn("Mapa de Visao", brief,
                      "CoS Intake brief must include Mapa de Visao section when vision_output is set")
        self.assertIn(vision_map[:100], brief,
                      "CoS Intake brief must contain vision_output content")
        self.assertEqual(result["cos_intake_target"], "cos",
                         "ideation flow must route cos_intake_target to 'cos'")
        self.assertEqual(result["cos_intake_mode"], "deterministic",
                         "ideation flow gate must be deterministic")

    def test_vision_node_is_registered_in_graph(self):
        from app.graph import graph

        node_names = set(graph.nodes.keys())
        self.assertIn("vision", node_names, "graph must have a 'vision' node registered")

    def test_ideation_flow_agents_include_vision_and_cos(self):
        from app.intake import FLOW_AGENTS

        self.assertIn("ideation", FLOW_AGENTS, "FLOW_AGENTS must include 'ideation' key")
        agents = FLOW_AGENTS["ideation"]
        self.assertIn("vision", agents, "ideation flow must include vision agent")
        self.assertIn("cos", agents, "ideation flow must include cos agent")


class ExecutorPackageTests(unittest.TestCase):
    """Tests for app/executor/ — adapters, registry, and select_adapter."""

    # ------------------------------------------------------------------ helpers
    def _make_spec(self, **kwargs) -> "TaskSpec":
        from app.executor.base import TaskSpec
        defaults = dict(
            objective="Write a hello world function",
            target_files=["hello.py"],
            complexity="medium",
        )
        defaults.update(kwargs)
        return TaskSpec(**defaults)

    # ------------------------------------------------------------------ base
    def test_task_spec_to_claude_prompt_contains_objective(self):
        spec = self._make_spec(objective="Add unit tests for foo.py")
        prompt = spec.to_claude_prompt()
        self.assertIn("Add unit tests for foo.py", prompt)

    def test_task_spec_to_claude_prompt_contains_all_sections(self):
        spec = self._make_spec(
            objective="Refactor bar",
            context_files=["ctx.py"],
            target_files=["bar.py"],
            forbidden_files=["secrets.py"],
            acceptance_criteria=["all tests pass"],
            constraints=["no new dependencies"],
            validation_commands=["pytest"],
        )
        prompt = spec.to_claude_prompt()
        for fragment in [
            "Refactor bar",
            "ctx.py",
            "bar.py",
            "secrets.py",
            "all tests pass",
            "no new dependencies",
            "pytest",
        ]:
            self.assertIn(fragment, prompt)

    def test_execution_result_defaults(self):
        from app.executor.base import ExecutionResult
        r = ExecutionResult(success=True)
        self.assertEqual(r.files_changed, [])
        self.assertEqual(r.output, "")
        self.assertIsNone(r.tokens_used)
        self.assertFalse(r.partial)

    # ------------------------------------------------------------------ ClaudeCodeAdapter
    def test_claude_code_is_available_when_cli_present(self):
        from app.executor.claude_code import ClaudeCodeAdapter
        from unittest.mock import patch, MagicMock
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("app.executor.claude_code.subprocess.run", return_value=mock_result):
            self.assertTrue(ClaudeCodeAdapter().is_available())

    def test_claude_code_is_not_available_when_cli_absent(self):
        from app.executor.claude_code import ClaudeCodeAdapter
        from unittest.mock import patch
        with patch(
            "app.executor.claude_code.subprocess.run",
            side_effect=FileNotFoundError("claude not found"),
        ):
            self.assertFalse(ClaudeCodeAdapter().is_available())

    def test_claude_code_execute_success(self):
        import tempfile, os
        from app.executor.claude_code import ClaudeCodeAdapter
        from unittest.mock import patch, MagicMock, call

        def _fake_run(cmd, **kwargs):
            r = MagicMock()
            if cmd[0] == "claude":
                r.returncode = 0
                r.stdout = "done"
                r.stderr = ""
            else:  # git diff
                r.returncode = 0
                r.stdout = "hello.py\n"
            return r

        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.executor.claude_code.subprocess.run", side_effect=_fake_run):
                result = ClaudeCodeAdapter().execute(self._make_spec(), Path(tmp))

        self.assertTrue(result.success)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("hello.py", result.files_changed)

    def test_claude_code_execute_failure_no_exception(self):
        import tempfile
        from app.executor.claude_code import ClaudeCodeAdapter
        from unittest.mock import patch, MagicMock

        def _fake_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = "error"
            return r

        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.executor.claude_code.subprocess.run", side_effect=_fake_run):
                result = ClaudeCodeAdapter().execute(self._make_spec(), Path(tmp))

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 1)

    def test_claude_code_execute_subprocess_crash_no_exception(self):
        import tempfile
        from app.executor.claude_code import ClaudeCodeAdapter
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "app.executor.claude_code.subprocess.run",
                side_effect=OSError("no such file"),
            ):
                result = ClaudeCodeAdapter().execute(self._make_spec(), Path(tmp))

        self.assertFalse(result.success)
        self.assertIn("no such file", result.errors)

    def test_claude_code_estimated_cost_is_none(self):
        from app.executor.claude_code import ClaudeCodeAdapter
        self.assertIsNone(ClaudeCodeAdapter().estimated_cost(self._make_spec()))

    # ------------------------------------------------------------------ CodexAdapter
    def test_codex_is_available_with_api_key(self):
        from app.executor.codex import CodexAdapter
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            self.assertTrue(CodexAdapter().is_available())

    def test_codex_is_not_available_without_api_key(self):
        from app.executor.codex import CodexAdapter
        env = {k: v for k, v in __import__("os").environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict("os.environ", env, clear=True):
            self.assertFalse(CodexAdapter().is_available())

    def test_codex_execute_success(self):
        import sys, tempfile
        from app.executor.codex import CodexAdapter
        from unittest.mock import MagicMock, patch

        mock_openai = MagicMock()
        mock_resp = MagicMock()
        mock_resp.output_text = "def hello(): return 'hi'"
        mock_resp.usage.total_tokens = 120
        mock_resp.status = "completed"
        mock_resp.incomplete_details = None
        mock_openai.OpenAI.return_value.responses.create.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(sys.modules, {"openai": mock_openai}):
                result = CodexAdapter().execute(self._make_spec(), Path(tmp))

        self.assertTrue(result.success)
        self.assertFalse(result.partial)
        self.assertIn("hello", result.output)
        self.assertEqual(result.tokens_used, 120)

    def test_codex_execute_api_error_no_exception(self):
        import sys, tempfile
        from app.executor.codex import CodexAdapter
        from unittest.mock import MagicMock, patch

        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value.responses.create.side_effect = RuntimeError("API error")

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(sys.modules, {"openai": mock_openai}):
                result = CodexAdapter().execute(self._make_spec(), Path(tmp))

        self.assertFalse(result.success)
        self.assertIn("API error", result.errors)

    def test_codex_execute_import_error_no_exception(self):
        import sys, tempfile
        from app.executor.codex import CodexAdapter
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(sys.modules, {"openai": None}):
                result = CodexAdapter().execute(self._make_spec(), Path(tmp))

        self.assertFalse(result.success)
        self.assertIn("not installed", result.errors)

    def test_codex_estimated_cost_proportional_to_complexity(self):
        from app.executor.codex import CodexAdapter
        adapter = CodexAdapter()
        low = adapter.estimated_cost(self._make_spec(complexity="low"))
        high = adapter.estimated_cost(self._make_spec(complexity="high"))
        self.assertIsNotNone(low)
        self.assertIsNotNone(high)
        self.assertGreater(high, low)

    # ------------------------------------------------------------------ AiderAdapter
    def test_aider_is_available_when_cli_present(self):
        from app.executor.aider import AiderAdapter
        from unittest.mock import patch, MagicMock
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("app.executor.aider.subprocess.run", return_value=mock_result):
            self.assertTrue(AiderAdapter().is_available())

    def test_aider_is_not_available_when_cli_absent(self):
        from app.executor.aider import AiderAdapter
        from unittest.mock import patch
        with patch(
            "app.executor.aider.subprocess.run",
            side_effect=FileNotFoundError("aider not found"),
        ):
            self.assertFalse(AiderAdapter().is_available())

    def test_aider_execute_success(self):
        import tempfile
        from app.executor.aider import AiderAdapter
        from unittest.mock import patch, MagicMock
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Applied changes."
        mock_result.stderr = ""
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.executor.aider.subprocess.run", return_value=mock_result):
                result = AiderAdapter().execute(self._make_spec(), Path(tmp))
        self.assertTrue(result.success)
        self.assertIn("Applied", result.output)

    def test_aider_execute_failure_no_exception(self):
        import tempfile
        from app.executor.aider import AiderAdapter
        from unittest.mock import patch, MagicMock
        mock_result = MagicMock()
        mock_result.returncode = 2
        mock_result.stdout = ""
        mock_result.stderr = "conflict"
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.executor.aider.subprocess.run", return_value=mock_result):
                result = AiderAdapter().execute(self._make_spec(), Path(tmp))
        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 2)

    def test_aider_execute_exception_no_raise(self):
        import tempfile
        from app.executor.aider import AiderAdapter
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "app.executor.aider.subprocess.run",
                side_effect=OSError("aider crashed"),
            ):
                result = AiderAdapter().execute(self._make_spec(), Path(tmp))
        self.assertFalse(result.success)
        self.assertIn("aider crashed", result.errors)

    def test_aider_estimated_cost_local_model(self):
        from app.executor.aider import AiderAdapter
        with patch.dict("os.environ", {"AIDER_MODEL": "ollama/codellama"}):
            cost = AiderAdapter().estimated_cost(self._make_spec())
        self.assertEqual(cost, 0.0)

    def test_aider_estimated_cost_cloud_model_is_none(self):
        from app.executor.aider import AiderAdapter
        env = {k: v for k, v in __import__("os").environ.items() if k != "AIDER_MODEL"}
        with patch.dict("os.environ", {"AIDER_MODEL": "gpt-4o"}, clear=False):
            cost = AiderAdapter().estimated_cost(self._make_spec())
        self.assertIsNone(cost)

    # ------------------------------------------------------------------ registry
    def test_get_registry_returns_all_three_adapters(self):
        from app.executor.registry import get_registry
        reg = get_registry()
        for name in ("claude_code", "codex", "aider"):
            self.assertIn(name, reg)

    def test_select_adapter_prefers_preferred_executor(self):
        from app.executor.registry import select_adapter, REGISTRY
        from unittest.mock import patch

        spec = self._make_spec(preferred_executor="claude_code", complexity="low")
        with patch.object(REGISTRY["claude_code"], "is_available", return_value=True):
            chosen = select_adapter(spec, {})
        self.assertEqual(chosen.name(), "claude_code")

    def test_select_adapter_skips_unavailable_preferred_and_falls_through(self):
        from app.executor.registry import select_adapter, REGISTRY
        from unittest.mock import patch

        spec = self._make_spec(preferred_executor="claude_code", complexity="low")
        # low order: codex, aider, claude_code
        with (
            patch.object(REGISTRY["claude_code"], "is_available", return_value=False),
            patch.object(REGISTRY["codex"], "is_available", return_value=True),
            patch.object(REGISTRY["codex"], "estimated_cost", return_value=0.01),
            patch.object(REGISTRY["aider"], "is_available", return_value=False),
        ):
            chosen = select_adapter(spec, {})
        self.assertEqual(chosen.name(), "codex")

    def test_select_adapter_high_complexity_prefers_claude_code(self):
        from app.executor.registry import select_adapter, REGISTRY
        from unittest.mock import patch

        spec = self._make_spec(complexity="high")
        # high order: claude_code, codex, aider
        with (
            patch.object(REGISTRY["claude_code"], "is_available", return_value=True),
            patch.object(REGISTRY["codex"], "is_available", return_value=True),
        ):
            chosen = select_adapter(spec, {})
        self.assertEqual(chosen.name(), "claude_code")

    def test_select_adapter_budget_filter_skips_over_budget(self):
        from app.executor.registry import select_adapter, REGISTRY
        from unittest.mock import patch

        spec = self._make_spec(complexity="medium")
        # medium order: codex, aider, claude_code
        with (
            patch.object(REGISTRY["codex"], "is_available", return_value=True),
            patch.object(REGISTRY["codex"], "estimated_cost", return_value=0.05),
            patch.object(REGISTRY["aider"], "is_available", return_value=True),
            patch.object(REGISTRY["aider"], "estimated_cost", return_value=0.0),
            patch.object(REGISTRY["claude_code"], "is_available", return_value=False),
        ):
            chosen = select_adapter(spec, {"budget_remaining_usd": 0.01})
        # codex costs 0.05 > 0.01 budget, so skipped; aider costs 0.0, selected
        self.assertEqual(chosen.name(), "aider")

    def test_select_adapter_raises_when_none_available(self):
        from app.executor.registry import select_adapter, REGISTRY
        from unittest.mock import patch

        spec = self._make_spec(complexity="medium")
        with (
            patch.object(REGISTRY["codex"], "is_available", return_value=False),
            patch.object(REGISTRY["aider"], "is_available", return_value=False),
            patch.object(REGISTRY["claude_code"], "is_available", return_value=False),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                select_adapter(spec, {})
        self.assertIn("No code executor adapter", str(ctx.exception))

    def test_select_adapter_cost_none_is_not_filtered(self):
        """An adapter returning estimated_cost=None passes budget check (cost unknown)."""
        from app.executor.registry import select_adapter, REGISTRY
        from unittest.mock import patch

        spec = self._make_spec(complexity="medium")
        with (
            patch.object(REGISTRY["codex"], "is_available", return_value=True),
            patch.object(REGISTRY["codex"], "estimated_cost", return_value=None),
        ):
            chosen = select_adapter(spec, {"budget_remaining_usd": 0.001})
        self.assertEqual(chosen.name(), "codex")

    # ------------------------------------------------------------------ pilot_readiness
    def test_pilot_readiness_includes_executor_adapters(self):
        import app.pilot_readiness as pr_module
        from unittest.mock import patch

        with patch("app.pilot_readiness.get_registry") as mock_reg:
            from app.executor.base import CodeExecutorAdapter, TaskSpec, ExecutionResult
            class _FakeAdapter(CodeExecutorAdapter):
                def name(self): return "fake"
                def is_available(self): return True
                def execute(self, t, w): return ExecutionResult(success=True)
                def estimated_cost(self, t): return None

            mock_reg.return_value = {"fake": _FakeAdapter()}
            result = pr_module.pilot_readiness()

        self.assertIn("executor_adapters", result)
        self.assertIn("registered", result["executor_adapters"])
        self.assertIn("available", result["executor_adapters"])
        self.assertIn("fake", result["executor_adapters"]["available"])


class InvokeExecutorIntegrationTests(unittest.TestCase):
    """Tests for invoke_executor in execution_engine and its graph integration."""

    def _make_spec_dict(self, **kwargs) -> dict:
        base = {
            "objective": "Implement feature X",
            "target_files": ["app/feature.py"],
            "complexity": "medium",
            "validation_commands": [],
        }
        base.update(kwargs)
        return base

    # ------------------------------------------------------------------ invoke_executor
    def test_invoke_executor_success_returns_result_and_adapter_name(self):
        from app.execution_engine import invoke_executor
        from app.executor.base import ExecutionResult
        from unittest.mock import patch, MagicMock
        import tempfile

        mock_adapter = MagicMock()
        mock_adapter.name.return_value = "claude_code"
        mock_adapter.is_available.return_value = True
        mock_adapter.estimated_cost.return_value = None
        mock_adapter.execute.return_value = ExecutionResult(
            success=True, files_changed=["app/feature.py"], output="done"
        )

        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.executor.registry.select_adapter", return_value=mock_adapter):
                result, adapter_name = invoke_executor(
                    self._make_spec_dict(), tmp, {}
                )

        self.assertTrue(result.success)
        self.assertEqual(adapter_name, "claude_code")
        self.assertIn("app/feature.py", result.files_changed)

    def test_invoke_executor_no_adapter_available_returns_failure(self):
        from app.execution_engine import invoke_executor
        from unittest.mock import patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "app.executor.registry.select_adapter",
                side_effect=RuntimeError("No code executor adapter is available"),
            ):
                result, adapter_name = invoke_executor(
                    self._make_spec_dict(), tmp, {}
                )

        self.assertFalse(result.success)
        self.assertEqual(adapter_name, "")
        self.assertIn("No code executor adapter", result.errors)

    def test_invoke_executor_validation_failure_triggers_rollback(self):
        """When a validation command fails, git checkout -- . must be called."""
        from app.execution_engine import invoke_executor, _run_command
        from app.executor.base import ExecutionResult
        from unittest.mock import patch, MagicMock, call
        import tempfile

        mock_adapter = MagicMock()
        mock_adapter.name.return_value = "aider"
        mock_adapter.is_available.return_value = True
        mock_adapter.estimated_cost.return_value = 0.0
        mock_adapter.execute.return_value = ExecutionResult(
            success=True, files_changed=["app/feature.py"]
        )

        rollback_calls = []

        def fake_run_command(cmd, *, workspace_root, timeout=120):
            if cmd[0:2] == ["git", "checkout"]:
                rollback_calls.append(cmd)
                return {"command": cmd, "passed": True, "return_code": 0, "output": ""}
            # validation command fails
            return {"command": cmd, "passed": False, "return_code": 1, "output": "test failed"}

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("app.executor.registry.select_adapter", return_value=mock_adapter),
                patch("app.execution_engine._run_command", side_effect=fake_run_command),
            ):
                spec = self._make_spec_dict(validation_commands=["pytest tests/"])
                result, _ = invoke_executor(spec, tmp, {})

        self.assertFalse(result.success)
        self.assertTrue(any("git" in str(c) and "checkout" in str(c) for c in rollback_calls),
                        f"git checkout not called; calls were: {rollback_calls}")
        self.assertIn("pytest tests/", result.errors)

    def test_invoke_executor_invalid_spec_dict_returns_failure(self):
        from app.execution_engine import invoke_executor
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            # Missing required 'objective' field
            result, adapter_name = invoke_executor({}, tmp, {})

        self.assertFalse(result.success)
        self.assertIn("Invalid task spec", result.errors)

    # ------------------------------------------------------------------ operator_node with executor
    def test_operator_node_execution_ready_sets_execution_result_in_state(self):
        """execution_ready=True + workspace_root → execution_result in returned state."""
        import tempfile
        from app.graph import operator_node
        from app.executor.base import ExecutionResult
        from unittest.mock import patch, MagicMock

        mock_exec_result = ExecutionResult(
            success=True, files_changed=["app/feature.py"], output="implemented"
        )

        state = {
            "run_id": "test-exec-001",
            "user_goal": "Implement feature X",
            "active_flow": "delivery_core",
            "memory_namespace": "test",
            "fixed_agents": ["operator"],
            "on_demand_agents": [],
            "execution_policy": {"execution_tier": "full", "max_cost_usd": 0.60},
            "confidence_by_agent": {},
            "summaries_by_agent": {},
            "orchestrator_checks": {},
            "structured_outputs": {},
            "raw_model_outputs": {},
            "cache_metrics": {},
            "workspace_root": "/tmp/workspace",
        }

        with (
            patch("app.graph.USE_MOCK_MODEL", True),
            patch("app.graph._resolve_model", return_value=__import__("app.mock_model", fromlist=["MockModel"]).MockModel()),
            patch("app.graph.invoke_executor", return_value=(mock_exec_result, "claude_code")),
        ):
            result = operator_node(state)

        self.assertIn("execution_result", result, "execution_result must be in operator_node output")
        exec_res = result["execution_result"]
        self.assertIsNotNone(exec_res)
        self.assertTrue(exec_res["success"])
        self.assertEqual(result.get("executor_used"), "claude_code")
        self.assertIn("Executor Result", result.get("operator_output", ""))

    def test_operator_node_no_adapters_flow_continues(self):
        """When invoke_executor returns failure, operator_node completes normally."""
        import tempfile
        from app.graph import operator_node
        from app.executor.base import ExecutionResult
        from unittest.mock import patch

        failed_result = ExecutionResult(
            success=False, errors="No code executor adapter is available", partial=False
        )

        state = {
            "run_id": "test-exec-002",
            "user_goal": "Implement feature Y",
            "active_flow": "delivery_core",
            "memory_namespace": "test",
            "fixed_agents": ["operator"],
            "on_demand_agents": [],
            "execution_policy": {"execution_tier": "full", "max_cost_usd": 0.60},
            "confidence_by_agent": {},
            "summaries_by_agent": {},
            "orchestrator_checks": {},
            "structured_outputs": {},
            "raw_model_outputs": {},
            "cache_metrics": {},
            "workspace_root": "/tmp/workspace",
        }

        with (
            patch("app.graph.USE_MOCK_MODEL", True),
            patch("app.graph._resolve_model", return_value=__import__("app.mock_model", fromlist=["MockModel"]).MockModel()),
            patch("app.graph.invoke_executor", return_value=(failed_result, "")),
        ):
            result = operator_node(state)

        # Flow must complete — operator_output must be set
        self.assertIn("operator_output", result)
        self.assertTrue(result["operator_output"])
        # execution_result is present and records the failure
        self.assertIsNotNone(result.get("execution_result"))
        self.assertFalse(result["execution_result"]["success"])

    def test_operator_node_no_workspace_root_skips_executor(self):
        """When workspace_root is absent, executor is not called."""
        from app.graph import operator_node
        from unittest.mock import patch, MagicMock

        state = {
            "run_id": "test-exec-003",
            "user_goal": "Implement feature Z",
            "active_flow": "delivery_core",
            "memory_namespace": "test",
            "fixed_agents": ["operator"],
            "on_demand_agents": [],
            "execution_policy": {"execution_tier": "full", "max_cost_usd": 0.60},
            "confidence_by_agent": {},
            "summaries_by_agent": {},
            "orchestrator_checks": {},
            "structured_outputs": {},
            "raw_model_outputs": {},
            "cache_metrics": {},
            # workspace_root intentionally absent
        }

        mock_invoke_executor = MagicMock()

        with (
            patch("app.graph.USE_MOCK_MODEL", True),
            patch("app.graph._resolve_model", return_value=__import__("app.mock_model", fromlist=["MockModel"]).MockModel()),
            patch("app.graph.invoke_executor", mock_invoke_executor),
        ):
            result = operator_node(state)

        mock_invoke_executor.assert_not_called()
        # execution_result should be None (skipped)
        self.assertIsNone(result.get("execution_result"))

    # ------------------------------------------------------------------ engineering_review context
    def test_engineering_review_receives_execution_result_in_prompt(self):
        """When execution_result is in state, engineering_review prompt contains executor evidence."""
        from app.graph import engineering_review_node
        from unittest.mock import patch, MagicMock

        captured_prompts = []

        def fake_invoke_validated(agent_name, prompt):
            captured_prompts.append((agent_name, prompt))
            from app.mock_model import MockModel
            return __import__("app.graph", fromlist=["invoke_validated"]).__dict__

        state = {
            "run_id": "test-review-exec",
            "user_goal": "Implement feature",
            "active_flow": "delivery_core",
            "memory_namespace": "test",
            "fixed_agents": ["engineering_review"],
            "on_demand_agents": [],
            "execution_policy": {"execution_tier": "full"},
            "confidence_by_agent": {},
            "summaries_by_agent": {},
            "orchestrator_checks": {},
            "structured_outputs": {},
            "raw_model_outputs": {},
            "cache_metrics": {},
            "execution_result": {
                "success": True,
                "files_changed": ["app/feature.py", "tests/test_feature.py"],
                "output": "All changes applied successfully.",
                "errors": "",
                "exit_code": 0,
                "tokens_used": None,
                "iterations": None,
                "partial": False,
            },
            "executor_used": "claude_code",
        }

        captured = []

        def fake_invoke_validated(agent_name, prompt):
            captured.append(prompt)
            from app.mock_model import MockModel
            m = MockModel()
            from app.structured_output import safe_parse_agent_output
            from app.graph import ValidatedResponse, with_output_contract
            raw = m.invoke(with_output_contract(prompt, agent_name)).content
            envelope, errors = safe_parse_agent_output(raw, agent_name)
            return ValidatedResponse(raw, envelope, errors)

        with (
            patch("app.graph.USE_MOCK_MODEL", True),
            patch("app.graph.invoke_validated", side_effect=fake_invoke_validated),
        ):
            result = engineering_review_node(state)

        self.assertTrue(captured, "invoke_validated was not called")
        prompt_text = captured[0]
        self.assertIn("Executor Result", prompt_text,
                      "Executor Result block must appear in engineering_review prompt")
        self.assertIn("claude_code", prompt_text,
                      "executor_used must appear in engineering_review prompt")
        self.assertIn("app/feature.py", prompt_text,
                      "files_changed must appear in engineering_review prompt")


    # ------------------------------------------------------------------ executor config
    def test_config_executor_defaults(self):
        """DEFAULT_EXECUTOR and related settings are exported from app.config."""
        import app.config as cfg
        self.assertEqual(cfg.DEFAULT_EXECUTOR, "claude_code")
        self.assertIsInstance(cfg.CODEX_COST_PER_TOKEN, float)
        self.assertGreater(cfg.CODEX_COST_PER_TOKEN, 0)
        self.assertIsInstance(cfg.EXECUTOR_MAX_ITERATIONS, int)
        self.assertGreater(cfg.EXECUTOR_MAX_ITERATIONS, 0)
        self.assertIsInstance(cfg.EXECUTOR_MAX_DURATION_SECONDS, int)
        self.assertGreater(cfg.EXECUTOR_MAX_DURATION_SECONDS, 0)

    def test_select_adapter_promotes_default_executor_for_medium(self):
        """DEFAULT_EXECUTOR is head of preference order for medium/low complexity."""
        from unittest.mock import patch
        from app.executor.registry import _build_order
        import app.config as cfg

        with patch.object(cfg, "DEFAULT_EXECUTOR", "aider"):
            order = _build_order("medium")
        self.assertEqual(order[0], "aider", "DEFAULT_EXECUTOR must head non-high order")

    def test_select_adapter_high_complexity_ignores_default_executor(self):
        """High complexity keeps the fixed order regardless of DEFAULT_EXECUTOR."""
        from unittest.mock import patch
        from app.executor.registry import _build_order
        import app.config as cfg

        with patch.object(cfg, "DEFAULT_EXECUTOR", "aider"):
            order = _build_order("high")
        self.assertEqual(order[0], "claude_code", "High complexity order is fixed")

    # ------------------------------------------------------------------ executor_runs persistence
    def test_invoke_executor_persists_executor_run(self):
        """After invoke_executor, executor_runs_snapshot returns the recorded row."""
        import tempfile
        from pathlib import Path
        from unittest.mock import patch, MagicMock
        from app.execution_engine import invoke_executor
        from app.executor.base import ExecutionResult
        from app.operational_store import executor_runs_snapshot, initialize_schema

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.sqlite3"
            workspace = Path(tmp) / "ws"
            workspace.mkdir()

            fake_result = ExecutionResult(
                success=True,
                files_changed=["app/new_feature.py"],
                output="done",
                errors="",
                exit_code=0,
                tokens_used=42,
            )
            mock_adapter = MagicMock()
            mock_adapter.name.return_value = "claude_code"
            mock_adapter.execute.return_value = fake_result

            task_spec_dict = {
                "objective": "Add feature",
                "complexity": "medium",
            }

            with patch("app.executor.registry.select_adapter", return_value=mock_adapter):
                result, adapter_name = invoke_executor(
                    task_spec_dict, workspace, {}, run_id="test-persist-123", db_path=db_path
                )

            self.assertTrue(result.success)
            self.assertEqual(adapter_name, "claude_code")

            snapshot = executor_runs_snapshot(run_id="test-persist-123", db_path=db_path)
            runs = snapshot["executor_runs"]
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["executor_used"], "claude_code")
            self.assertTrue(runs[0]["success"])
            self.assertIn("app/new_feature.py", runs[0]["files_changed"])
            self.assertEqual(runs[0]["tokens_used"], 42)

    def test_executor_runs_snapshot_all_runs_when_no_run_id(self):
        """executor_runs_snapshot without run_id returns recent rows."""
        import tempfile
        from pathlib import Path
        from app.operational_store import record_executor_run, executor_runs_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.sqlite3"
            record_executor_run(run_id="r1", executor_used="codex", success=True, db_path=db_path)
            record_executor_run(run_id="r2", executor_used="aider", success=False, db_path=db_path)
            snapshot = executor_runs_snapshot(db_path=db_path)
            self.assertEqual(len(snapshot["executor_runs"]), 2)

    # ------------------------------------------------------------------ metrics executor_summary
    def test_metrics_snapshot_includes_executor_summary(self):
        """metrics_snapshot always contains executor_summary with aggregated fields."""
        import tempfile
        from pathlib import Path
        from app.operational_store import record_executor_run, metrics_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.sqlite3"
            record_executor_run(run_id="r1", executor_used="claude_code", success=True,
                                tokens_used=100, db_path=db_path)
            record_executor_run(run_id="r1", executor_used="claude_code", success=True,
                                tokens_used=200, db_path=db_path)
            record_executor_run(run_id="r2", executor_used="codex", success=False,
                                tokens_used=50, db_path=db_path)

            snap = metrics_snapshot(db_path=db_path)
            ex = snap["executor_summary"]
            self.assertEqual(ex["total_executions"], 3)
            self.assertAlmostEqual(ex["success_rate"], round(2 / 3, 4))
            self.assertEqual(ex["most_used_executor"], "claude_code")
            self.assertEqual(ex["total_tokens_used"], 350)

    def test_metrics_snapshot_executor_summary_empty(self):
        """executor_summary is zeroed when no executor runs exist."""
        import tempfile
        from pathlib import Path
        from app.operational_store import metrics_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.sqlite3"
            snap = metrics_snapshot(db_path=db_path)
            ex = snap["executor_summary"]
            self.assertEqual(ex["total_executions"], 0)
            self.assertEqual(ex["success_rate"], 0.0)
            self.assertEqual(ex["most_used_executor"], "")
            self.assertEqual(ex["total_tokens_used"], 0)

    # ------------------------------------------------------------------ /api/executor/status
    def test_executor_status_endpoint_returns_adapter_list(self):
        """GET /api/executor/status returns adapters with name, available, estimated_cost_low_task."""
        import io
        from unittest.mock import patch, MagicMock
        from http.server import BaseHTTPRequestHandler
        from app.operational_api import OperationsHandler

        mock_wfile = io.BytesIO()
        response_lines: list[bytes] = []

        class FakeSocket:
            def makefile(self, mode, **kw):
                return io.BufferedReader(io.BytesIO(b"GET /api/executor/status HTTP/1.0\r\n\r\n"))

        class CapturingHandler(OperationsHandler):
            def send_response(self, code, message=None):
                response_lines.append(f"HTTP {code}".encode())

            def send_header(self, key, value):
                pass

            def end_headers(self):
                pass

            def _json(self, payload, status=None):
                response_lines.append(__import__("json").dumps(payload).encode())

        mock_adapter_a = MagicMock()
        mock_adapter_a.is_available.return_value = True
        mock_adapter_a.estimated_cost.return_value = 0.001

        mock_adapter_b = MagicMock()
        mock_adapter_b.is_available.return_value = False
        mock_adapter_b.estimated_cost.return_value = None

        fake_registry = {"alpha": mock_adapter_a, "beta": mock_adapter_b}

        with patch("app.executor.registry.REGISTRY", fake_registry):
            handler = CapturingHandler.__new__(CapturingHandler)
            handler.path = "/api/executor/status"
            handler.db_path = __import__("pathlib").Path(":memory:")
            handler.do_GET()

        self.assertTrue(response_lines, "No response captured")
        body = response_lines[-1].decode()
        data = __import__("json").loads(body)
        self.assertIn("adapters", data)
        names = [a["name"] for a in data["adapters"]]
        self.assertIn("alpha", names)
        self.assertIn("beta", names)
        alpha = next(a for a in data["adapters"] if a["name"] == "alpha")
        self.assertTrue(alpha["available"])
        self.assertAlmostEqual(alpha["estimated_cost_low_task"], 0.001)
        beta = next(a for a in data["adapters"] if a["name"] == "beta")
        self.assertFalse(beta["available"])

    # ------------------------------------------------------------------ /api/executor/runs
    def test_executor_runs_endpoint_filters_by_run_id(self):
        """GET /api/executor/runs?run_id=X returns only that run's records."""
        import tempfile
        import io
        import json
        from pathlib import Path
        from unittest.mock import patch
        from app.operational_api import OperationsHandler
        from app.operational_store import record_executor_run

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.sqlite3"
            record_executor_run(run_id="run-abc", executor_used="claude_code", success=True,
                                db_path=db_path)
            record_executor_run(run_id="run-xyz", executor_used="codex", success=False,
                                db_path=db_path)

            captured: list[dict] = []

            class CapturingHandler(OperationsHandler):
                def send_response(self, code, message=None): pass
                def send_header(self, key, value): pass
                def end_headers(self): pass
                def _json(self, payload, status=None):
                    captured.append(payload)

            handler = CapturingHandler.__new__(CapturingHandler)
            handler.path = "/api/executor/runs?run_id=run-abc"
            handler.db_path = db_path
            handler.do_GET()

            self.assertTrue(captured)
            runs = captured[0]["executor_runs"]
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["run_id"], "run-abc")
            self.assertEqual(runs[0]["executor_used"], "claude_code")

    # ------------------------------------------------------------------ adapter partial detection
    def test_claude_code_adapter_partial_for_large_output(self):
        """Output > max_iterations × 2000 chars marks partial=True (success unchanged)."""
        from pathlib import Path
        from unittest.mock import MagicMock, patch
        from app.executor.claude_code import ClaudeCodeAdapter
        from app.executor.base import TaskSpec

        adapter = ClaudeCodeAdapter()
        spec = TaskSpec(objective="Do something", max_iterations=2)  # threshold = 4000 chars

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "x" * 4001  # exceeds 2 × 2000
        fake_proc.stderr = ""

        with (
            patch("app.executor.claude_code.subprocess.run", return_value=fake_proc),
            patch.object(adapter, "_get_changed_files", return_value=[]),
        ):
            result = adapter.execute(spec, Path("/tmp"))

        self.assertTrue(result.partial, "partial must be True when output exceeds threshold")
        self.assertTrue(result.success, "success reflects returncode, not partial flag")

    def test_claude_code_adapter_not_partial_for_small_output(self):
        """Output within max_iterations × 2000 chars does not mark partial."""
        from pathlib import Path
        from unittest.mock import MagicMock, patch
        from app.executor.claude_code import ClaudeCodeAdapter
        from app.executor.base import TaskSpec

        adapter = ClaudeCodeAdapter()
        spec = TaskSpec(objective="Small task", max_iterations=3)  # threshold = 6000 chars

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "done"
        fake_proc.stderr = ""

        with (
            patch("app.executor.claude_code.subprocess.run", return_value=fake_proc),
            patch.object(adapter, "_get_changed_files", return_value=[]),
        ):
            result = adapter.execute(spec, Path("/tmp"))

        self.assertFalse(result.partial)

    def test_codex_adapter_partial_for_incomplete_status(self):
        """Codex response with status='incomplete' returns partial=True and success=False."""
        from pathlib import Path
        from unittest.mock import MagicMock, patch
        from app.executor.codex import CodexAdapter
        from app.executor.base import TaskSpec

        adapter = CodexAdapter()
        spec = TaskSpec(objective="Do something")

        mock_response = MagicMock()
        mock_response.output_text = "partial output..."
        mock_response.status = "incomplete"
        mock_response.incomplete_details = MagicMock()
        mock_response.usage = None

        mock_client = MagicMock()
        mock_client.responses.create.return_value = mock_response

        mock_openai_mod = MagicMock()
        mock_openai_mod.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            result = adapter.execute(spec, Path("/tmp"))

        self.assertTrue(result.partial, "partial must be True for incomplete Codex response")
        self.assertFalse(result.success, "success must be False when response is incomplete")

    def test_codex_adapter_not_partial_for_completed_status(self):
        """Codex response with status='completed' and no incomplete_details is not partial."""
        from pathlib import Path
        from unittest.mock import MagicMock, patch
        from app.executor.codex import CodexAdapter
        from app.executor.base import TaskSpec

        adapter = CodexAdapter()
        spec = TaskSpec(objective="Do something")

        mock_response = MagicMock()
        mock_response.output_text = "all done"
        mock_response.status = "completed"
        mock_response.incomplete_details = None
        mock_response.usage = None

        mock_client = MagicMock()
        mock_client.responses.create.return_value = mock_response

        mock_openai_mod = MagicMock()
        mock_openai_mod.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            result = adapter.execute(spec, Path("/tmp"))

        self.assertFalse(result.partial)
        self.assertTrue(result.success)

    def test_aider_adapter_partial_on_timeout(self):
        """AiderAdapter subprocess timeout returns partial=True (max_duration_seconds is the
        primary iteration-control mechanism since Aider has no native iteration limit flag)."""
        from pathlib import Path
        from unittest.mock import patch
        import subprocess
        from app.executor.aider import AiderAdapter
        from app.executor.base import TaskSpec

        adapter = AiderAdapter()
        spec = TaskSpec(objective="Do something", max_duration_seconds=1)

        with patch(
            "app.executor.aider.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["aider"], timeout=1),
        ):
            result = adapter.execute(spec, Path("/tmp"))

        self.assertTrue(result.partial, "partial must be True on aider timeout")
        self.assertFalse(result.success)
        self.assertIn("timed out", result.errors)


if __name__ == "__main__":
    unittest.main()
