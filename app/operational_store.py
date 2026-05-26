"""Persistencia SQLite para runs, artefatos, aprovacoes e metricas operacionais."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
import uuid


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "squad_runtime.sqlite3"


def _is_validation_initiative(initiative_id: str) -> bool:
    return initiative_id.startswith(("eval-", "baseline-"))


def _connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def _connection(db_path: str | Path = DEFAULT_DB_PATH):
    connection = _connect(db_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_schema(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with _connection(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT '',
                initiative_id TEXT NOT NULL DEFAULT '',
                memory_namespace TEXT NOT NULL DEFAULT '',
                user_goal TEXT NOT NULL,
                active_flow TEXT NOT NULL,
                execution_tier TEXT NOT NULL DEFAULT '',
                execution_policy_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                cos_decision TEXT,
                route_action TEXT,
                route_decision TEXT,
                human_escalation_created INTEGER NOT NULL DEFAULT 0,
                execution_ready INTEGER NOT NULL DEFAULT 0,
                agent_count INTEGER NOT NULL DEFAULT 0,
                c3_count INTEGER NOT NULL DEFAULT 0,
                duration_ms INTEGER,
                operational_packet_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_outputs (
                run_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                ece TEXT NOT NULL,
                schema_valid INTEGER NOT NULL,
                artifact_type TEXT NOT NULL,
                execution_ready INTEGER NOT NULL,
                target_refs_json TEXT NOT NULL,
                validation_issues_json TEXT NOT NULL DEFAULT '[]',
                repair_attempted INTEGER NOT NULL DEFAULT 0,
                output_json TEXT NOT NULL,
                PRIMARY KEY (run_id, agent_name),
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS eval_results (
                evaluation_id TEXT NOT NULL,
                scenario_id TEXT NOT NULL,
                executed_at TEXT NOT NULL,
                run_id TEXT NOT NULL,
                passed INTEGER NOT NULL,
                score REAL NOT NULL,
                findings_json TEXT NOT NULL,
                PRIMARY KEY (evaluation_id, scenario_id)
            );

            CREATE TABLE IF NOT EXISTS baseline_reviews (
                baseline_id TEXT NOT NULL,
                scenario_id TEXT NOT NULL,
                executed_at TEXT NOT NULL,
                run_id TEXT NOT NULL,
                trace_id TEXT,
                execution_status TEXT NOT NULL DEFAULT 'completed',
                provider_error TEXT,
                automatic_score REAL NOT NULL,
                automatic_findings_json TEXT NOT NULL,
                human_correctness REAL,
                human_practical_utility REAL,
                human_scope_control REAL,
                human_next_step_clarity REAL,
                human_execution_confidence REAL,
                human_notes TEXT,
                PRIMARY KEY (baseline_id, scenario_id)
            );

            CREATE TABLE IF NOT EXISTS execution_requests (
                request_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT '',
                initiative_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                effects_enabled INTEGER NOT NULL DEFAULT 0,
                status_reason TEXT NOT NULL DEFAULT '',
                approval_required INTEGER NOT NULL DEFAULT 1,
                requested_effects_json TEXT NOT NULL,
                target_refs_json TEXT NOT NULL,
                actions_json TEXT NOT NULL,
                verification_steps_json TEXT NOT NULL,
                policy_json TEXT NOT NULL,
                packet_json TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS execution_approvals (
                approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                decided_at TEXT NOT NULL,
                decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
                approval_stage TEXT NOT NULL DEFAULT 'prepare',
                decided_by TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (request_id) REFERENCES execution_requests(request_id)
            );

            CREATE TABLE IF NOT EXISTS execution_preparations (
                request_id TEXT PRIMARY KEY,
                prepared_at TEXT NOT NULL,
                preparation_status TEXT NOT NULL,
                workspace_root TEXT NOT NULL,
                patch_sha256 TEXT NOT NULL DEFAULT '',
                patch_text TEXT NOT NULL DEFAULT '',
                target_evidence_json TEXT NOT NULL,
                validation_json TEXT NOT NULL,
                FOREIGN KEY (request_id) REFERENCES execution_requests(request_id)
            );

            CREATE TABLE IF NOT EXISTS execution_applications (
                request_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                application_status TEXT NOT NULL,
                workspace_root TEXT NOT NULL,
                patch_sha256 TEXT NOT NULL,
                validation_json TEXT NOT NULL,
                git_evidence_json TEXT NOT NULL,
                rollback_reason TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (request_id) REFERENCES execution_requests(request_id)
            );

            CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS automation_demands (
                event_id TEXT PRIMARY KEY,
                received_at TEXT NOT NULL,
                source TEXT NOT NULL,
                project_id TEXT NOT NULL,
                initiative_id TEXT NOT NULL,
                user_goal TEXT NOT NULL,
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS human_decisions (
                decision_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT '',
                initiative_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                source_agent TEXT NOT NULL,
                reason TEXT NOT NULL,
                question TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                response TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                decided_by TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS parking_lot_items (
                idea_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                title TEXT NOT NULL,
                context TEXT NOT NULL,
                source_run_id TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT '',
                initiative_id TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'later',
                status TEXT NOT NULL DEFAULT 'candidate'
            );

            CREATE INDEX IF NOT EXISTS idx_workflow_checkpoints_thread
                ON workflow_checkpoints(thread_id, checkpoint_id);
            CREATE INDEX IF NOT EXISTS idx_human_decisions_status
                ON human_decisions(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_parking_lot_status
                ON parking_lot_items(status, updated_at);
            """
        )
        baseline_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(baseline_reviews)").fetchall()
        }
        if "execution_status" not in baseline_columns:
            connection.execute(
                "ALTER TABLE baseline_reviews "
                "ADD COLUMN execution_status TEXT NOT NULL DEFAULT 'completed'"
            )
        if "provider_error" not in baseline_columns:
            connection.execute("ALTER TABLE baseline_reviews ADD COLUMN provider_error TEXT")
        output_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(agent_outputs)").fetchall()
        }
        if "validation_issues_json" not in output_columns:
            connection.execute(
                "ALTER TABLE agent_outputs "
                "ADD COLUMN validation_issues_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "repair_attempted" not in output_columns:
            connection.execute(
                "ALTER TABLE agent_outputs ADD COLUMN repair_attempted INTEGER NOT NULL DEFAULT 0"
            )
        run_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(runs)").fetchall()
        }
        run_migrations = {
            "project_id": "TEXT NOT NULL DEFAULT ''",
            "initiative_id": "TEXT NOT NULL DEFAULT ''",
            "memory_namespace": "TEXT NOT NULL DEFAULT ''",
            "execution_tier": "TEXT NOT NULL DEFAULT ''",
            "execution_policy_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for column_name, column_type in run_migrations.items():
            if column_name not in run_columns:
                connection.execute(
                    f"ALTER TABLE runs ADD COLUMN {column_name} {column_type}"
                )
        request_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(execution_requests)").fetchall()
        }
        request_migrations = {
            "effects_enabled": "INTEGER NOT NULL DEFAULT 0",
            "status_reason": "TEXT NOT NULL DEFAULT ''",
        }
        for column_name, column_type in request_migrations.items():
            if column_name not in request_columns:
                connection.execute(
                    f"ALTER TABLE execution_requests ADD COLUMN {column_name} {column_type}"
                )
        approval_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(execution_approvals)").fetchall()
        }
        if "approval_stage" not in approval_columns:
            connection.execute(
                "ALTER TABLE execution_approvals "
                "ADD COLUMN approval_stage TEXT NOT NULL DEFAULT 'prepare'"
            )


