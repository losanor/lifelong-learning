"""Command-line approval inbox for dry-run execution requests."""

from __future__ import annotations

import argparse
import json

from app.execution_engine import decide_execution_request, list_execution_requests


def main() -> None:
    parser = argparse.ArgumentParser(description="Review execution requests.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List execution requests.")

    for decision in ("approve", "reject"):
        command = subparsers.add_parser(decision, help=f"{decision.title()} one request.")
        command.add_argument("request_id")
        command.add_argument("--by", required=True, dest="decided_by")
        command.add_argument("--notes", default="")

    args = parser.parse_args()
    if args.command == "list":
        result = list_execution_requests()
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
