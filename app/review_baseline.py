"""Registra a rubrica humana de uma execucao baseline."""

from __future__ import annotations

import argparse
import json

from app.operational_store import baseline_snapshot, update_human_review


def main() -> None:
    parser = argparse.ArgumentParser(description="Record human review scores for one baseline scenario.")
    parser.add_argument("baseline_id")
    parser.add_argument("scenario_id")
    parser.add_argument("--correctness", type=float, required=True)
    parser.add_argument("--practical-utility", type=float, required=True)
    parser.add_argument("--scope-control", type=float, required=True)
    parser.add_argument("--next-step-clarity", type=float, required=True)
    parser.add_argument("--execution-confidence", type=float, required=True)
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    update_human_review(
        baseline_id=args.baseline_id,
        scenario_id=args.scenario_id,
        correctness=args.correctness,
        practical_utility=args.practical_utility,
        scope_control=args.scope_control,
        next_step_clarity=args.next_step_clarity,
        execution_confidence=args.execution_confidence,
        notes=args.notes,
    )
    print(json.dumps(baseline_snapshot(args.baseline_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
