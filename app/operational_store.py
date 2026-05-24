"""Persistencia SQLite para runs, artefatos e metricas operacionais."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "squad_runtime.sqlite3"


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
                user_goal TEXT NOT NULL,
                active_flow TEXT NOT NULL,
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
            """
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
    c3_count = sum(
        1 for output in outputs.values()
        if output.get("summary", {}).get("ece") == "C3"
    )
    packet = result.get("operational_packet", {})

    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO runs (
                run_id, created_at, user_goal, active_flow, status, cos_decision,
                route_action, route_decision, human_escalation_created,
                execution_ready, agent_count, c3_count, duration_ms,
                operational_packet_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                active_flow=excluded.active_flow,
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
                user_goal,
                result.get("active_flow", ""),
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
                    execution_ready, target_refs_json, output_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, agent_name) DO UPDATE SET
                    ece=excluded.ece,
                    schema_valid=excluded.schema_valid,
                    artifact_type=excluded.artifact_type,
                    execution_ready=excluded.execution_ready,
                    target_refs_json=excluded.target_refs_json,
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
                    json.dumps(output, ensure_ascii=False),
                ),
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


def metrics_snapshot(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    initialize_schema(db_path)
    with _connection(db_path) as connection:
        totals = connection.execute(
            """
            SELECT
                COUNT(*) AS run_count,
                COALESCE(AVG(agent_count), 0) AS average_agents_per_run,
                COALESCE(AVG(c3_count), 0) AS average_c3_per_run,
                COALESCE(AVG(duration_ms), 0) AS average_duration_ms,
                COALESCE(SUM(human_escalation_created), 0) AS escalation_count,
                COALESCE(SUM(execution_ready), 0) AS execution_ready_count
            FROM runs
            """
        ).fetchone()
        flow_rows = connection.execute(
            """
            SELECT active_flow, COUNT(*) AS run_count, AVG(agent_count) AS average_agents
            FROM runs
            GROUP BY active_flow
            ORDER BY run_count DESC, active_flow ASC
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
    }
