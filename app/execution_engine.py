"""Execution request lifecycle with an explicit human approval gate."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess
from typing import Any

from app.operational_store import (
    DEFAULT_DB_PATH,
    execution_request_snapshot,
    record_apply_decision,
    record_execution_decision,
    record_execution_preparation,
    record_execution_request,
)


EXECUTION_MODE = "dry_run_only"
PENDING_APPROVAL = "pending_approval"
NOT_ACTIONABLE = "not_actionable"
APPROVED_FOR_DRY_RUN = "approved_for_dry_run"
AWAITING_PATCH = "awaiting_patch"
AWAITING_APPLY_APPROVAL = "awaiting_apply_approval"
MAX_PATCH_BYTES = 1_000_000


def _normalize_relative_ref(ref: str) -> str | None:
    normalized = ref.replace("\\", "/").removeprefix("./")
    if not normalized or normalized.startswith("/") or ":" in normalized:
        return None
    if ".." in Path(normalized).parts:
        return None
    return normalized


def _patch_target_refs(patch_text: str) -> list[str]:
    targets: list[str] = []
    patterns = (
        r"^(?:---|\+\+\+)\s+([^\t\r\n ]+)",
        r"^diff --git\s+([^\s]+)\s+([^\s]+)",
        r"^(?:rename|copy) (?:from|to)\s+(.+)$",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, patch_text, flags=re.MULTILINE):
            for raw_ref in match.groups():
                if raw_ref == "/dev/null":
                    continue
                relative_ref = raw_ref.removeprefix("a/").removeprefix("b/")
                normalized = _normalize_relative_ref(relative_ref)
                if normalized and normalized not in targets:
                    targets.append(normalized)
    return targets


def _workspace_target_evidence(
    target_refs: list[str],
    *,
    workspace_root: Path,
) -> tuple[list[dict[str, Any]], set[str]]:
    evidence: list[dict[str, Any]] = []
    allowed_refs: set[str] = set()
    root = workspace_root.resolve()
    for raw_ref in target_refs:
        normalized = _normalize_relative_ref(raw_ref)
        if normalized is None:
            evidence.append({"target_ref": raw_ref, "eligible_path": False, "exists": False})
            continue
        resolved = (root / normalized).resolve()
        in_workspace = resolved == root or root in resolved.parents
        if in_workspace:
            allowed_refs.add(normalized)
        evidence.append(
            {
                "target_ref": raw_ref,
                "normalized_ref": normalized,
                "eligible_path": in_workspace,
                "exists": resolved.exists() if in_workspace else False,
            }
        )
    return evidence, allowed_refs


def _validate_patch(
    patch_text: str,
    *,
    workspace_root: Path,
    allowed_refs: set[str],
) -> dict[str, Any]:
    patch_refs = _patch_target_refs(patch_text)
    errors: list[str] = []
    if len(patch_text.encode("utf-8")) > MAX_PATCH_BYTES:
        errors.append("Candidate patch exceeds the 1 MB preparation limit.")
    if "GIT binary patch" in patch_text:
        errors.append("Binary patches are not accepted by the controlled executor.")
    if not patch_refs:
        errors.append("No file targets were found in the unified diff.")
    unapproved_refs = sorted(set(patch_refs).difference(allowed_refs))
    if unapproved_refs:
        errors.append(f"Patch includes unapproved target refs: {', '.join(unapproved_refs)}.")
    git_check = {"executed": False, "passed": False, "details": ""}
    if not errors:
        try:
            completed = subprocess.run(
                ["git", "apply", "--check", "--recount", "-"],
                cwd=workspace_root,
                input=patch_text.encode("utf-8"),
                capture_output=True,
                check=False,
                timeout=10,
            )
            details = (completed.stderr or completed.stdout).decode(
                "utf-8", errors="replace"
            ).strip()
            git_check = {
                "executed": True,
                "passed": completed.returncode == 0,
                "details": details[:600],
            }
            if completed.returncode != 0:
                errors.append("Git rejected the candidate patch during apply --check.")
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
            errors.append(f"Unable to validate candidate patch with Git: {error}.")
    return {
        "passed": not errors,
        "patch_target_refs": patch_refs,
        "unapproved_target_refs": unapproved_refs,
        "git_apply_check": git_check,
        "errors": errors,
    }


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


def prepare_execution_request(
    request_id: str,
    *,
    patch_text: str = "",
    workspace_root: str | Path = ".",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    snapshot = execution_request_snapshot(request_id=request_id, db_path=db_path)
    if not snapshot["requests"]:
        raise ValueError("Execution request not found.")
    request = snapshot["requests"][0]
    if request["status"] not in {APPROVED_FOR_DRY_RUN, AWAITING_PATCH, AWAITING_APPLY_APPROVAL}:
        raise ValueError("Execution request must be approved before preparation.")
    root = Path(workspace_root).resolve()
    target_evidence, allowed_refs = _workspace_target_evidence(
        request.get("target_refs", []),
        workspace_root=root,
    )
    if patch_text:
        validation = _validate_patch(patch_text, workspace_root=root, allowed_refs=allowed_refs)
        preparation_status = "patch_validated" if validation["passed"] else "patch_rejected"
        request_status = AWAITING_APPLY_APPROVAL if validation["passed"] else AWAITING_PATCH
        status_reason = (
            "Candidate diff validated; human apply approval is required."
            if validation["passed"]
            else "Candidate diff rejected; submit a corrected scoped patch."
        )
    else:
        validation = {
            "passed": False,
            "patch_target_refs": [],
            "unapproved_target_refs": [],
            "git_apply_check": {"executed": False, "passed": False, "details": ""},
            "errors": ["A candidate unified diff is required before apply approval."],
        }
        preparation_status = AWAITING_PATCH
        request_status = AWAITING_PATCH
        status_reason = "Preparation approved; awaiting a scoped candidate diff."
    preparation = {
        "request_id": request_id,
        "request_status": request_status,
        "status_reason": status_reason,
        "preparation_status": preparation_status,
        "workspace_root": str(root),
        "patch_sha256": hashlib.sha256(patch_text.encode("utf-8")).hexdigest() if patch_text else "",
        "patch_text": patch_text,
        "target_evidence": target_evidence,
        "validation": validation,
    }
    return record_execution_preparation(preparation, db_path=db_path)


def decide_apply_execution(
    request_id: str,
    *,
    decision: str,
    decided_by: str,
    notes: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    return record_apply_decision(
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
