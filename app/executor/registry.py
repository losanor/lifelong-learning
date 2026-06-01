"""Adapter registry and selection logic."""

from __future__ import annotations

from app.executor.base import CodeExecutorAdapter, TaskSpec
from app.executor.aider import AiderAdapter
from app.executor.claude_code import ClaudeCodeAdapter
from app.executor.codex import CodexAdapter


REGISTRY: dict[str, CodeExecutorAdapter] = {
    "claude_code": ClaudeCodeAdapter(),
    "codex": CodexAdapter(),
    "aider": AiderAdapter(),
}

_COMPLEXITY_ORDER: dict[str, list[str]] = {
    "high": ["claude_code", "codex", "aider"],
    "medium": ["codex", "aider", "claude_code"],
    "low": ["codex", "aider", "claude_code"],
}


def get_registry() -> dict[str, CodeExecutorAdapter]:
    return REGISTRY


def select_adapter(task_spec: TaskSpec, policy: dict) -> CodeExecutorAdapter:
    """
    Selection priority:
    1. preferred_executor if available in registry and is_available().
    2. Adapters ordered by complexity preset, filtered by budget.
    3. RuntimeError if none qualify.
    """
    budget = policy.get("budget_remaining_usd", policy.get("max_cost_usd"))

    if task_spec.preferred_executor:
        preferred = REGISTRY.get(task_spec.preferred_executor)
        if preferred is not None and preferred.is_available():
            return preferred

    order = _COMPLEXITY_ORDER.get(task_spec.complexity, _COMPLEXITY_ORDER["medium"])
    for adapter_name in order:
        adapter = REGISTRY.get(adapter_name)
        if adapter is None or not adapter.is_available():
            continue
        cost = adapter.estimated_cost(task_spec)
        if budget is not None and cost is not None and cost > budget:
            continue
        return adapter

    raise RuntimeError(
        f"No code executor adapter is available for task "
        f"'{task_spec.objective[:80]}'. "
        f"Install claude CLI, set OPENAI_API_KEY, or install aider."
    )
