"""Core dataclasses and ABC for code executor adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class TaskSpec:
    objective: str
    context_files: list[str] = field(default_factory=list)
    target_files: list[str] = field(default_factory=list)
    forbidden_files: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    validation_commands: list[str] = field(default_factory=list)
    max_iterations: int = 5
    max_duration_seconds: int = 300
    complexity: Literal["low", "medium", "high"] = "medium"
    preferred_executor: str | None = None

    def to_claude_prompt(self) -> str:
        def _list_block(items: list[str], label: str) -> str:
            if not items:
                return f"## {label}\n(none)\n"
            body = "\n".join(f"- {item}" for item in items)
            return f"## {label}\n{body}\n"

        sections = [
            "# Execution Task\n",
            f"## Objective\n{self.objective}\n",
            _list_block(self.context_files, "Context Files"),
            _list_block(self.target_files, "Target Files"),
            _list_block(self.forbidden_files, "Forbidden Files (do NOT modify)"),
            _list_block(self.acceptance_criteria, "Acceptance Criteria"),
            _list_block(self.constraints, "Constraints"),
            _list_block(self.validation_commands, "Validation Commands"),
            (
                f"## Settings\n"
                f"- max_iterations: {self.max_iterations}\n"
                f"- max_duration_seconds: {self.max_duration_seconds}\n"
                f"- complexity: {self.complexity}\n"
            ),
        ]
        return "\n".join(sections)


@dataclass
class ExecutionResult:
    success: bool
    files_changed: list[str] = field(default_factory=list)
    output: str = ""
    errors: str = ""
    exit_code: int = 0
    tokens_used: int | None = None
    iterations: int | None = None
    partial: bool = False


class CodeExecutorAdapter(ABC):
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def execute(self, task_spec: TaskSpec, workspace_path: Path) -> ExecutionResult:
        """Never raises — captures all exceptions and returns ExecutionResult(success=False)."""

    @abstractmethod
    def estimated_cost(self, task_spec: TaskSpec) -> float | None: ...
