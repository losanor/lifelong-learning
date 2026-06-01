"""Adapter for the Aider CLI code assistant."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.executor.base import CodeExecutorAdapter, ExecutionResult, TaskSpec

_LOCAL_MODEL_INDICATORS = {"local", "ollama", "llama", "mistral", "deepseek"}


class AiderAdapter(CodeExecutorAdapter):
    def name(self) -> str:
        return "aider"

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                ["aider", "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return False

    def execute(self, task_spec: TaskSpec, workspace_path: Path) -> ExecutionResult:
        try:
            cmd = ["aider", "--message", task_spec.objective, "--yes-always"]
            cmd.extend(task_spec.target_files)
            result = subprocess.run(
                cmd,
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=task_spec.max_duration_seconds,
            )
            return ExecutionResult(
                success=result.returncode == 0,
                output=result.stdout.strip(),
                errors=result.stderr.strip(),
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False,
                errors=f"Aider timed out after {task_spec.max_duration_seconds}s.",
                exit_code=-1,
                partial=True,
            )
        except Exception as exc:
            return ExecutionResult(success=False, errors=str(exc), exit_code=-1)

    def estimated_cost(self, task_spec: TaskSpec) -> float | None:
        model = os.getenv("AIDER_MODEL", "").lower()
        if any(indicator in model for indicator in _LOCAL_MODEL_INDICATORS):
            return 0.0
        return None
