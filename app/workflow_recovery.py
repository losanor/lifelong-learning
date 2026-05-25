"""Recovery views for durable human-in-the-loop execution workflows."""

from __future__ import annotations

import argparse
import json

from app.operational_store import (
    execution_request_snapshot,
    pending_work_snapshot,
    workflow_checkpoint_snapshot,
)


def resume_snapshot(request_id: str) -> dict:
    snapshot = execution_request_snapshot(request_id=request_id)
    if not snapshot["requests"]:
        raise ValueError("Execution request not found.")
    request = snapshot["requests"][0]
    timeline = workflow_checkpoint_snapshot(thread_id=request["run_id"])
    return {
        "thread_id": request["run_id"],
        "request": request,
        "timeline": timeline["checkpoints"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover durable HITL workflows.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("pending", help="List work awaiting human or executor action.")
    resume = subparsers.add_parser("resume", help="Inspect one resumable execution thread.")
    resume.add_argument("request_id")
    args = parser.parse_args()
    result = pending_work_snapshot() if args.command == "pending" else resume_snapshot(args.request_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
