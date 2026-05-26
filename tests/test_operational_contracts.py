import unittest
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

import app.graph as graph_module
import app.pilot_readiness as pilot_readiness_module
from app.evals import EvalScenario, assess_scenario
from app.mock_model import MockResponse
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
    create_execution_request,
    decide_apply_execution,
    decide_execution_request,
    prepare_execution_request,
    rollback_execution_request,
)
from app.operational_store import (
    baseline_snapshot,
    execution_request_snapshot,
    metrics_snapshot,
    pending_work_snapshot,
    record_baseline_result,
    record_run,
    update_human_review,
    workflow_checkpoint_snapshot,
)
from app.orchestrator import inspect_agent_output, update_orchestrator_checks
from app.retry_policy import initialize_retry_state, resolve_retry_permission
from app.structured_output import CoSOutputEnvelope, mock_agent_output, safe_parse_agent_output
from app.intake import decide_intake
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
                    ],
                )
                pending = pending_work_snapshot(db_path)
                self.assertEqual(pending["pending"][0]["next_action"], "Revisar entrega; commit/push manual ou rollback.")

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


if __name__ == "__main__":
    unittest.main()
