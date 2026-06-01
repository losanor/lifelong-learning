"""Persistencia SQLite para runs, artefatos, aprovacoes e metricas operacionais."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
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
                trace_id TEXT NOT NULL DEFAULT '',
                observed_cost_usd REAL,
                observed_tokens INTEGER,
                trace_url TEXT NOT NULL DEFAULT '',
                observability_status TEXT NOT NULL DEFAULT '',
                observability_error TEXT NOT NULL DEFAULT '',
                observability_synced_at TEXT,
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

            CREATE TABLE IF NOT EXISTS handoff_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                source_agent TEXT NOT NULL,
                target_agent TEXT NOT NULL,
                artifact TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                ece TEXT NOT NULL DEFAULT '',
                blockers TEXT NOT NULL DEFAULT '',
                next_step TEXT NOT NULL DEFAULT '',
                escalate_to_cos TEXT NOT NULL DEFAULT '',
                memory_namespace TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS initiative_documents (
                memory_namespace TEXT PRIMARY KEY,
                bound_at TEXT NOT NULL,
                workspace_root TEXT NOT NULL,
                document_ref TEXT NOT NULL,
                document_name TEXT NOT NULL,
                document_format TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                character_count INTEGER NOT NULL DEFAULT 0,
                included_characters INTEGER NOT NULL DEFAULT 0,
                truncated INTEGER NOT NULL DEFAULT 0
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

            CREATE TABLE IF NOT EXISTS git_deliveries (
                request_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                delivery_status TEXT NOT NULL,
                workspace_root TEXT NOT NULL,
                branch_name TEXT NOT NULL DEFAULT '',
                base_branch TEXT NOT NULL DEFAULT '',
                commit_sha TEXT NOT NULL DEFAULT '',
                commit_message TEXT NOT NULL DEFAULT '',
                remote_name TEXT NOT NULL DEFAULT 'origin',
                pr_url TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '{}',
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
            CREATE INDEX IF NOT EXISTS idx_handoff_events_run
                ON handoff_events(run_id, event_id);
            CREATE INDEX IF NOT EXISTS idx_human_decisions_status
                ON human_decisions(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_parking_lot_status
                ON parking_lot_items(status, updated_at);

            CREATE TABLE IF NOT EXISTS executor_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL DEFAULT '',
                agent_name TEXT NOT NULL DEFAULT '',
                executor_used TEXT NOT NULL DEFAULT '',
                success INTEGER NOT NULL DEFAULT 0,
                files_changed_json TEXT NOT NULL DEFAULT '[]',
                tokens_used INTEGER,
                partial INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_executor_runs_run_id
                ON executor_runs(run_id, id);
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
            "trace_id": "TEXT NOT NULL DEFAULT ''",
            "observed_cost_usd": "REAL",
            "observed_tokens": "INTEGER",
            "trace_url": "TEXT NOT NULL DEFAULT ''",
            "observability_status": "TEXT NOT NULL DEFAULT ''",
            "observability_error": "TEXT NOT NULL DEFAULT ''",
            "observability_synced_at": "TEXT",
            "cache_metrics_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for column_name, column_type in run_migrations.items():
            if column_name not in run_columns:
                connection.execute(
                    f"ALTER TABLE runs ADD COLUMN {column_name} {column_type}"
                )
        connection.execute(
            """
            UPDATE runs SET trace_id=(
                SELECT baseline_reviews.trace_id FROM baseline_reviews
                WHERE baseline_reviews.run_id=runs.run_id AND baseline_reviews.trace_id IS NOT NULL
                LIMIT 1
            )
            WHERE trace_id=''
              AND EXISTS (
                SELECT 1 FROM baseline_reviews
                WHERE baseline_reviews.run_id=runs.run_id AND baseline_reviews.trace_id IS NOT NULL
              )
            """
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
    trace_id: str = "",
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

    cache_metrics = result.get("cache_metrics") or {}

    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO runs (
                run_id, created_at, project_id, initiative_id, memory_namespace,
                user_goal, active_flow, execution_tier, execution_policy_json,
                status, cos_decision,
                route_action, route_decision, human_escalation_created,
                execution_ready, agent_count, c3_count, duration_ms,
                trace_id, operational_packet_json, cache_metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                trace_id=CASE
                    WHEN excluded.trace_id != '' THEN excluded.trace_id
                    ELSE runs.trace_id
                END,
                operational_packet_json=excluded.operational_packet_json,
                cache_metrics_json=excluded.cache_metrics_json
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
                trace_id,
                json.dumps(packet, ensure_ascii=False),
                json.dumps(cache_metrics, ensure_ascii=False),
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
    trace_id: str = "",
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
                trace_id, operational_packet_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, '{}')
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
                trace_id,
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


def record_handoff_event(
    *,
    run_id: str,
    source_agent: str,
    target_agent: str,
    artifact: str,
    summary: str,
    ece: str,
    blockers: str = "",
    next_step: str = "",
    escalate_to_cos: str = "",
    memory_namespace: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    if not run_id.strip():
        return
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO handoff_events (
                run_id, occurred_at, source_agent, target_agent, artifact,
                summary, ece, blockers, next_step, escalate_to_cos, memory_namespace
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id[:120],
                datetime.now(timezone.utc).isoformat(),
                source_agent[:120],
                target_agent[:120],
                artifact[:240],
                summary[:4000],
                ece[:40],
                blockers[:2000],
                next_step[:2000],
                escalate_to_cos[:500],
                memory_namespace[:250],
            ),
        )


def bind_initiative_document(
    memory_namespace: str,
    workspace_root: str,
    document: dict[str, Any],
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO initiative_documents (
                memory_namespace, bound_at, workspace_root, document_ref,
                document_name, document_format, sha256, character_count,
                included_characters, truncated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_namespace) DO UPDATE SET
                bound_at=excluded.bound_at,
                workspace_root=excluded.workspace_root,
                document_ref=excluded.document_ref,
                document_name=excluded.document_name,
                document_format=excluded.document_format,
                sha256=excluded.sha256,
                character_count=excluded.character_count,
                included_characters=excluded.included_characters,
                truncated=excluded.truncated
            """,
            (
                memory_namespace[:250],
                datetime.now(timezone.utc).isoformat(),
                str(Path(workspace_root).resolve()),
                document["document_ref"][:500],
                document["document_name"][:240],
                document["document_format"][:20],
                document["sha256"][:80],
                int(document["character_count"]),
                int(document["included_characters"]),
                int(document["truncated"]),
            ),
        )


