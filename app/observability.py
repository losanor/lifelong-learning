"""Configuracao de tracing para execucoes da squad."""

from __future__ import annotations

import os


def langsmith_enabled() -> bool:
    return os.getenv("LANGSMITH_TRACING", "").lower() == "true"


def build_run_config(
    *,
    run_id: str,
    active_flow: str = "",
    mode: str = "runtime",
) -> dict:
    """Anexa metadata/tags consumidas pelo tracing do LangGraph/LangSmith."""
    tags = ["squad-v5-lite", mode]
    if active_flow:
        tags.append(active_flow)

    return {
        "run_name": f"squad:{run_id}",
        "tags": tags,
        "metadata": {
            "run_id": run_id,
            "active_flow": active_flow or "intake_pending",
            "structured_output": True,
            "langsmith_enabled": langsmith_enabled(),
        },
    }
