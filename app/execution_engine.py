"""Execution request lifecycle with an explicit human approval gate."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from app.operational_store import (
    DEFAULT_DB_PATH,
    execution_request_snapshot,
    record_apply_decision,
    record_execution_decision,
    record_execution_application,
    record_git_delivery,
    record_execution_preparation,
    record_execution_request,
    record_workflow_checkpoint,
)


EXECUTION_MODE = "dry_run_only"
PENDING_APPROVAL = "pending_approval"
NOT_ACTIONABLE = "not_actionable"
APPROVED_FOR_DRY_RUN = "approved_for_dry_run"
AWAITING_PATCH = "awaiting_patch"
AWAITING_APPLY_APPROVAL = "awaiting_apply_approval"
APPROVED_FOR_APPLY = "approved_for_apply"
APPLIED_VALIDATED = "applied_validated"
GIT_COMMITTED = "git_committed"
PR_OPENED = "pr_opened"
ROLLED_BACK = "rolled_back"
MAX_PATCH_BYTES = 1_000_000
VALIDATION_PRESETS = {
    "git_diff_check": ["git", "diff", "--check"],
    "python_compile": [sys.executable, "-m", "compileall", "app", "tests"],
    "unit_tests": [sys.executable, "-m", "unittest", "tests.test_operational_contracts", "-v"],
}


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
    record_workflow_checkpoint(
        thread_id=run_id,
        run_id=run_id,
        stage="execution_request_created",
        status=request["status"],
        payload={"request_id": request["request_id"], "target_refs": request["target_refs"]},
        db_path=db_path,
    )
    return request


def decide_execution_request(
    request_id: str,
    *,
    decision: str,
    decided_by: str,
    notes: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    decided = record_execution_decision(
        request_id,
        decision=decision,
        decided_by=decided_by,
        notes=notes,
        db_path=db_path,
    )
    record_workflow_checkpoint(
        thread_id=decided["run_id"],
        run_id=decided["run_id"],
        stage="preparation_approval_decided",
        status=decided["status"],
        payload={"request_id": request_id, "decision": decision, "decided_by": decided_by},
        db_path=db_path,
    )
    return decided


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
    prepared = record_execution_preparation(preparation, db_path=db_path)
    record_workflow_checkpoint(
        thread_id=prepared["run_id"],
        run_id=prepared["run_id"],
        stage="candidate_patch_prepared",
        status=prepared["status"],
        payload={
            "request_id": request_id,
            "preparation_status": preparation_status,
            "patch_sha256": preparation["patch_sha256"],
            "validation": validation,
        },
        db_path=db_path,
    )
    return prepared


def decide_apply_execution(
    request_id: str,
    *,
    decision: str,
    decided_by: str,
    notes: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    decided = record_apply_decision(
        request_id,
        decision=decision,
        decided_by=decided_by,
        notes=notes,
        db_path=db_path,
    )
    record_workflow_checkpoint(
        thread_id=decided["run_id"],
        run_id=decided["run_id"],
        stage="apply_approval_decided",
        status=decided["status"],
        payload={"request_id": request_id, "decision": decision, "decided_by": decided_by},
        db_path=db_path,
    )
    return decided


def _run_command(command: list[str], *, workspace_root: Path, timeout: int = 120) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=workspace_root,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
        output = (completed.stdout + completed.stderr).strip()
        return {
            "command": command,
            "passed": completed.returncode == 0,
            "return_code": completed.returncode,
            "output": output[-2000:],
        }
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        return {
            "command": command,
            "passed": False,
            "return_code": None,
            "output": str(error),
        }


def _apply_patch_bytes(
    patch_text: str,
    *,
    workspace_root: Path,
    reverse: bool = False,
) -> dict[str, Any]:
    command = ["git", "apply"]
    if reverse:
        command.append("--reverse")
    command.extend(["--recount", "-"])
    try:
        completed = subprocess.run(
            command,
            cwd=workspace_root,
            input=patch_text.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=20,
        )
        output = (completed.stderr or completed.stdout).decode(
            "utf-8", errors="replace"
        ).strip()
        return {"passed": completed.returncode == 0, "details": output[:1000]}
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        return {"passed": False, "details": str(error)}


def _record_application_with_checkpoint(
    application: dict[str, Any],
    *,
    stage: str,
    db_path: str | Path,
) -> dict[str, Any]:
    recorded = record_execution_application(application, db_path=db_path)
    record_workflow_checkpoint(
        thread_id=recorded["run_id"],
        run_id=recorded["run_id"],
        stage=stage,
        status=recorded["status"],
        payload={
            "request_id": application["request_id"],
            "application_status": application["application_status"],
            "validation": application.get("validation", {}),
            "rollback_reason": application.get("rollback_reason", ""),
        },
        db_path=db_path,
    )
    return recorded


def apply_execution_request(
    request_id: str,
    *,
    workspace_root: str | Path = ".",
    validations: list[str] | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    snapshot = execution_request_snapshot(request_id=request_id, db_path=db_path)
    if not snapshot["requests"]:
        raise ValueError("Execution request not found.")
    request = snapshot["requests"][0]
    if request["status"] != APPROVED_FOR_APPLY:
        raise ValueError("A validated patch requires apply approval before execution.")
    preparation = request.get("preparation")
    if not preparation or not preparation.get("validation", {}).get("passed"):
        raise ValueError("No validated candidate patch is available.")
    root = Path(workspace_root).resolve()
    if str(root) != preparation["workspace_root"]:
        raise ValueError("Workspace root differs from the prepared execution workspace.")
    patch_text = preparation["patch_text"]
    patch_digest = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
    if patch_digest != preparation["patch_sha256"]:
        raise ValueError("Candidate patch digest does not match the approved preparation.")
    target_evidence, allowed_refs = _workspace_target_evidence(
        request["target_refs"], workspace_root=root
    )
    revalidation = _validate_patch(patch_text, workspace_root=root, allowed_refs=allowed_refs)
    if not revalidation["passed"]:
        return _record_application_with_checkpoint(
            {
                "request_id": request_id,
                "request_status": AWAITING_PATCH,
                "status_reason": "Approved patch is no longer applicable; submit a new candidate diff.",
                "application_status": "pre_apply_validation_failed",
                "workspace_root": str(root),
                "patch_sha256": patch_digest,
                "effects_enabled": False,
                "validation": {"pre_apply": revalidation, "presets": []},
                "git_evidence": {"target_evidence": target_evidence},
            },
            stage="application_precheck_failed",
            db_path=db_path,
        )
    applied = _apply_patch_bytes(patch_text, workspace_root=root)
    if not applied["passed"]:
        return _record_application_with_checkpoint(
            {
                "request_id": request_id,
                "request_status": AWAITING_PATCH,
                "status_reason": "Patch application failed without recorded workspace effects.",
                "application_status": "apply_failed",
                "workspace_root": str(root),
                "patch_sha256": patch_digest,
                "effects_enabled": False,
                "validation": {"pre_apply": revalidation, "presets": []},
                "git_evidence": {"target_evidence": target_evidence, "apply": applied},
            },
            stage="application_failed",
            db_path=db_path,
        )
    requested_validations = validations or ["git_diff_check"]
    invalid_presets = sorted(set(requested_validations).difference(VALIDATION_PRESETS))
    if invalid_presets:
        rollback = _apply_patch_bytes(patch_text, workspace_root=root, reverse=True)
        raise ValueError(
            f"Validation presets are not allowed: {', '.join(invalid_presets)}. "
            f"Patch rollback passed={rollback['passed']}."
        )
    validation_results = [
        {
            "preset": preset,
            **_run_command(VALIDATION_PRESETS[preset], workspace_root=root),
        }
        for preset in requested_validations
    ]
    all_passed = all(result["passed"] for result in validation_results)
    git_evidence = {
        "target_evidence": target_evidence,
        "apply": applied,
        "diff_stat": _run_command(["git", "diff", "--stat"], workspace_root=root),
    }
    if not all_passed:
        rollback = _apply_patch_bytes(patch_text, workspace_root=root, reverse=True)
        return _record_application_with_checkpoint(
            {
                "request_id": request_id,
                "request_status": ROLLED_BACK,
                "status_reason": "Patch was rolled back after validation failure.",
                "application_status": "rolled_back_validation_failed",
                "workspace_root": str(root),
                "patch_sha256": patch_digest,
                "effects_enabled": False,
                "validation": {"pre_apply": revalidation, "presets": validation_results},
                "git_evidence": {**git_evidence, "rollback": rollback},
                "rollback_reason": "One or more approved validation presets failed.",
            },
            stage="application_rolled_back",
            db_path=db_path,
        )
    return _record_application_with_checkpoint(
        {
            "request_id": request_id,
            "request_status": APPLIED_VALIDATED,
            "status_reason": "Patch applied and validation evidence recorded; Git delivery awaits human action.",
            "application_status": "applied_validated",
            "workspace_root": str(root),
            "patch_sha256": patch_digest,
            "effects_enabled": True,
            "validation": {"pre_apply": revalidation, "presets": validation_results},
            "git_evidence": git_evidence,
        },
        stage="application_validated",
        db_path=db_path,
    )


def _validated_branch_name(value: str, request_id: str) -> str:
    branch_name = value.strip() or f"squad/delivery-{request_id.removeprefix('exec_')[:24]}"
    if (
        len(branch_name) > 120
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch_name)
        or ".." in branch_name
        or "//" in branch_name
        or branch_name.endswith(("/", "."))
    ):
        raise ValueError("Branch name is invalid for supervised Git delivery.")
    return branch_name


def commit_git_delivery(
    request_id: str,
    *,
    workspace_root: str | Path = ".",
    branch_name: str = "",
    commit_message: str = "",
    remote_name: str = "origin",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    snapshot = execution_request_snapshot(request_id=request_id, db_path=db_path)
    if not snapshot["requests"]:
        raise ValueError("Execution request not found.")
    request = snapshot["requests"][0]
    if request["status"] != APPLIED_VALIDATED:
        raise ValueError("Only an applied and validated delivery can be committed.")
    application = request.get("application") or {}
    root = Path(workspace_root).resolve()
    if str(root) != application.get("workspace_root"):
        raise ValueError("Workspace root differs from the applied execution workspace.")
    branch = _validated_branch_name(branch_name, request_id)
    message = commit_message.strip()[:240] or f"Deliver {request.get('initiative_id') or request_id}"
    base_result = _run_command(["git", "branch", "--show-current"], workspace_root=root)
    if not base_result["passed"]:
        raise ValueError("Unable to identify the current Git branch.")
    base_branch = base_result["output"].strip()
    changed_result = _run_command(
        ["git", "diff", "--name-only", "--", *request["target_refs"]],
        workspace_root=root,
    )
    if not changed_result["passed"] or not changed_result["output"].strip():
        raise ValueError("No approved target changes are available to commit.")
    switch = _run_command(["git", "switch", "-c", branch], workspace_root=root)
    if not switch["passed"]:
        raise ValueError(f"Unable to create delivery branch: {switch['output']}")
    add = _run_command(["git", "add", "--", *request["target_refs"]], workspace_root=root)
    if not add["passed"]:
        raise ValueError(f"Unable to stage approved targets: {add['output']}")
    commit = _run_command(["git", "commit", "-m", message, "--", *request["target_refs"]], workspace_root=root)
    if not commit["passed"]:
        raise ValueError(f"Unable to create delivery commit: {commit['output']}")
    sha = _run_command(["git", "rev-parse", "HEAD"], workspace_root=root)
    if not sha["passed"]:
        raise ValueError("Commit was created but its SHA could not be recorded.")
    recorded = record_git_delivery(
        {
            "request_id": request_id,
            "request_status": GIT_COMMITTED,
            "status_reason": "Branch and commit created; publication and draft PR require human confirmation.",
            "delivery_status": GIT_COMMITTED,
            "workspace_root": str(root),
            "branch_name": branch,
            "base_branch": base_branch,
            "commit_sha": sha["output"].strip(),
            "commit_message": message,
            "remote_name": remote_name.strip()[:80] or "origin",
            "evidence": {"changed_targets": changed_result, "switch": switch, "add": add, "commit": commit},
        },
        db_path=db_path,
    )
    record_workflow_checkpoint(
        thread_id=recorded["run_id"],
        run_id=recorded["run_id"],
        stage="git_commit_created",
        status=recorded["status"],
        payload={"request_id": request_id, "branch_name": branch, "commit_sha": sha["output"].strip()},
        db_path=db_path,
    )
    return recorded


def publish_git_delivery(
    request_id: str,
    *,
    workspace_root: str | Path = ".",
    pr_title: str = "",
    pr_body: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    snapshot = execution_request_snapshot(request_id=request_id, db_path=db_path)
    if not snapshot["requests"]:
        raise ValueError("Execution request not found.")
    request = snapshot["requests"][0]
    delivery = request.get("git_delivery")
    if request["status"] != GIT_COMMITTED or not delivery:
        raise ValueError("Create the supervised commit before publishing a draft PR.")
    root = Path(workspace_root).resolve()
    if str(root) != delivery["workspace_root"]:
        raise ValueError("Workspace root differs from the committed delivery workspace.")
    title = pr_title.strip()[:240] or delivery["commit_message"]
    body = pr_body.strip()[:8000] or "Entrega gerada pela squad e aguardando revisao humana."
    push = _run_command(
        ["git", "push", "-u", delivery["remote_name"], delivery["branch_name"]],
        workspace_root=root,
        timeout=180,
    )
    if not push["passed"]:
        raise ValueError(f"Unable to publish delivery branch: {push['output']}")
    pr = _run_command(
        [
            "gh", "pr", "create", "--draft", "--base", delivery["base_branch"],
            "--head", delivery["branch_name"], "--title", title, "--body", body,
        ],
        workspace_root=root,
        timeout=180,
    )
    if not pr["passed"]:
        raise ValueError(f"Branch published, but draft PR creation failed: {pr['output']}")
    match = re.search(r"https?://\S+", pr["output"])
    recorded = record_git_delivery(
        {
            "request_id": request_id,
            "request_status": PR_OPENED,
            "status_reason": "Draft PR published; merge remains a human decision.",
            "delivery_status": PR_OPENED,
            "workspace_root": str(root),
            "branch_name": delivery["branch_name"],
            "base_branch": delivery["base_branch"],
            "commit_sha": delivery["commit_sha"],
            "commit_message": delivery["commit_message"],
            "remote_name": delivery["remote_name"],
            "pr_url": match.group(0) if match else "",
            "evidence": {**delivery.get("evidence", {}), "push": push, "draft_pr": pr},
        },
        db_path=db_path,
    )
    record_workflow_checkpoint(
        thread_id=recorded["run_id"],
        run_id=recorded["run_id"],
        stage="draft_pr_published",
        status=recorded["status"],
        payload={"request_id": request_id, "pr_url": recorded["git_delivery"]["pr_url"]},
        db_path=db_path,
    )
    return recorded


def rollback_execution_request(
    request_id: str,
    *,
    workspace_root: str | Path = ".",
    reason: str = "Human requested rollback.",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    snapshot = execution_request_snapshot(request_id=request_id, db_path=db_path)
    if not snapshot["requests"]:
        raise ValueError("Execution request not found.")
    request = snapshot["requests"][0]
    if request["status"] != APPLIED_VALIDATED:
        raise ValueError("Only applied and validated executions can be rolled back.")
    preparation = request.get("preparation")
    root = Path(workspace_root).resolve()
    if not preparation or str(root) != preparation["workspace_root"]:
        raise ValueError("Workspace root differs from the applied execution workspace.")
    rollback = _apply_patch_bytes(preparation["patch_text"], workspace_root=root, reverse=True)
    if not rollback["passed"]:
        raise ValueError(f"Rollback could not be applied: {rollback['details']}")
    application = request.get("application", {})
    return _record_application_with_checkpoint(
        {
            "request_id": request_id,
            "request_status": ROLLED_BACK,
            "status_reason": "Applied patch rolled back by human request.",
            "application_status": "rolled_back",
            "workspace_root": str(root),
            "patch_sha256": preparation["patch_sha256"],
            "effects_enabled": False,
            "validation": application.get("validation", {}),
            "git_evidence": {**application.get("git_evidence", {}), "rollback": rollback},
            "rollback_reason": reason,
        },
        stage="application_rolled_back",
        db_path=db_path,
    )


def list_execution_requests(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    return execution_request_snapshot(db_path=db_path)
