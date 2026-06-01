"""Read-only readiness check for the no-monthly-cost local pilot."""

from __future__ import annotations

import json
import os

from app.config import ANTHROPIC_API_KEY, USE_MOCK_MODEL
from app.observability import langsmith_enabled, langsmith_ready
from app.operational_store import DEFAULT_DB_PATH, metrics_snapshot
from app.executor.registry import get_registry


def pilot_readiness() -> dict:
    sampling_raw = os.getenv("LANGSMITH_TRACING_SAMPLING_RATE", "1.0").strip()
    try:
        sampling_rate = float(sampling_raw)
    except ValueError:
        sampling_rate = -1.0

    findings: list[str] = []
    if USE_MOCK_MODEL:
        findings.append("USE_MOCK_MODEL=true: adequado para teste, nao para piloto real.")
    if not ANTHROPIC_API_KEY:
        findings.append("ANTHROPIC_API_KEY ausente: execucao real indisponivel.")
    if not langsmith_ready():
        findings.append("LangSmith tracing nao esta ativo com chave configurada.")
    if sampling_rate < 0 or sampling_rate > 1:
        findings.append("LANGSMITH_TRACING_SAMPLING_RATE deve estar entre 0 e 1.")
    elif sampling_rate > 0.25:
        findings.append("Sampling acima de 25% pode consumir mais rapidamente a franquia gratuita.")

    metrics = metrics_snapshot(DEFAULT_DB_PATH) if DEFAULT_DB_PATH.exists() else {}
    registry = get_registry()
    available_adapters = [name for name, adapter in registry.items() if adapter.is_available()]
    ready_for_real_pilot = (
        not USE_MOCK_MODEL
        and bool(ANTHROPIC_API_KEY)
        and langsmith_ready()
        and 0 <= sampling_rate <= 0.25
    )
    return {
        "pilot_mode": "local_langgraph_langsmith_developer",
        "ready_for_real_pilot": ready_for_real_pilot,
        "configuration": {
            "model_provider_key_present": bool(ANTHROPIC_API_KEY),
            "use_mock_model": USE_MOCK_MODEL,
            "langsmith_tracing_enabled": langsmith_enabled(),
            "langsmith_ready": langsmith_ready(),
            "sampling_rate": sampling_rate,
        },
        "cost_controls": {
            "monthly_paid_seat_required": False,
            "recommended_paid_trace_spend_limit_usd": 0,
            "recommended_langsmith_trace_limit": 5000,
            "automatic_workspace_effects": False,
        },
        "local_metrics": {
            "run_count": metrics.get("run_count", 0),
            "execution_ready_rate": metrics.get("execution_ready_rate", 0),
            "escalation_rate": metrics.get("escalation_rate", 0),
        },
        "executor_adapters": {
            "registered": list(registry.keys()),
            "available": available_adapters,
        },
        "findings": findings,
    }


def main() -> None:
    print(json.dumps(pilot_readiness(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
