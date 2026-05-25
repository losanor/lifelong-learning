"""Execution request lifecycle with an explicit human approval gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.operational_store import (
    DEFAULT_DB_PATH,
    execution_request_snapshot,
    record_execution_decision,
    record_execution_request,
)


EXECUTION_MODE = "dry_run_only"
PENDING_APPROVAL = "pending_approval"
NOT_ACTIONABLE = "not_actionable"


def build_execution_request(
    result: dict[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    packet = result.get("operational_packet", {})
    policy = result.get("execution_policy", packet.get("execution_policy", {}))
    scope = result.get("work_scope", {})
    execution_ready = bool(packet.get("execution_ready", False))
    requested_effects: list[str] = []
    if execution_ready:
        requested_effects.append("write_files")
        if packet.get("git_actions"):
            requested_effects.append("git_commit")
    requires_approval = bool(requested_effects)
    return {
        "request_id": f"exec_{run_id}",
        "run_id": run_id,
        "project_id": scope.get("project_id", packet.get("project_id", "")),
        "initiative_id": scope.get("initiative_id", packet.get("initiative_id", "")),
        "status": PENDING_APPROVAL if execution_ready else NOT_ACTIONABLE,
        "execution_mode": EXECUTION_MODE,
        "approval_required": requires_approval,
        "requested_effects": requested_effects,
        "target_refs": packet.get("target_refs", []),
        "recommended_actions": packet.get("recommended_actions", []),
        "verification_steps": packet.get("verification_steps", []),
        "execution_policy": policy,
        "operational_packet": packet,
        "effects_enabled": False,
        "status_reason": (
            "Human approval is required before future workspace effects."
            if execution_ready
            else "The operational packet is not eligible for execution."
        ),
    }


def create_execution_request(
    result: dict[str, Any],
    *,
    run_id: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    request = build_execution_request(result, run_id=run_id)
    record_execution_request(request, db_path=db_path)
    return request


def decide_execution_request(
    request_id: str,
    *,
    decision: str,
    decided_by: str,
    notes: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    return record_execution_decision(
        request_id,
        decision=decision,
        decided_by=decided_by,
        notes=notes,
        db_path=db_path,
    )


def list_execution_requests(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    return execution_request_snapshot(db_path=db_path)
