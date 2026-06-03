"""Adapter for the Claude Code CLI (claude --print --no-interactive)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.executor.base import CodeExecutorAdapter, ExecutionResult, TaskSpec


class ClaudeCodeAdapter(CodeExecutorAdapter):
    def name(self) -> str:
        return "claude_code"

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return False

    def execute(self, task_spec: TaskSpec, workspace_path: Path) -> ExecutionResult:
        # max_duration_seconds is the primary iteration-control mechanism for Claude Code:
        # the CLI does not expose an iteration counter, so the subprocess timeout is the
        # only hard bound. max_iterations is passed in the prompt for informational purposes.
        try:
            result = subprocess.run(
                ["claude", "--print", "--no-interactive", task_spec.to_claude_prompt()],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=task_spec.max_duration_seconds,
            )
            output = result.stdout.strip()
            files_changed = self._get_changed_files(workspace_path)
            # Heuristic: output longer than max_iterations × 2000 chars indicates many
            # tool-call cycles occurred. Flag partial so callers can decide to continue.
            partial = len(output) > task_spec.max_iterations * 2000
            return ExecutionResult(
                success=result.returncode == 0,
                files_changed=files_changed,
                output=output,
                errors=result.stderr.strip(),
                exit_code=result.returncode,
                partial=partial,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False,
                errors=f"Claude Code timed out after {task_spec.max_duration_seconds}s.",
                exit_code=-1,
                partial=True,
            )
        except Exception as exc:
            return ExecutionResult(success=False, errors=str(exc), exit_code=-1)

    def _get_changed_files(self, workspace_path: Path) -> list[str]:
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return [f for f in result.stdout.strip().splitlines() if f]
        except Exception:
            pass
        return []

    def estimated_cost(self, task_spec: TaskSpec) -> float | None:
        return None
