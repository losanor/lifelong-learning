"""Command-line approval inbox for dry-run execution requests."""

from __future__ import annotations

import argparse
import json

from pathlib import Path

from app.execution_engine import (
    apply_execution_request,
    decide_apply_execution,
    decide_execution_request,
    list_execution_requests,
    prepare_execution_request,
    rollback_execution_request,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Review execution requests.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List execution requests.")
    prepare = subparsers.add_parser("prepare", help="Validate an approved candidate diff.")
    prepare.add_argument("request_id")
    prepare.add_argument("--patch-file", required=True)
    prepare.add_argument("--workspace-root", default=".")
    apply_command = subparsers.add_parser("apply", help="Apply an approved patch and validate it.")
    apply_command.add_argument("request_id")
    apply_command.add_argument("--workspace-root", default=".")
    apply_command.add_argument(
        "--validate",
        action="append",
        dest="validations",
        choices=("git_diff_check", "python_compile", "unit_tests"),
    )
    rollback = subparsers.add_parser("rollback", help="Roll back an applied validated patch.")
    rollback.add_argument("request_id")
    rollback.add_argument("--workspace-root", default=".")
    rollback.add_argument("--reason", default="Human requested rollback.")

    for decision in ("approve", "reject", "approve-apply", "reject-apply"):
        command = subparsers.add_parser(decision, help=f"{decision.title()} one request.")
        command.add_argument("request_id")
        command.add_argument("--by", required=True, dest="decided_by")
        command.add_argument("--notes", default="")

    args = parser.parse_args()
    if args.command == "list":
        result = list_execution_requests()
    elif args.command == "prepare":
        patch_text = Path(args.patch_file).read_text(encoding="utf-8")
        result = prepare_execution_request(
            args.request_id,
            patch_text=patch_text,
            workspace_root=args.workspace_root,
        )
    elif args.command == "apply":
        result = apply_execution_request(
            args.request_id,
            workspace_root=args.workspace_root,
            validations=args.validations,
        )
    elif args.command == "rollback":
        result = rollback_execution_request(
            args.request_id,
            workspace_root=args.workspace_root,
            reason=args.reason,
        )
    elif args.command in {"approve-apply", "reject-apply"}:
        result = decide_apply_execution(
            args.request_id,
            decision="approved" if args.command == "approve-apply" else "rejected",
            decided_by=args.decided_by,
            notes=args.notes,
        )
    else:
        result = decide_execution_request(
            args.request_id,
            decision="approved" if args.command == "approve" else "rejected",
            decided_by=args.decided_by,
            notes=args.notes,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