def initiative_document_snapshot(
    memory_namespace: str,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM initiative_documents WHERE memory_namespace=?",
            (memory_namespace,),
        ).fetchone()
    if not row:
        return None
    document = dict(row)
    document["truncated"] = bool(document["truncated"])
    return document


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
                        'git_committed',
                        'pr_opened',
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


def update_execution_request_target_refs(
    request_id: str,
    *,
    target_refs: list[str],
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        request = connection.execute(
            "SELECT packet_json FROM execution_requests WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if request is None:
            raise ValueError("Execution request not found.")
        packet = json.loads(request["packet_json"])
        packet["target_refs"] = target_refs
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            UPDATE execution_requests
            SET target_refs_json=?, packet_json=?, updated_at=?
            WHERE request_id=?
            """,
            (
                json.dumps(target_refs, ensure_ascii=False),
                json.dumps(packet, ensure_ascii=False),
                now,
                request_id,
            ),
        )
    return execution_request_snapshot(request_id=request_id, db_path=db_path)["requests"][0]


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


def record_git_delivery(
    delivery: dict[str, Any],
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    initialize_schema(db_path)
    request_id = delivery["request_id"]
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
            INSERT INTO git_deliveries (
                request_id, created_at, updated_at, delivery_status,
                workspace_root, branch_name, base_branch, commit_sha,
                commit_message, remote_name, pr_url, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                updated_at=excluded.updated_at,
                delivery_status=excluded.delivery_status,
                workspace_root=excluded.workspace_root,
                branch_name=excluded.branch_name,
                base_branch=excluded.base_branch,
                commit_sha=excluded.commit_sha,
                commit_message=excluded.commit_message,
                remote_name=excluded.remote_name,
                pr_url=excluded.pr_url,
                evidence_json=excluded.evidence_json
            """,
            (
                request_id,
                now,
                now,
                delivery["delivery_status"],
                delivery["workspace_root"],
                delivery.get("branch_name", ""),
                delivery.get("base_branch", ""),
                delivery.get("commit_sha", ""),
                delivery.get("commit_message", ""),
                delivery.get("remote_name", "origin"),
                delivery.get("pr_url", ""),
                json.dumps(delivery.get("evidence", {}), ensure_ascii=False),
            ),
        )
        connection.execute(
            """
            UPDATE execution_requests
            SET status=?, status_reason=?, effects_enabled=1, updated_at=?
            WHERE request_id=?
            """,
            (
                delivery["request_status"],
                delivery.get("status_reason", ""),
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


def record_human_decision(
    *,
    decision_id: str,
    run_id: str,
    project_id: str,
    initiative_id: str,
    source_agent: str,
    reason: str,
    question: str,
    recommendation: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    initialize_schema(db_path)
    now = datetime.now(timezone.utc).isoformat()
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO human_decisions (
                decision_id, run_id, created_at, updated_at, project_id,
                initiative_id, status, source_agent, reason, question,
                recommendation
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                decision_id=excluded.decision_id,
                updated_at=CASE
                    WHEN human_decisions.status='pending' THEN excluded.updated_at
                    ELSE excluded.updated_at
                END,
                status='pending',
                response='',
                resolution='',
                decided_by='',
                source_agent=excluded.source_agent,
                reason=excluded.reason,
                question=excluded.question,
                recommendation=excluded.recommendation
            """,
            (
                decision_id[:160],
                run_id[:120],
                now,
                now,
                project_id[:120],
                initiative_id[:120],
                source_agent[:120],
                reason[:4000],
                question[:4000],
                recommendation[:4000],
            ),
        )
    return next(
        item
        for item in human_decision_snapshot(db_path, include_validation=True)["decisions"]
        if item["decision_id"] == decision_id[:160]
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
        "applied_validated": "Criar branch e commit supervisionado ou reverter aplicacao.",
        "git_committed": "Publicar branch e abrir PR draft apos revisao humana.",
    }
    snapshot = execution_request_snapshot(db_path=db_path)
    decision_snapshot = human_decision_snapshot(db_path, include_validation=True)
    pending_decision_by_run = {
        item["run_id"]: item
        for item in decision_snapshot["decisions"]
        if item["status"] == "pending" and item.get("is_actionable")
    }
    pending: list[dict[str, Any]] = []
    for request in snapshot["requests"]:
        status = request["status"]
        if status not in pending_statuses:
            continue
        decision = pending_decision_by_run.get(request["run_id"])
        pending.append(
            {
                "thread_id": request["run_id"],
                "request_id": request["request_id"],
                "project_id": request["project_id"],
                "initiative_id": request["initiative_id"],
                "status": status,
                "next_action": (
                    f"Responder pendencia humana: {decision['decision_title']}."
                    if decision
                    else pending_statuses[status]
                ),
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
                agent_count, duration_ms, trace_id, observed_cost_usd,
                observed_tokens, trace_url, observability_status,
                operational_packet_json
            FROM runs
            {scope_filter}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    runs = [dict(row) for row in rows]
    for run in runs:
        packet = json.loads(run.pop("operational_packet_json") or "{}")
        run["runtime_status"] = packet.get("runtime_status", "")
        run["product_acceptance_status"] = packet.get("product_acceptance_status", "")
    return {"run_count": len(runs), "runs": runs}


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


def _friendly_decision_context(decision: dict[str, Any]) -> dict[str, Any]:
    reason = decision.get("reason", "")
    question = decision.get("question", "")
    previous_step = decision.get("previous_step") or {}
    combined = f"{reason} {question}".casefold()
    options = [
        {
            "resolution": "continue",
            "label": "Continuar com a recomendacao",
            "description": "Autorizar a squad a seguir pelo caminho recomendado abaixo.",
        },
        {
            "resolution": "request_revision",
            "label": "Pedir revisao",
            "description": "Solicitar novo ciclo com ajuste de escopo, PRD, evidencias ou artefato.",
        },
        {
            "resolution": "close",
            "label": "Encerrar",
            "description": "Parar esta demanda sem nova execucao.",
        },
    ]
    context = {
        "decision_title": "Decidir continuidade da demanda",
        "what_happened": "A squad pediu uma decisao humana antes de produzir efeitos.",
        "decision_needed": question or "Escolha se a squad deve continuar, revisar ou encerrar.",
        "squad_recommendation": decision.get("recommendation", ""),
        "recommended_resolution": "continue",
        "human_options": options,
        "technical_reason": reason,
    }
    if previous_step:
        source = previous_step.get("source_agent") or previous_step.get("agent_name") or "Agente anterior"
        target = previous_step.get("target_agent") or "CoS / Orchestrator"
        artifact = previous_step.get("artifact") or previous_step.get("artifact_type") or "artefato"
        context["what_happened"] = (
            f"O ultimo passo registrado foi {source} -> {target}, com o artefato {artifact}. "
            "Depois disso a squad pediu decisao humana antes de continuar."
        )
        if question.casefold().startswith("definir o proximo passo"):
            next_step = previous_step.get("next_step") or previous_step.get("blockers") or ""
            context["decision_needed"] = (
                next_step
                or "Escolha se a squad deve continuar a partir do passo anterior, revisar o escopo ou encerrar."
            )
    if "return_to_product" in combined or "route_to_product" in combined:
        context.update(
            {
                "decision_title": "Autorizar revisao de Produto",
                "what_happened": (
                    "O CoS entendeu que o escopo ainda nao esta pronto para Engenharia/QA. "
                    "Produto precisa transformar a PRD/contexto em criterios testaveis."
                ),
                "decision_needed": (
                    "Escolha se a squad deve voltar para Produto com os insumos atuais, "
                    "se voce quer complementar a PRD antes, ou se prefere encerrar este ciclo."
                ),
                "squad_recommendation": (
                    "Recomendo pedir revisao de Produto informando exatamente o insumo que faltou "
                    "ou iniciar uma nova run de entrega completa com a PRD correta selecionada."
                ),
                "recommended_resolution": "request_revision",
            }
        )
    elif "no_go" in combined:
        validation_gap = "validacao" in combined or "validação" in combined
        context.update(
            {
                "decision_title": "Decidir se redesenha ou encerra",
                "what_happened": (
                    "A squad emitiu NO-GO: neste ciclo ela nao encontrou evidencias suficientes "
                    "para recomendar continuidade."
                ),
                "decision_needed": (
                    "Escolha entre redesenhar o ciclo com mais insumos/evidencias ou encerrar esta demanda."
                ),
                "squad_recommendation": (
                    "Recomendo pedir revisao se o problema continua valido. Encerre apenas se o escopo "
                    "nao deve mais seguir."
                ),
                "recommended_resolution": "request_revision",
            }
        )
        if validation_gap:
            context["what_happened"] = (
                "A squad emitiu NO-GO porque nao havia evidencia de validacao executada. "
                "QA consegue validar tecnicamente quando existe artefato executavel, patch aplicado "
                "ou comandos de teste definidos; nesta etapa ele nao tinha esse material para testar sozinho."
            )
            context["decision_needed"] = (
                "Decida se a squad deve voltar para implementar/preparar um artefato validavel, "
                "se voce vai anexar evidencia de validacao, ou se este ciclo deve ser encerrado."
            )
            context["squad_recommendation"] = (
                "Recomendo pedir revisao para gerar ou apontar o artefato executavel e depois rodar QA "
                "com validacoes objetivas."
            )
    elif "retry" in combined or "limite" in combined:
        context.update(
            {
                "decision_title": "Resolver limite de tentativas",
                "what_happened": "A squad atingiu o limite de retorno automatico entre agentes.",
                "decision_needed": (
                    "Escolha se autoriza mais um ciclo de revisao, muda o escopo ou encerra a demanda."
                ),
                "squad_recommendation": "Recomendo revisar o escopo antes de autorizar nova tentativa.",
                "recommended_resolution": "request_revision",
            }
        )
    return context


def _decision_scope_key(decision: dict[str, Any]) -> str:
    project_id = (decision.get("project_id") or "").strip()
    initiative_id = (decision.get("initiative_id") or "").strip()
    if project_id or initiative_id:
        return f"{project_id or 'default'}::{initiative_id or 'default'}"
    return f"run::{decision.get('run_id', '')}"


def _apply_decision_governance(decisions: list[dict[str, Any]]) -> dict[str, int]:
    pending_by_scope: dict[str, list[dict[str, Any]]] = {}
    for decision in decisions:
        scope_key = _decision_scope_key(decision)
        unscoped_legacy = not (decision.get("project_id") or "").strip() and not (
            decision.get("initiative_id") or ""
        ).strip()
        decision["scope_key"] = scope_key
        decision["is_actionable"] = decision["status"] == "pending" and not unscoped_legacy
        decision["blocked_by_decision_id"] = ""
        decision["blocked_by_title"] = ""
        if decision["status"] != "pending":
            decision["governance_status"] = "historical"
            decision["governance_reason"] = "Esta decisao ja foi respondida e fica apenas como historico."
        elif unscoped_legacy:
            decision["governance_status"] = "legacy"
            decision["governance_reason"] = (
                "Esta decisao pertence a uma execucao antiga sem projeto/iniciativa. "
                "Ela fica visivel para auditoria, mas nao entra como decisao acionavel do projeto atual."
            )
        else:
            decision["governance_status"] = "actionable"
            decision["governance_reason"] = "Pode ser decidida agora."

        if decision["status"] == "pending" and not unscoped_legacy:
            pending_by_scope.setdefault(scope_key, []).append(decision)

    for scoped_pending in pending_by_scope.values():
        if len(scoped_pending) <= 1:
            continue
        scoped_pending.sort(key=lambda item: (item.get("created_at", ""), item.get("updated_at", "")))
        primary = scoped_pending[0]
        primary["governance_reason"] = (
            "Esta e a decisao bloqueante mais antiga deste projeto/iniciativa. "
            "Resolva primeiro para preservar a ordem do fluxo."
        )
        for blocked in scoped_pending[1:]:
            blocked["is_actionable"] = False
            blocked["blocked_by_decision_id"] = primary["decision_id"]
            blocked["blocked_by_title"] = primary.get("decision_title", "decisao anterior")
            blocked["governance_status"] = "blocked"
            blocked["governance_reason"] = (
                "Existe uma decisao anterior aberta no mesmo projeto/iniciativa. "
                "Esta decisao pode mudar ou perder sentido depois da resposta anterior."
            )

    actionable = sum(1 for item in decisions if item["is_actionable"])
    blocked = sum(1 for item in decisions if item["governance_status"] == "blocked")
    legacy = sum(1 for item in decisions if item["governance_status"] == "legacy")
    pending = sum(1 for item in decisions if item["status"] == "pending")
    return {
        "actionable_pending_count": actionable,
        "blocked_pending_count": blocked,
        "legacy_pending_count": legacy,
        "pending_count": pending,
    }


def _decision_previous_step_snapshot(
    connection: sqlite3.Connection,
    run_ids: list[str],
) -> dict[str, dict[str, Any]]:
    if not run_ids:
        return {}
    placeholders = ",".join("?" for _ in run_ids)
    handoff_rows = connection.execute(
        f"""
        SELECT run_id, occurred_at, source_agent, target_agent, artifact,
               summary, ece, blockers, next_step
        FROM handoff_events
        WHERE run_id IN ({placeholders})
        ORDER BY run_id, event_id ASC
        """,
        run_ids,
    ).fetchall()
    previous_steps: dict[str, dict[str, Any]] = {}
    human_fallbacks: dict[str, dict[str, Any]] = {}
    for row in handoff_rows:
        item = dict(row)
        if item["target_agent"] == "Human Decision Maker":
            human_fallbacks[item["run_id"]] = item
            continue
        previous_steps[item["run_id"]] = item
    for run_id, item in human_fallbacks.items():
        previous_steps.setdefault(run_id, item)

    missing_run_ids = [run_id for run_id in run_ids if run_id not in previous_steps]
    if missing_run_ids:
        output_placeholders = ",".join("?" for _ in missing_run_ids)
        output_rows = connection.execute(
            f"""
            SELECT run_id, agent_name, ece, artifact_type, execution_ready
            FROM agent_outputs
            WHERE run_id IN ({output_placeholders})
            ORDER BY run_id, rowid ASC
            """,
            missing_run_ids,
        ).fetchall()
        for row in output_rows:
            previous_steps[row["run_id"]] = dict(row)
    return previous_steps


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
        all_run_ids = [row["run_id"] for row in rows] + [row["run_id"] for row in legacy_rows]
        previous_steps = _decision_previous_step_snapshot(connection, all_run_ids)
    decisions = [
        dict(row) for row in rows
        if include_validation or not _is_validation_initiative(row["initiative_id"])
    ]
    for decision in decisions:
        decision["previous_step"] = previous_steps.get(decision["run_id"], {})
    for row in legacy_rows:
        if not include_validation and _is_validation_initiative(row["initiative_id"]):
            continue
        packet = json.loads(row["operational_packet_json"] or "{}")
        legacy_decision = {
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
        legacy_decision["previous_step"] = previous_steps.get(row["run_id"], {})
        decisions.append(legacy_decision)
    for decision in decisions:
        decision.update(_friendly_decision_context(decision))
    governance = _apply_decision_governance(decisions)
    decisions.sort(key=lambda item: (item["status"] == "pending", item["updated_at"]), reverse=True)
    return {
        "decision_count": len(decisions),
        "pending_count": governance["pending_count"],
        "actionable_pending_count": governance["actionable_pending_count"],
        "blocked_pending_count": governance["blocked_pending_count"],
        "legacy_pending_count": governance["legacy_pending_count"],
        "decisions": decisions,
    }


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
            "SELECT run_id, status, question FROM human_decisions WHERE decision_id=?",
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
            row = {"run_id": legacy["run_id"], "status": "pending", "question": packet.get("human_checkpoint") or ""}
        if row["status"] != "pending":
            raise ValueError("A decisao humana ja foi respondida.")

        if resolution == "continue" and "TELEGRAM_TOKEN" in str(row["question"]):
            run = connection.execute(
                "SELECT operational_packet_json FROM runs WHERE run_id=?",
                (row["run_id"],),
            ).fetchone()
            packet = json.loads(run["operational_packet_json"] or "{}") if run else {}
            workspace_root = str(packet.get("workspace_root", "")).strip()
            if not workspace_root:
                for item in packet.get("external_inputs", []):
                    workspace_root = str(item.get("workspace_root", "")).strip()
                    if workspace_root:
                        break
            env_path = Path(workspace_root) / ".env" if workspace_root else None
            env_example_path = Path(workspace_root) / ".env.example" if workspace_root else None
            token_ready = False
            if env_path and env_path.exists():
                token_ready = bool(
                    re.search(
                        r"^TELEGRAM_TOKEN=\S+",
                        env_path.read_text(encoding="utf-8-sig", errors="replace"),
                        re.MULTILINE,
                    )
                )
            if not token_ready:
                example_has_token = False
                if env_example_path and env_example_path.exists():
                    example_has_token = bool(
                        re.search(
                            r"^TELEGRAM_TOKEN=\S+",
                            env_example_path.read_text(encoding="utf-8-sig", errors="replace"),
                            re.MULTILINE,
                        )
                    )
                raise ValueError(
                    (
                        "A chave foi preenchida em .env.example. Esse arquivo e apenas um modelo e nao deve conter segredos. "
                        "Copie .env.example para .env, preencha TELEGRAM_TOKEN em .env e remova o valor de .env.example."
                        if example_has_token
                        else "Ainda falta configurar TELEGRAM_TOKEN no arquivo .env do workspace antes de continuar."
                    )
                )

    snapshot = human_decision_snapshot(db_path, include_validation=True)
    current = next((item for item in snapshot["decisions"] if item["decision_id"] == decision_id), None)
    if current and not current.get("is_actionable", True):
        blocked_by = current.get("blocked_by_title") or current.get("blocked_by_decision_id") or "decisao anterior"
        raise ValueError(f"Resolva primeiro a decisao bloqueante: {blocked_by}.")

    with _connection(db_path) as connection:
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
    if resolution == "continue" and "TELEGRAM_TOKEN" in str(row["question"]):
        record_workflow_checkpoint(
            thread_id=row["run_id"],
            run_id=row["run_id"],
            stage="external_input_confirmed",
            status="ready_for_runtime_start",
            payload={"decision_id": decision_id, "input": "TELEGRAM_TOKEN"},
            db_path=db_path,
        )
        update_run_status(
            row["run_id"],
            status="return_requested",
            operational_packet={
                **packet,
                "post_apply_status": "ready_for_runtime_start",
                "product_acceptance_status": "functional_qa_pending",
            },
            db_path=db_path,
        )
    return next(
        item
        for item in human_decision_snapshot(db_path, include_validation=True)["decisions"]
        if item["decision_id"] == decision_id
    )


def record_functional_qa_result(
    run_id: str,
    *,
    passed: bool,
    evidence: str,
    decided_by: str = "owner",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    evidence = evidence.strip()
    if len(evidence) < 20:
        raise ValueError("Descreva a validacao funcional executada e o resultado observado.")
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        row = connection.execute(
            "SELECT operational_packet_json FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    if row is None:
        raise ValueError("Run nao encontrada.")
    packet = json.loads(row["operational_packet_json"] or "{}")
    qa_status = "functional_qa_passed" if passed else "functional_qa_failed"
    acceptance = "product_accepted" if passed else "functional_qa_failed"
    updated_packet = {
        **packet,
        "product_acceptance_status": acceptance,
        "functional_qa": {
            "status": qa_status,
            "evidence": evidence[:4000],
            "decided_by": decided_by.strip()[:120] or "owner",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    update_run_status(
        run_id,
        status="ended" if passed else "return_requested",
        operational_packet=updated_packet,
        db_path=db_path,
    )
    record_workflow_checkpoint(
        thread_id=run_id,
        run_id=run_id,
        stage=qa_status,
        status="product_accepted" if passed else "return_to_operator",
        payload={
            "evidence": evidence[:4000],
            "decided_by": decided_by.strip()[:120] or "owner",
        },
        db_path=db_path,
    )
    return {
        "run_id": run_id,
        "status": "ended" if passed else "return_requested",
        "product_acceptance_status": acceptance,
        "functional_qa": updated_packet["functional_qa"],
    }


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
                   execution_tier, status, cos_decision, route_decision,
                   trace_id, observed_cost_usd, observed_tokens, trace_url,
                   observability_status, observability_error, observability_synced_at,
                   operational_packet_json
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
        handoff_rows = connection.execute(
            """
            SELECT event_id, occurred_at, source_agent, target_agent, artifact,
                   summary, ece, blockers, next_step, escalate_to_cos
            FROM handoff_events WHERE run_id=? ORDER BY event_id ASC
            """,
            (run_id,),
        ).fetchall()
    agents = [dict(row) for row in rows]
    handoffs = [dict(row) for row in handoff_rows]
    if handoffs:
        links = [
            {"from": item["source_agent"], "to": item["target_agent"], "artifact": item["artifact"]}
            for item in handoffs
        ]
        link_source = "recorded_handoffs"
    else:
        links = [
            {"from": agents[index]["agent_name"], "to": agents[index + 1]["agent_name"]}
            for index in range(len(agents) - 1)
        ]
        link_source = "legacy_output_sequence"
    run_snapshot = dict(run) if run else None
    if run_snapshot:
        run_snapshot["operational_packet"] = json.loads(run_snapshot.pop("operational_packet_json") or "{}")
    return {
        "run": run_snapshot,
        "agents": agents,
        "handoffs": handoffs,
        "links": links,
        "link_source": link_source,
    }


def trace_sync_candidates(
    *,
    run_id: str | None = None,
    limit: int = 100,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[dict[str, str]]:
    initialize_schema(db_path)
    safe_limit = max(1, min(limit, 100))
    with _connection(db_path) as connection:
        if run_id:
            rows = connection.execute(
                "SELECT run_id, trace_id FROM runs WHERE run_id=? AND trace_id!=''",
                (run_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT run_id, trace_id FROM runs
                WHERE trace_id!=''
                ORDER BY created_at DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def update_run_observability(
    run_id: str,
    observation: dict[str, Any],
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        connection.execute(
            """
            UPDATE runs SET observed_cost_usd=?, observed_tokens=?, trace_url=?,
                observability_status=?, observability_error=?, observability_synced_at=?
            WHERE run_id=?
            """,
            (
                observation.get("observed_cost_usd"),
                observation.get("observed_tokens"),
                str(observation.get("trace_url", ""))[:1000],
                str(observation.get("status", "failed"))[:40],
                str(observation.get("error", ""))[:500],
                datetime.now(timezone.utc).isoformat(),
                run_id,
            ),
        )


def cost_snapshot(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT project_id, initiative_id, execution_tier, execution_policy_json,
                   status, agent_count, trace_id, observed_cost_usd, observed_tokens,
                   observability_status
            FROM runs ORDER BY created_at DESC
            """
        ).fetchall()
    by_tier: dict[str, dict[str, Any]] = {}
    by_project: dict[str, dict[str, Any]] = {}
    total_budget = 0.0
    validation_budget = 0.0
    validation_runs = 0
    observed_cost = 0.0
    observed_tokens = 0
    traced_runs = 0
    synced_runs = 0
    for row in rows:
        policy = json.loads(row["execution_policy_json"] or "{}")
        estimated = float(policy.get("max_cost_usd") or 0)
        total_budget += estimated
        if _is_validation_initiative(row["initiative_id"]):
            validation_budget += estimated
            validation_runs += 1
        if row["trace_id"]:
            traced_runs += 1
        if row["observability_status"] == "synced":
            synced_runs += 1
            observed_cost += float(row["observed_cost_usd"] or 0)
            observed_tokens += int(row["observed_tokens"] or 0)
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
        "observed_cost_usd": round(observed_cost, 6),
        "observed_tokens": observed_tokens,
        "traced_runs": traced_runs,
        "synced_runs": synced_runs,
        "real_cost_available": synced_runs > 0,
        "tracing_enabled": os.getenv("LANGSMITH_TRACING", "").lower() == "true",
        "sampling_rate": sampling_rate,
        "by_tier": sorted(by_tier.values(), key=lambda item: item["estimated_budget_usd"], reverse=True),
        "by_project": sorted(by_project.values(), key=lambda item: item["estimated_budget_usd"], reverse=True)[:8],
    }


def board_snapshot(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    runs = recent_runs_snapshot(limit=100, include_validation=False, db_path=db_path)["runs"]
    requests = execution_request_snapshot(db_path=db_path)["requests"]
    demands = automation_demand_snapshot(db_path)["demands"]
    decision_snapshot = human_decision_snapshot(db_path)
    pending_decisions = {
        item["run_id"] for item in decision_snapshot["decisions"] if item.get("is_actionable")
    }
    blocked_decisions = {
        item["run_id"]
        for item in decision_snapshot["decisions"]
        if item.get("governance_status") in {"blocked", "legacy"}
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
        elif run["run_id"] in blocked_decisions:
            column = "blocked"
        elif run.get("product_acceptance_status") == "functional_qa_pending":
            column = "in_progress"
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
            delivery = connection.execute(
                "SELECT * FROM git_deliveries WHERE request_id=?",
                (request["request_id"],),
            ).fetchone()
            request["git_delivery"] = dict(delivery) if delivery else None
            if request["git_delivery"]:
                request["git_delivery"]["evidence"] = json.loads(
                    request["git_delivery"].pop("evidence_json")
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


def record_executor_run(
    *,
    run_id: str = "",
    agent_name: str = "",
    executor_used: str,
    success: bool,
    files_changed: list[str] | None = None,
    tokens_used: int | None = None,
    partial: bool = False,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    initialize_schema(db_path)
    now = datetime.now(timezone.utc).isoformat()
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO executor_runs
                (run_id, agent_name, executor_used, success, files_changed_json,
                 tokens_used, partial, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                agent_name,
                executor_used,
                1 if success else 0,
                json.dumps(files_changed or []),
                tokens_used,
                1 if partial else 0,
                now,
            ),
        )


def executor_runs_snapshot(
    run_id: str | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        if run_id:
            rows = connection.execute(
                """
                SELECT id, run_id, agent_name, executor_used, success,
                       files_changed_json, tokens_used, partial, created_at
                FROM executor_runs
                WHERE run_id = ?
                ORDER BY id ASC
                """,
                (run_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT id, run_id, agent_name, executor_used, success,
                       files_changed_json, tokens_used, partial, created_at
                FROM executor_runs
                ORDER BY id DESC LIMIT 200
                """
            ).fetchall()
    return {
        "executor_runs": [
            {
                "id": row["id"],
                "run_id": row["run_id"],
                "agent_name": row["agent_name"],
                "executor_used": row["executor_used"],
                "success": bool(row["success"]),
                "files_changed": json.loads(row["files_changed_json"]),
                "tokens_used": row["tokens_used"],
                "partial": bool(row["partial"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    }


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
        repair_rows = connection.execute(
            """
            SELECT
                ao.agent_name,
                COUNT(*) AS total_outputs,
                COALESCE(SUM(ao.repair_attempted), 0) AS repair_count
            FROM agent_outputs ao
            JOIN runs r ON r.run_id = ao.run_id
            GROUP BY ao.agent_name
            ORDER BY repair_count DESC, ao.agent_name ASC
            """
        ).fetchall()
        executor_totals = connection.execute(
            """
            SELECT
                COUNT(*) AS total_executions,
                COALESCE(SUM(success), 0) AS success_count,
                COALESCE(SUM(tokens_used), 0) AS total_tokens_used
            FROM executor_runs
            """
        ).fetchone()
        executor_top_row = connection.execute(
            """
            SELECT executor_used, COUNT(*) AS cnt
            FROM executor_runs
            WHERE executor_used != ''
            GROUP BY executor_used
            ORDER BY cnt DESC
            LIMIT 1
            """
        ).fetchone()

    run_count = totals["run_count"]
    exec_total = executor_totals["total_executions"] or 0
    most_used_executor = executor_top_row["executor_used"] if executor_top_row else ""
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
        "repair_rates": [
            {
                "agent_name": row["agent_name"],
                "total_outputs": row["total_outputs"],
                "repair_count": row["repair_count"],
                "repair_rate": round(row["repair_count"] / row["total_outputs"], 4) if row["total_outputs"] else 0.0,
            }
            for row in repair_rows
        ],
        "executor_summary": {
            "total_executions": exec_total,
            "success_rate": round(executor_totals["success_count"] / exec_total, 4) if exec_total else 0.0,
            "most_used_executor": most_used_executor,
            "total_tokens_used": executor_totals["total_tokens_used"] or 0,
        },
    }


def performance_history_snapshot(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        daily_rows = connection.execute(
            """
            SELECT substr(created_at, 1, 10) AS day,
                   COUNT(*) AS run_count,
                   COALESCE(AVG(duration_ms), 0) AS average_duration_ms,
                   COALESCE(SUM(human_escalation_created), 0) AS escalation_count,
                   COALESCE(SUM(observed_cost_usd), 0) AS observed_cost_usd,
                   SUM(CASE WHEN observability_status='synced' THEN 1 ELSE 0 END) AS synced_runs
            FROM runs
            WHERE initiative_id NOT LIKE 'eval-%' AND initiative_id NOT LIKE 'baseline-%'
            GROUP BY substr(created_at, 1, 10)
            ORDER BY day DESC LIMIT 14
            """
        ).fetchall()
        evaluation_rows = connection.execute(
            """
            SELECT evaluation_id, MAX(executed_at) AS executed_at,
                   COUNT(*) AS scenario_count,
                   ROUND(AVG(score), 2) AS average_score,
                   SUM(passed) AS passed_count
            FROM eval_results
            GROUP BY evaluation_id
            ORDER BY executed_at DESC LIMIT 12
            """
        ).fetchall()
        baseline_rows = connection.execute(
            """
            SELECT baseline_id, MAX(executed_at) AS executed_at,
                   COUNT(*) AS scenario_count,
                   ROUND(AVG(CASE WHEN execution_status='completed' THEN automatic_score END), 2)
                     AS automatic_average_score,
                   ROUND(AVG(
                     CASE WHEN human_correctness IS NOT NULL THEN
                       (human_correctness + human_practical_utility + human_scope_control
                        + human_next_step_clarity + human_execution_confidence) / 5.0
                     END
                   ), 2) AS human_average_score
            FROM baseline_reviews
            GROUP BY baseline_id
            ORDER BY executed_at DESC LIMIT 12
            """
        ).fetchall()
    daily = [
        {
            "day": row["day"],
            "run_count": row["run_count"],
            "average_duration_ms": round(row["average_duration_ms"] or 0, 2),
            "escalation_count": row["escalation_count"],
            "observed_cost_usd": round(row["observed_cost_usd"] or 0, 6),
            "synced_runs": row["synced_runs"],
        }
        for row in daily_rows
    ]
    evaluations = [dict(row) for row in evaluation_rows]
    baselines = [dict(row) for row in baseline_rows]
    return {
        "daily_runs": daily,
        "evaluations": evaluations,
        "baselines": baselines,
        "quality_target": 8.7,
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
