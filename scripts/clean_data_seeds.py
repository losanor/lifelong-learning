#!/usr/bin/env python3
"""
Scan data/ seed files for hardcoded project literals and optionally clear them.

Run interactively:
    python scripts/clean_data_seeds.py

Flags:
    --dry-run   Report matches without prompting for deletion (default: off).
    --yes       Auto-confirm every deletion (use carefully in CI).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Project-specific literals that must not live in seed files long-term.
# Each entry is a plain substring; matching is case-insensitive.
_KNOWN_LITERALS: list[str] = [
    "mvp sujo",
    "d-001",
    "d-002",
    "d-003",
    "d-004",
    "d-005",
    "b-001",
    "b-002",
    "eme",
    "critérios do experimento precisam ser definidos",
    "engineering completa bloqueada",
    "engineering completa não aprovada",
    "número de usuários",
    "product lead deve definir número",
]

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

_SEED_GLOBS = ["*.md", "*.txt", "*.json"]


def _find_seed_files() -> list[Path]:
    files: list[Path] = []
    for pattern in _SEED_GLOBS:
        for p in DATA_DIR.glob(pattern):
            if p.is_file():
                files.append(p)
    return sorted(files)


def _matches_in_file(path: Path) -> list[tuple[int, str, str]]:
    """Return list of (line_no, literal_matched, line_content) for each hit."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []
    hits: list[tuple[int, str, str]] = []
    lower_text = text.lower()
    for i, line in enumerate(text.splitlines(), start=1):
        line_lower = line.lower()
        for literal in _KNOWN_LITERALS:
            if literal in line_lower:
                hits.append((i, literal, line.strip()))
                break
    return hits


def _clear_file(path: Path) -> None:
    path.write_text("", encoding="utf-8")
    try:
        label = path.relative_to(BASE_DIR)
    except ValueError:
        label = path
    print(f"  Cleared: {label}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report only, do not prompt for deletion.")
    parser.add_argument("--yes", action="store_true", help="Auto-confirm all deletions.")
    args = parser.parse_args(argv)

    seed_files = _find_seed_files()
    if not seed_files:
        print(f"No seed files found in {DATA_DIR}")
        return 0

    dirty: list[Path] = []
    for path in seed_files:
        hits = _matches_in_file(path)
        if hits:
            dirty.append(path)
            try:
                rel = path.relative_to(BASE_DIR)
            except ValueError:
                rel = path
            print(f"\n[MATCH] {rel}")
            for line_no, literal, content in hits[:5]:
                print(f"  line {line_no:>4}  ({literal!r})  {content[:100]}")
            if len(hits) > 5:
                print(f"  ... and {len(hits) - 5} more matches")

    if not dirty:
        print("No project-specific literals found in seed files.")
        return 0

    print(f"\n{len(dirty)} file(s) contain project-specific literals.")

    if args.dry_run:
        print("--dry-run: no files modified.")
        return 0

    for path in dirty:
        try:
            rel = path.relative_to(BASE_DIR)
        except ValueError:
            rel = path
        if args.yes:
            _clear_file(path)
        else:
            answer = input(f"\nClear {rel}? [y/N] ").strip().lower()
            if answer == "y":
                _clear_file(path)
            else:
                print(f"  Skipped: {rel}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
