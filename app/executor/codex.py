"""Adapter for OpenAI Codex via the Responses API (codex-mini-latest)."""

from __future__ import annotations

import os
from pathlib import Path

from app.executor.base import CodeExecutorAdapter, ExecutionResult, TaskSpec


_AVERAGE_TOKENS: dict[str, int] = {
    "low": 5_000,
    "medium": 15_000,
    "high": 40_000,
}
_DEFAULT_COST_PER_TOKEN = 0.000_001_5


class CodexAdapter(CodeExecutorAdapter):
    def name(self) -> str:
        return "codex"

    def is_available(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    def execute(self, task_spec: TaskSpec, workspace_path: Path) -> ExecutionResult:
        try:
            try:
                from openai import OpenAI
            except ImportError:
                return ExecutionResult(
                    success=False,
                    errors="openai package is not installed.",
                    exit_code=-1,
                )
            client = OpenAI()
            response = client.responses.create(
                model="codex-mini-latest",
                input=task_spec.to_claude_prompt(),
            )
            output = getattr(response, "output_text", None) or str(response)
            tokens_used: int | None = None
            usage = getattr(response, "usage", None)
            if usage is not None:
                tokens_used = getattr(usage, "total_tokens", None)
            return ExecutionResult(
                success=True,
                output=output.strip() if isinstance(output, str) else str(output),
                tokens_used=tokens_used,
                exit_code=0,
            )
        except Exception as exc:
            return ExecutionResult(success=False, errors=str(exc), exit_code=-1)

    def estimated_cost(self, task_spec: TaskSpec) -> float | None:
        cost_per_token = float(
            os.getenv("CODEX_COST_PER_TOKEN", str(_DEFAULT_COST_PER_TOKEN))
        )
        expected_tokens = _AVERAGE_TOKENS.get(task_spec.complexity, 15_000)
        return expected_tokens * cost_per_token
