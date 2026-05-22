"""Contexto local deterministico para trabalho real em Git e documentacao."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import subprocess


IGNORED_DIRS = {".git", ".venv", "__pycache__", "node_modules"}
DOC_SUFFIXES = {".md", ".docx", ".txt", ".rst"}
PROJECT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class WorkspaceContext:
    root: str
    git_available: bool
    git_repo: bool
    git_branch: str
    git_status: str
    documentation_refs: tuple[str, ...]
    project_refs: tuple[str, ...]
    execution_notes: tuple[str, ...]

    def as_state(self) -> dict:
        return asdict(self)


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def _is_visible_candidate(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return not any(part in IGNORED_DIRS for part in relative.parts)


def _collect_refs(root: Path, suffixes: set[str], *, limit: int) -> tuple[str, ...]:
    refs: list[str] = []
    candidates = sorted(
        root.rglob("*"),
        key=lambda path: (len(path.relative_to(root).parts), path.relative_to(root).as_posix().casefold()),
    )
    for path in candidates:
        if len(refs) >= limit:
            break
        if not path.is_file() or path.suffix.casefold() not in suffixes:
            continue
        if not _is_visible_candidate(path, root):
            continue
        refs.append(path.relative_to(root).as_posix())
    return tuple(refs)


def _git_status_summary(status_lines: list[str]) -> str:
    if not status_lines:
        return "clean"
    changed = sum(1 for line in status_lines if not line.startswith("??"))
    untracked = sum(1 for line in status_lines if line.startswith("??"))
    return f"dirty: {changed} changed, {untracked} untracked"


def capture_workspace_context(root: str | Path | None = None) -> WorkspaceContext:
    workspace_root = Path(root or Path.cwd()).resolve()
    git_probe = _run_git(workspace_root, "rev-parse", "--is-inside-work-tree")
    git_available = git_probe is not None
    git_repo = bool(git_probe and git_probe.returncode == 0 and git_probe.stdout.strip() == "true")

    git_branch = ""
    git_status = "not a git repository"
    if git_repo:
        branch = _run_git(workspace_root, "branch", "--show-current")
        status = _run_git(workspace_root, "status", "--short")
        git_branch = branch.stdout.strip() if branch and branch.returncode == 0 else "detached-or-unknown"
        status_lines = status.stdout.splitlines() if status and status.returncode == 0 else []
        git_status = _git_status_summary(status_lines)

    notes = [
        "Use os arquivos existentes como fonte primaria antes de propor novos artefatos.",
        "Acoes destrutivas e publicacao remota exigem autorizacao humana.",
    ]
    if git_repo:
        notes.append("Git local detectado: branch, diff, testes e commits podem virar proximos passos.")
    else:
        notes.append("Git nao detectado: proponha refs locais e documentacao; nao presuma PR, branch ou commit.")

    return WorkspaceContext(
        root=str(workspace_root),
        git_available=git_available,
        git_repo=git_repo,
        git_branch=git_branch,
        git_status=git_status,
        documentation_refs=_collect_refs(workspace_root, DOC_SUFFIXES, limit=12),
        project_refs=_collect_refs(workspace_root, PROJECT_SUFFIXES, limit=18),
        execution_notes=tuple(notes),
    )


def format_workspace_context(context: dict | WorkspaceContext | None) -> str:
    if context is None:
        return "Workspace context indisponivel."
    payload = context.as_state() if isinstance(context, WorkspaceContext) else context
    docs = ", ".join(payload.get("documentation_refs", ())) or "nenhuma ref documental detectada"
    files = ", ".join(payload.get("project_refs", ())) or "nenhuma ref de projeto detectada"
    notes = " | ".join(payload.get("execution_notes", ())) or "sem notas"
    return (
        f"Root: {payload.get('root', '')}\n"
        f"Git repo: {payload.get('git_repo', False)}\n"
        f"Git branch: {payload.get('git_branch', '') or 'n/a'}\n"
        f"Git status: {payload.get('git_status', '')}\n"
        f"Docs refs: {docs}\n"
        f"Project refs: {files}\n"
        f"Notas operacionais: {notes}"
    )
