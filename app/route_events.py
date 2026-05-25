from pathlib import Path
from datetime import datetime
from typing import Any

from app.scoped_storage import read_scoped_or_seed, scoped_path


BASE_DIR = Path(__file__).resolve().parent.parent
ROUTE_EVENTS_PATH = BASE_DIR / "data" / "route_events.md"


def append_route_event(
    user_goal: str,
    route_summary: dict[str, Any],
    run_id: str = "",
    memory_namespace: str = "",
) -> None:
    """
    Registra um evento de roteamento no Route Events Log.
    """
    path = scoped_path("route_events.md", memory_namespace)
    path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    entry = f"""

---

## Route Event — {timestamp}

### Run ID
{run_id}

### Objetivo
{user_goal}

### CoS Decision
{route_summary.get("cos_decision", "")}

### CoS Route Action
{route_summary.get("cos_route_action", "")}

### Route Status
{route_summary.get("route_status", "")}

### Route Target
{route_summary.get("route_target", "")}

### Route Reason
{route_summary.get("route_reason", "")}

### Route Decision
{route_summary.get("route_decision", "")}

### Consistency Error
{route_summary.get("consistency_error", "")}

### Retry Allowed
{route_summary.get("retry_allowed", False)}

### Retry Target
{route_summary.get("retry_target", "")}

### Retry Blocked
{route_summary.get("retry_blocked", False)}

### Retry Block Reason
{route_summary.get("retry_block_reason", "")}
"""

    with path.open("a", encoding="utf-8") as file:
        file.write(entry)


def read_route_events(memory_namespace: str = "") -> str:
    """
    Lê o Route Events Log completo.
    """
    return read_scoped_or_seed("route_events.md", memory_namespace)