def record_run(
    result: dict[str, Any],
    *,
    run_id: str,
    user_goal: str,
    duration_ms: int | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    initialize_schema(db_path)
    outputs = result.get("structured_outputs", {})
    checks = result.get("orchestrator_checks", {})
    scope = result.get("work_scope", {})
    policy = result.get("execution_policy", {})
    c3_count = sum(
        1 for output in outputs.values()
        if output.get("summary", {}).get("ece") == "C3"
    )
    packet = result.get("operational_packet", {})

    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO runs (
                run_id, created_at, project_id, initiative_id, memory_namespace,
                user_goal, active_flow, execution_tier, execution_policy_json,
                status, cos_decision,
                route_action, route_decision, human_escalation_created,
                execution_ready, agent_count, c3_count, duration_ms,
                operational_packet_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                project_id=excluded.project_id,
                initiative_id=excluded.initiative_id,
                memory_namespace=excluded.memory_namespace,
                active_flow=excluded.active_flow,
                execution_tier=excluded.execution_tier,
                execution_policy_json=excluded.execution_policy_json,
                status=excluded.status,
                cos_decision=excluded.cos_decision,
                route_action=excluded.route_action,
                route_decision=excluded.route_decision,
                human_escalation_created=excluded.human_escalation_created,
                execution_ready=excluded.execution_ready,
                agent_count=excluded.agent_count,
                c3_count=excluded.c3_count,
                duration_ms=excluded.duration_ms,
                operational_packet_json=excluded.operational_packet_json
            """,
            (
                run_id,
                datetime.now(timezone.utc).isoformat(),
                scope.get("project_id", result.get("project_id", "")),
                scope.get("initiative_id", result.get("initiative_id", "")),
                scope.get("memory_namespace", result.get("memory_namespace", "")),
                user_goal,
                result.get("active_flow", ""),
                policy.get("execution_tier", ""),
                json.dumps(policy, ensure_ascii=False),
                result.get("route_status", "unknown"),
                result.get("cos_decision", ""),
                result.get("cos_route_action", ""),
                result.get("route_decision", ""),
                int(result.get("human_escalation_created", False)),
                int(packet.get("execution_ready", False)),
                len(outputs),
                c3_count,
                duration_ms,
                json.dumps(packet, ensure_ascii=False),
            ),
        )

        for agent_name, output in outputs.items():
            artifact = output.get("operational_artifact", {})
            check = checks.get(agent_name, {})
            connection.execute(
                """
                INSERT INTO agent_outputs (
                    run_id, agent_name, ece, schema_valid, artifact_type,
                    execution_ready, target_refs_json, validation_issues_json,
                    repair_attempted, output_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, agent_name) DO UPDATE SET
                    ece=excluded.ece,
                    schema_valid=excluded.schema_valid,
                    artifact_type=excluded.artifact_type,
                    execution_ready=excluded.execution_ready,
                    target_refs_json=excluded.target_refs_json,
                    validation_issues_json=excluded.validation_issues_json,
                    repair_attempted=excluded.repair_attempted,
                    output_json=excluded.output_json
                """,
                (
                    run_id,
                    agent_name,
                    output.get("summary", {}).get("ece", "UNKNOWN"),
                    int(check.get("schema_valid", False)),
                    artifact.get("artifact_type", ""),
                    int(artifact.get("execution_ready", False)),
                    json.dumps(artifact.get("target_refs", []), ensure_ascii=False),
                    json.dumps(check.get("issues", []), ensure_ascii=False),
                    int(check.get("repair_attempted", False)),
                    json.dumps(output, ensure_ascii=False),
                ),
            )

        if result.get("human_escalation_created", False):
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                INSERT INTO human_decisions (
                    decision_id, run_id, created_at, updated_at, project_id,
                    initiative_id, status, source_agent, reason, question,
                    recommendation
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    updated_at=CASE
                        WHEN human_decisions.status='pending' THEN excluded.updated_at
                        ELSE human_decisions.updated_at
                    END,
                    reason=excluded.reason,
                    question=excluded.question,
                    recommendation=excluded.recommendation
                """,
                (
                    f"decision_{run_id}",
                    run_id,
                    now,
                    now,
                    scope.get("project_id", result.get("project_id", "")),
                    scope.get("initiative_id", result.get("initiative_id", "")),
                    "CoS / Orchestrator",
                    result.get("human_escalation_reason", "A squad solicitou decisao humana."),
                    result.get("human_required_decision", "Definir o proximo passo desta demanda."),
                    "Responder antes de permitir qualquer continuidade ou efeito.",
                ),
            )


def record_run_started(
    *,
    run_id: str,
    user_goal: str,
    project_id: str,
    initiative_id: str,
    memory_namespace: str,
    active_flow: str,
    execution_policy: dict[str, Any],
    status: str = "queued",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    """Registra uma run antes do trabalho assíncrono para exibição na console."""
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO runs (
                run_id, created_at, project_id, initiative_id, memory_namespace,
                user_goal, active_flow, execution_tier, execution_policy_json,
                status, execution_ready, agent_count, c3_count,
                operational_packet_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, '{}')
            """,
            (
                run_id,
                datetime.now(timezone.utc).isoformat(),
                project_id,
                initiative_id,
                memory_namespace,
                user_goal,
                active_flow,
                execution_policy.get("execution_tier", ""),
                json.dumps(execution_policy, ensure_ascii=False),
                status,
            ),
        )


def update_run_status(
    run_id: str,
    *,
    status: str,
    operational_packet: dict[str, Any] | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        connection.execute(
            """
            UPDATE runs
            SET status=?, operational_packet_json=?
            WHERE run_id=?
            """,
            (status, json.dumps(operational_packet or {}, ensure_ascii=False), run_id),
        )


def record_eval_result(
    *,
    evaluation_id: str,
    scenario_id: str,
    run_id: str,
    passed: bool,
    score: float,
    findings: list[str],
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO eval_results (
                evaluation_id, scenario_id, executed_at, run_id, passed,
                score, findings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(evaluation_id, scenario_id) DO UPDATE SET
                executed_at=excluded.executed_at,
                run_id=excluded.run_id,
                passed=excluded.passed,
                score=excluded.score,
                findings_json=excluded.findings_json
            """,
            (
                evaluation_id,
                scenario_id,
                datetime.now(timezone.utc).isoformat(),
                run_id,
                int(passed),
                score,
                json.dumps(findings, ensure_ascii=False),
            ),
        )


def record_execution_request(
    request: dict[str, Any],
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    initialize_schema(db_path)
    now = datetime.now(timezone.utc).isoformat()
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO execution_requests (
                request_id, run_id, created_at, updated_at, project_id,
                initiative_id, status, execution_mode, effects_enabled,
                status_reason, approval_required,
                requested_effects_json, target_refs_json, actions_json,
                verification_steps_json, policy_json, packet_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                updated_at=excluded.updated_at,
                project_id=excluded.project_id,
                initiative_id=excluded.initiative_id,
                status=CASE
                    WHEN execution_requests.status IN (
                        'approved_for_dry_run',
                        'awaiting_patch',
                        'awaiting_apply_approval',
                        'approved_for_apply',
                        'applied_validated',
                        'rolled_back',
                        'rejected'
                    )
                    THEN execution_requests.status
                    ELSE excluded.status
                END,
                execution_mode=excluded.execution_mode,
                effects_enabled=excluded.effects_enabled,
                status_reason=excluded.status_reason,
                approval_required=excluded.approval_required,
                requested_effects_json=excluded.requested_effects_json,
                target_refs_json=excluded.target_refs_json,
                actions_json=excluded.actions_json,
                verification_steps_json=excluded.verification_steps_json,
                policy_json=excluded.policy_json,
                packet_json=excluded.packet_json
            """,
            (
                request["request_id"],
                request["run_id"],
                now,
                now,
                request.get("project_id", ""),
                request.get("initiative_id", ""),
                request["status"],
                request["execution_mode"],
                int(request.get("effects_enabled", False)),
                request.get("status_reason", ""),
                int(request.get("approval_required", False)),
                json.dumps(request.get("requested_effects", []), ensure_ascii=False),
                json.dumps(request.get("target_refs", []), ensure_ascii=False),
                json.dumps(request.get("recommended_actions", []), ensure_ascii=False),
                json.dumps(request.get("verification_steps", []), ensure_ascii=False),
                json.dumps(request.get("execution_policy", {}), ensure_ascii=False),
                json.dumps(request.get("operational_packet", {}), ensure_ascii=False),
            ),
        )


def record_execution_decision(
    request_id: str,
    *,
    decision: str,
    decided_by: str,
    notes: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    if decision not in {"approved", "rejected"}:
        raise ValueError("Execution decision must be approved or rejected.")
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        request = connection.execute(
            "SELECT status FROM execution_requests WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if request is None:
            raise ValueError("Execution request not found.")
        if request["status"] != "pending_approval":
            raise ValueError("Only pending execution requests can be decided.")
        status = "approved_for_dry_run" if decision == "approved" else "rejected"
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT INTO execution_approvals (
                request_id, decided_at, decision, approval_stage, decided_by, notes
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (request_id, now, decision, "prepare", decided_by, notes),
        )
        connection.execute(
            """
            UPDATE execution_requests
            SET status=?, status_reason=?, updated_at=?
            WHERE request_id=?
            """,
            (
                status,
                (
                    "Preparation approved; awaiting a scoped candidate diff."
                    if decision == "approved"
                    else "Execution request rejected during preparation approval."
                ),
                now,
                request_id,
            ),
        )
    return execution_request_snapshot(request_id=request_id, db_path=db_path)["requests"][0]


def record_apply_decision(
    request_id: str,
    *,
    decision: str,
    decided_by: str,
    notes: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    if decision not in {"approved", "rejected"}:
        raise ValueError("Execution decision must be approved or rejected.")
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        request = connection.execute(
            "SELECT status FROM execution_requests WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if request is None:
            raise ValueError("Execution request not found.")
        if request["status"] != "awaiting_apply_approval":
            raise ValueError("Only validated patches can receive apply approval.")
        status = "approved_for_apply" if decision == "approved" else "rejected"
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT INTO execution_approvals (
                request_id, decided_at, decision, approval_stage, decided_by, notes
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (request_id, now, decision, "apply", decided_by, notes),
        )
        connection.execute(
            """
            UPDATE execution_requests SET status=?, status_reason=?, updated_at=?
            WHERE request_id=?
            """,
            (
                status,
                (
                    "Patch approved; controlled workspace application is not enabled yet."
                    if decision == "approved"
                    else "Validated patch rejected before workspace application."
                ),
                now,
                request_id,
            ),
        )
    return execution_request_snapshot(request_id=request_id, db_path=db_path)["requests"][0]


def record_execution_preparation(
    preparation: dict[str, Any],
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    initialize_schema(db_path)
    request_id = preparation["request_id"]
    request_status = preparation["request_status"]
    allowed_statuses = {"approved_for_dry_run", "awaiting_patch", "awaiting_apply_approval"}
    with _connection(db_path) as connection:
        request = connection.execute(
            "SELECT status FROM execution_requests WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if request is None:
            raise ValueError("Execution request not found.")
        if request["status"] not in allowed_statuses:
            raise ValueError("Execution request is not approved for preparation.")
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT INTO execution_preparations (
                request_id, prepared_at, preparation_status, workspace_root,
                patch_sha256, patch_text, target_evidence_json, validation_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                prepared_at=excluded.prepared_at,
                preparation_status=excluded.preparation_status,
                workspace_root=excluded.workspace_root,
                patch_sha256=excluded.patch_sha256,
                patch_text=excluded.patch_text,
                target_evidence_json=excluded.target_evidence_json,
                validation_json=excluded.validation_json
            """,
            (
                request_id,
                now,
                preparation["preparation_status"],
                preparation["workspace_root"],
                preparation.get("patch_sha256", ""),
                preparation.get("patch_text", ""),
                json.dumps(preparation.get("target_evidence", []), ensure_ascii=False),
                json.dumps(preparation.get("validation", {}), ensure_ascii=False),
            ),
        )
        connection.execute(
            """
            UPDATE execution_requests SET status=?, status_reason=?, updated_at=?
            WHERE request_id=?
            """,
            (
                request_status,
                preparation.get("status_reason", ""),
                now,
                request_id,
            ),
        )
    return execution_request_snapshot(request_id=request_id, db_path=db_path)["requests"][0]


def record_execution_application(
    application: dict[str, Any],
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    initialize_schema(db_path)
    request_id = application["request_id"]
    with _connection(db_path) as connection:
        request = connection.execute(
            "SELECT status FROM execution_requests WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if request is None:
            raise ValueError("Execution request not found.")
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT INTO execution_applications (
                request_id, applied_at, updated_at, application_status,
                workspace_root, patch_sha256, validation_json,
                git_evidence_json, rollback_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                updated_at=excluded.updated_at,
                application_status=excluded.application_status,
                workspace_root=excluded.workspace_root,
                patch_sha256=excluded.patch_sha256,
                validation_json=excluded.validation_json,
                git_evidence_json=excluded.git_evidence_json,
                rollback_reason=excluded.rollback_reason
            """,
            (
                request_id,
                now,
                now,
                application["application_status"],
                application["workspace_root"],
                application["patch_sha256"],
                json.dumps(application.get("validation", {}), ensure_ascii=False),
                json.dumps(application.get("git_evidence", {}), ensure_ascii=False),
                application.get("rollback_reason", ""),
            ),
        )
        connection.execute(
            """
            UPDATE execution_requests
            SET status=?, status_reason=?, effects_enabled=?, updated_at=?
            WHERE request_id=?
            """,
            (
                application["request_status"],
                application["status_reason"],
                int(application.get("effects_enabled", False)),
                now,
                request_id,
            ),
        )
    return execution_request_snapshot(request_id=request_id, db_path=db_path)["requests"][0]


def record_workflow_checkpoint(
    *,
    thread_id: str,
    run_id: str,
    stage: str,
    status: str,
    payload: dict[str, Any],
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO workflow_checkpoints (
                thread_id, run_id, recorded_at, stage, status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                thread_id,
                run_id,
                datetime.now(timezone.utc).isoformat(),
                stage,
                status,
                json.dumps(payload, ensure_ascii=False),
            ),
        )


def workflow_checkpoint_snapshot(
    *,
    thread_id: str | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        if thread_id:
            rows = connection.execute(
                """
                SELECT * FROM workflow_checkpoints
                WHERE thread_id=?
                ORDER BY checkpoint_id ASC
                """,
                (thread_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM workflow_checkpoints ORDER BY checkpoint_id DESC"
            ).fetchall()
    checkpoints = [dict(row) for row in rows]
    for checkpoint in checkpoints:
        checkpoint["payload"] = json.loads(checkpoint.pop("payload_json"))
    return {"checkpoint_count": len(checkpoints), "checkpoints": checkpoints}


def pending_work_snapshot(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    pending_statuses = {
        "pending_approval": "Aprovar ou rejeitar preparacao.",
        "approved_for_dry_run": "Enviar diff candidato para preparacao.",
        "awaiting_patch": "Enviar diff corrigido.",
        "awaiting_apply_approval": "Aprovar ou rejeitar aplicacao.",
        "approved_for_apply": "Aplicar patch aprovado e executar validacoes.",
        "applied_validated": "Revisar entrega; commit/push manual ou rollback.",
    }
    snapshot = execution_request_snapshot(db_path=db_path)
    pending: list[dict[str, Any]] = []
    for request in snapshot["requests"]:
        status = request["status"]
        if status not in pending_statuses:
            continue
        pending.append(
            {
                "thread_id": request["run_id"],
                "request_id": request["request_id"],
                "project_id": request["project_id"],
                "initiative_id": request["initiative_id"],
                "status": status,
                "next_action": pending_statuses[status],
                "target_refs": request["target_refs"],
                "updated_at": request["updated_at"],
            }
        )
    return {"pending_count": len(pending), "pending": pending}


def recent_runs_snapshot(
    *,
    limit: int = 20,
    include_validation: bool = True,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    initialize_schema(db_path)
    safe_limit = max(1, min(limit, 100))
    scope_filter = "" if include_validation else "WHERE initiative_id NOT LIKE 'eval-%' AND initiative_id NOT LIKE 'baseline-%'"
    with _connection(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT run_id, created_at, project_id, initiative_id, user_goal,
                active_flow, execution_tier, status, execution_ready,
                agent_count, duration_ms
            FROM runs
            {scope_filter}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return {"run_count": len(rows), "runs": [dict(row) for row in rows]}


def record_automation_demand(
    *,
    event_id: str,
    source: str,
    project_id: str,
    initiative_id: str,
    user_goal: str,
    metadata: dict[str, Any] | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    if not event_id.strip() or not user_goal.strip():
        raise ValueError("Automation event_id and user_goal are required.")
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO automation_demands (
                event_id, received_at, source, project_id, initiative_id,
                user_goal, status, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (
                event_id.strip()[:120],
                datetime.now(timezone.utc).isoformat(),
                source.strip()[:40] or "n8n",
                project_id.strip()[:120] or "default",
                initiative_id.strip()[:120] or "default",
                user_goal.strip()[:4000],
                "queued_for_review",
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        row = connection.execute(
            "SELECT * FROM automation_demands WHERE event_id=?",
            (event_id.strip()[:120],),
        ).fetchone()
    demand = dict(row)
    demand["metadata"] = json.loads(demand.pop("metadata_json"))
    return demand


def automation_demand_snapshot(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM automation_demands ORDER BY received_at DESC LIMIT 100"
        ).fetchall()
    demands = [dict(row) for row in rows]
    for demand in demands:
        demand["metadata"] = json.loads(demand.pop("metadata_json"))
    return {"demand_count": len(demands), "demands": demands}


def human_decision_snapshot(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    include_validation: bool = False,
) -> dict[str, Any]:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        legacy_rows = connection.execute(
            """
            SELECT run_id, created_at, project_id, initiative_id, cos_decision,
                   route_decision, operational_packet_json
            FROM runs
            WHERE human_escalation_created=1
              AND run_id NOT IN (SELECT run_id FROM human_decisions)
            """
        ).fetchall()
        rows = connection.execute(
            "SELECT * FROM human_decisions ORDER BY status='pending' DESC, updated_at DESC"
        ).fetchall()
    decisions = [
        dict(row) for row in rows
        if include_validation or not _is_validation_initiative(row["initiative_id"])
    ]
    for row in legacy_rows:
        if not include_validation and _is_validation_initiative(row["initiative_id"]):
            continue
        packet = json.loads(row["operational_packet_json"] or "{}")
        decisions.append(
            {
                "decision_id": f"decision_{row['run_id']}",
                "run_id": row["run_id"],
                "created_at": row["created_at"],
                "updated_at": row["created_at"],
                "project_id": row["project_id"],
                "initiative_id": row["initiative_id"],
                "status": "pending",
                "source_agent": "CoS / Orchestrator",
                "reason": f"Decisao {row['cos_decision'] or row['route_decision']} exige validacao humana.",
                "question": packet.get("human_checkpoint") or "Definir o proximo passo desta demanda.",
                "recommendation": "Responder antes de permitir continuidade ou efeitos.",
                "response": "",
                "resolution": "",
                "decided_by": "",
            }
        )
    decisions.sort(key=lambda item: (item["status"] == "pending", item["updated_at"]), reverse=True)
    pending_count = sum(1 for item in decisions if item["status"] == "pending")
    return {"decision_count": len(decisions), "pending_count": pending_count, "decisions": decisions}


def respond_human_decision(
    decision_id: str,
    *,
    response: str,
    resolution: str,
    decided_by: str = "owner",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    response = response.strip()
    allowed_resolutions = {"continue", "request_revision", "close"}
    if not response:
        raise ValueError("Registre a decisao humana antes de concluir.")
    if resolution not in allowed_resolutions:
        raise ValueError("Resolucao humana invalida.")
    initialize_schema(db_path)
    now = datetime.now(timezone.utc).isoformat()
    with _connection(db_path) as connection:
        row = connection.execute(
            "SELECT run_id, status FROM human_decisions WHERE decision_id=?",
            (decision_id,),
        ).fetchone()
        if row is None:
            legacy_run_id = decision_id.removeprefix("decision_")
            if legacy_run_id == decision_id:
                raise ValueError("Decisao humana nao encontrada.")
            legacy = connection.execute(
                """
                SELECT run_id, created_at, project_id, initiative_id, cos_decision,
                       route_decision, operational_packet_json
                FROM runs
                WHERE human_escalation_created=1 AND run_id=?
                """,
                (legacy_run_id,),
            ).fetchone()
            if legacy is None:
                raise ValueError("Decisao humana nao encontrada.")
            packet = json.loads(legacy["operational_packet_json"] or "{}")
            connection.execute(
                """
                INSERT INTO human_decisions (
                    decision_id, run_id, created_at, updated_at, project_id,
                    initiative_id, status, source_agent, reason, question,
                    recommendation
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    legacy["run_id"],
                    legacy["created_at"],
                    legacy["created_at"],
                    legacy["project_id"],
                    legacy["initiative_id"],
                    "CoS / Orchestrator",
                    f"Decisao {legacy['cos_decision'] or legacy['route_decision']} exige validacao humana.",
                    packet.get("human_checkpoint") or "Definir o proximo passo desta demanda.",
                    "Responder antes de permitir continuidade ou efeitos.",
                ),
            )
            row = {"run_id": legacy["run_id"], "status": "pending"}
        if row["status"] != "pending":
            raise ValueError("A decisao humana ja foi respondida.")
        connection.execute(
            """
            UPDATE human_decisions SET
                updated_at=?, status='resolved', response=?, resolution=?, decided_by=?
            WHERE decision_id=?
            """,
            (now, response[:4000], resolution, decided_by.strip()[:120] or "owner", decision_id),
        )
    record_workflow_checkpoint(
        thread_id=row["run_id"],
        run_id=row["run_id"],
        stage="human_decision_resolved",
        status=resolution,
        payload={"decision_id": decision_id, "resolution": resolution, "decided_by": decided_by},
        db_path=db_path,
    )
    return next(
        item
        for item in human_decision_snapshot(db_path, include_validation=True)["decisions"]
        if item["decision_id"] == decision_id
    )


def run_flow_snapshot(
    *,
    run_id: str | None = None,
    include_validation: bool = False,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        if not run_id:
            latest = connection.execute(
                """
                SELECT run_id FROM runs
                WHERE agent_count > 0
                  AND (? OR (initiative_id NOT LIKE 'eval-%' AND initiative_id NOT LIKE 'baseline-%'))
                ORDER BY created_at DESC LIMIT 1
                """,
                (include_validation,),
            ).fetchone()
            run_id = latest["run_id"] if latest else ""
        run = connection.execute(
            """
            SELECT run_id, project_id, initiative_id, user_goal, active_flow,
                   execution_tier, status, cos_decision, route_decision
            FROM runs WHERE run_id=?
            """,
            (run_id,),
        ).fetchone()
        rows = connection.execute(
            """
            SELECT agent_name, ece, schema_valid, artifact_type, execution_ready
            FROM agent_outputs WHERE run_id=? ORDER BY rowid ASC
            """,
            (run_id,),
        ).fetchall()
    agents = [dict(row) for row in rows]
    links = [
        {"from": agents[index]["agent_name"], "to": agents[index + 1]["agent_name"]}
        for index in range(len(agents) - 1)
    ]
    return {"run": dict(run) if run else None, "agents": agents, "links": links}


def cost_snapshot(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT project_id, initiative_id, execution_tier, execution_policy_json,
                   status, agent_count
            FROM runs ORDER BY created_at DESC
            """
        ).fetchall()
    by_tier: dict[str, dict[str, Any]] = {}
    by_project: dict[str, dict[str, Any]] = {}
    total_budget = 0.0
    validation_budget = 0.0
    validation_runs = 0
    for row in rows:
        policy = json.loads(row["execution_policy_json"] or "{}")
        estimated = float(policy.get("max_cost_usd") or 0)
        total_budget += estimated
        if _is_validation_initiative(row["initiative_id"]):
            validation_budget += estimated
            validation_runs += 1
        tier = row["execution_tier"] or "legacy"
        tier_item = by_tier.setdefault(tier, {"tier": tier, "runs": 0, "estimated_budget_usd": 0.0})
        tier_item["runs"] += 1
        tier_item["estimated_budget_usd"] += estimated
        project = row["project_id"] or "default"
        project_item = by_project.setdefault(
            project, {"project_id": project, "runs": 0, "estimated_budget_usd": 0.0}
        )
        project_item["runs"] += 1
        project_item["estimated_budget_usd"] += estimated
    try:
        sampling_rate = float(os.getenv("LANGSMITH_TRACING_SAMPLING_RATE", "0") or 0)
    except ValueError:
        sampling_rate = 0.0
    return {
        "label": "Orcamento maximo estimado",
        "total_runs": len(rows),
        "estimated_budget_usd": round(total_budget, 2),
        "validation_runs": validation_runs,
        "validation_estimated_budget_usd": round(validation_budget, 2),
        "real_cost_available": False,
        "tracing_enabled": os.getenv("LANGSMITH_TRACING", "").lower() == "true",
        "sampling_rate": sampling_rate,
        "by_tier": sorted(by_tier.values(), key=lambda item: item["estimated_budget_usd"], reverse=True),
        "by_project": sorted(by_project.values(), key=lambda item: item["estimated_budget_usd"], reverse=True)[:8],
    }


def board_snapshot(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    runs = recent_runs_snapshot(limit=100, include_validation=False, db_path=db_path)["runs"]
    requests = execution_request_snapshot(db_path=db_path)["requests"]
    demands = automation_demand_snapshot(db_path)["demands"]
    pending_decisions = {
        item["run_id"] for item in human_decision_snapshot(db_path)["decisions"] if item["status"] == "pending"
    }
    request_by_run = {item["run_id"]: item["status"] for item in requests}
    columns = {key: [] for key in ("planned", "in_progress", "human_decision", "approval", "done", "blocked")}
    for demand in demands:
        if demand["status"] == "queued_for_review":
            columns["planned"].append(
                {
                    "run_id": demand["event_id"],
                    "user_goal": demand["user_goal"],
                    "project_id": demand["project_id"],
                    "initiative_id": demand["initiative_id"],
                    "status": demand["status"],
                    "execution_tier": "entrada externa",
                }
            )
    for run in runs:
        status = run["status"]
        request_status = request_by_run.get(run["run_id"], "")
        card = {**run, "request_status": request_status}
        if run["run_id"] in pending_decisions:
            column = "human_decision"
        elif request_status in {
            "pending_approval", "approved_for_dry_run", "awaiting_patch",
            "awaiting_apply_approval", "approved_for_apply", "applied_validated",
        }:
            column = "approval"
        elif status in {"queued"}:
            column = "planned"
        elif status in {"running"}:
            column = "in_progress"
        elif status in {"failed"} or request_status in {"not_actionable", "rejected", "rolled_back"}:
            column = "blocked"
        else:
            column = "done"
        columns[column].append(card)
    return {"columns": columns}


def create_parking_lot_item(
    payload: dict[str, Any],
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    title = str(payload.get("title", "")).strip()
    context = str(payload.get("context", "")).strip()
    if not title or not context:
        raise ValueError("Titulo e contexto da ideia sao obrigatorios.")
    priority = str(payload.get("priority", "later")).strip()
    if priority not in {"later", "consider", "high"}:
        raise ValueError("Prioridade da ideia invalida.")
    item_id = f"idea_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).isoformat()
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO parking_lot_items (
                idea_id, created_at, updated_at, title, context, source_run_id,
                project_id, initiative_id, priority, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate')
            """,
            (
                item_id, now, now, title[:240], context[:4000],
                str(payload.get("source_run_id", "")).strip()[:120],
                str(payload.get("project_id", "")).strip()[:120],
                str(payload.get("initiative_id", "")).strip()[:120],
                priority,
            ),
        )
    return next(item for item in parking_lot_snapshot(db_path)["ideas"] if item["idea_id"] == item_id)


def parking_lot_snapshot(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM parking_lot_items ORDER BY status='candidate' DESC, updated_at DESC"
        ).fetchall()
    return {"idea_count": len(rows), "ideas": [dict(row) for row in rows]}


def promote_parking_lot_item(idea_id: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        item = connection.execute(
            "SELECT * FROM parking_lot_items WHERE idea_id=?",
            (idea_id,),
        ).fetchone()
        if item is None:
            raise ValueError("Ideia nao encontrada.")
        if item["status"] != "candidate":
            raise ValueError("A ideia ja foi promovida ou encerrada.")
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "UPDATE parking_lot_items SET status='promoted', updated_at=? WHERE idea_id=?",
            (now, idea_id),
        )
    record_automation_demand(
        event_id=f"parking:{idea_id}",
        source="parking_lot",
        project_id=item["project_id"] or "default",
        initiative_id=item["initiative_id"] or "default",
        user_goal=item["title"],
        metadata={"context": item["context"], "idea_id": idea_id},
        db_path=db_path,
    )
    return next(item for item in parking_lot_snapshot(db_path)["ideas"] if item["idea_id"] == idea_id)


def execution_request_snapshot(
    *,
    request_id: str | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        if request_id:
            rows = connection.execute(
                "SELECT * FROM execution_requests WHERE request_id=?",
                (request_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM execution_requests ORDER BY created_at DESC"
            ).fetchall()
        requests = [dict(row) for row in rows]
        for request in requests:
            approval_rows = connection.execute(
                """
                SELECT decision, approval_stage, decided_at, decided_by, notes
                FROM execution_approvals
                WHERE request_id=?
                ORDER BY approval_id ASC
                """,
                (request["request_id"],),
            ).fetchall()
            request["approval_required"] = bool(request["approval_required"])
            request["effects_enabled"] = bool(request["effects_enabled"])
            request["requested_effects"] = json.loads(request.pop("requested_effects_json"))
            request["target_refs"] = json.loads(request.pop("target_refs_json"))
            request["recommended_actions"] = json.loads(request.pop("actions_json"))
            request["verification_steps"] = json.loads(
                request.pop("verification_steps_json")
            )
            request["execution_policy"] = json.loads(request.pop("policy_json"))
            request["operational_packet"] = json.loads(request.pop("packet_json"))
            request["approvals"] = [dict(row) for row in approval_rows]
            preparation = connection.execute(
                "SELECT * FROM execution_preparations WHERE request_id=?",
                (request["request_id"],),
            ).fetchone()
            request["preparation"] = dict(preparation) if preparation else None
            if request["preparation"]:
                request["preparation"]["target_evidence"] = json.loads(
                    request["preparation"].pop("target_evidence_json")
                )
                request["preparation"]["validation"] = json.loads(
                    request["preparation"].pop("validation_json")
                )
            application = connection.execute(
                "SELECT * FROM execution_applications WHERE request_id=?",
                (request["request_id"],),
            ).fetchone()
            request["application"] = dict(application) if application else None
            if request["application"]:
                request["application"]["validation"] = json.loads(
                    request["application"].pop("validation_json")
                )
                request["application"]["git_evidence"] = json.loads(
                    request["application"].pop("git_evidence_json")
                )
    return {"request_count": len(requests), "requests": requests}


def record_baseline_result(
    *,
    baseline_id: str,
    scenario_id: str,
    run_id: str,
    trace_id: str,
    automatic_score: float,
    findings: list[str],
    execution_status: str = "completed",
    provider_error: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO baseline_reviews (
                baseline_id, scenario_id, executed_at, run_id, trace_id,
                execution_status, provider_error, automatic_score, automatic_findings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(baseline_id, scenario_id) DO UPDATE SET
                executed_at=excluded.executed_at,
                run_id=excluded.run_id,
                trace_id=excluded.trace_id,
                execution_status=excluded.execution_status,
                provider_error=excluded.provider_error,
                automatic_score=excluded.automatic_score,
                automatic_findings_json=excluded.automatic_findings_json
            """,
            (
                baseline_id,
                scenario_id,
                datetime.now(timezone.utc).isoformat(),
                run_id,
                trace_id,
                execution_status,
                provider_error,
                automatic_score,
                json.dumps(findings, ensure_ascii=False),
            ),
        )


def update_human_review(
    *,
    baseline_id: str,
    scenario_id: str,
    correctness: float,
    practical_utility: float,
    scope_control: float,
    next_step_clarity: float,
    execution_confidence: float,
    notes: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    initialize_schema(db_path)
    scores = (correctness, practical_utility, scope_control, next_step_clarity, execution_confidence)
    if any(score < 0 or score > 10 for score in scores):
        raise ValueError("Human review scores must be between 0 and 10.")
    with _connection(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE baseline_reviews SET
                human_correctness=?,
                human_practical_utility=?,
                human_scope_control=?,
                human_next_step_clarity=?,
                human_execution_confidence=?,
                human_notes=?
            WHERE baseline_id=? AND scenario_id=?
            """,
            (
                correctness,
                practical_utility,
                scope_control,
                next_step_clarity,
                execution_confidence,
                notes,
                baseline_id,
                scenario_id,
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError("Baseline scenario not found for human review.")


def metrics_snapshot(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    include_validation: bool = True,
) -> dict[str, Any]:
    initialize_schema(db_path)
    scope_filter = "" if include_validation else "WHERE initiative_id NOT LIKE 'eval-%' AND initiative_id NOT LIKE 'baseline-%'"
    with _connection(db_path) as connection:
        totals = connection.execute(
            f"""
            SELECT
                COUNT(*) AS run_count,
                COALESCE(AVG(agent_count), 0) AS average_agents_per_run,
                COALESCE(AVG(c3_count), 0) AS average_c3_per_run,
                COALESCE(AVG(duration_ms), 0) AS average_duration_ms,
                COALESCE(SUM(human_escalation_created), 0) AS escalation_count,
                COALESCE(SUM(execution_ready), 0) AS execution_ready_count
            FROM runs
            {scope_filter}
            """
        ).fetchone()
        flow_rows = connection.execute(
            f"""
            SELECT active_flow, COUNT(*) AS run_count, AVG(agent_count) AS average_agents
            FROM runs
            {scope_filter}
            GROUP BY active_flow
            ORDER BY run_count DESC, active_flow ASC
            """
        ).fetchall()
        tier_rows = connection.execute(
            f"""
            SELECT execution_tier, COUNT(*) AS run_count, AVG(duration_ms) AS average_duration_ms
            FROM runs
            {scope_filter}
            GROUP BY execution_tier
            ORDER BY run_count DESC, execution_tier ASC
            """
        ).fetchall()
        request_rows = connection.execute(
            """
            SELECT status, COUNT(*) AS request_count
            FROM execution_requests
            GROUP BY status
            ORDER BY status ASC
            """
        ).fetchall()

    run_count = totals["run_count"]
    return {
        "run_count": run_count,
        "average_agents_per_run": round(totals["average_agents_per_run"], 2),
        "average_c3_per_run": round(totals["average_c3_per_run"], 2),
        "average_duration_ms": round(totals["average_duration_ms"], 2),
        "escalation_rate": round(totals["escalation_count"] / run_count, 4) if run_count else 0.0,
        "execution_ready_rate": round(totals["execution_ready_count"] / run_count, 4) if run_count else 0.0,
        "flows": [
            {
                "active_flow": row["active_flow"],
                "run_count": row["run_count"],
                "average_agents": round(row["average_agents"], 2),
            }
            for row in flow_rows
        ],
        "execution_tiers": [
            {
                "execution_tier": row["execution_tier"] or "legacy",
                "run_count": row["run_count"],
                "average_duration_ms": round(row["average_duration_ms"] or 0, 2),
            }
            for row in tier_rows
        ],
        "execution_requests": [
            {
                "status": row["status"],
                "request_count": row["request_count"],
            }
            for row in request_rows
        ],
    }


def baseline_snapshot(baseline_id: str, db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM baseline_reviews
            WHERE baseline_id=?
            ORDER BY scenario_id
            """,
            (baseline_id,),
        ).fetchall()
    results = [dict(row) for row in rows]
    for result in results:
        result["automatic_findings"] = json.loads(result.pop("automatic_findings_json"))
        result["quality_score_eligible"] = result["execution_status"] == "completed"
        if not result["quality_score_eligible"]:
            result["automatic_score"] = None
        human_scores = [
            result["human_correctness"],
            result["human_practical_utility"],
            result["human_scope_control"],
            result["human_next_step_clarity"],
            result["human_execution_confidence"],
        ]
        result["human_average_score"] = (
            round(sum(human_scores) / len(human_scores), 2)
            if all(score is not None for score in human_scores)
            else None
        )
    return {
        "baseline_id": baseline_id,
        "scenario_count": len(results),
        "results": results,
    }
