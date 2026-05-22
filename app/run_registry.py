from pathlib import Path
from datetime import datetime
import uuid


BASE_DIR = Path(__file__).resolve().parent.parent
RUN_REGISTRY_PATH = BASE_DIR / "data" / "run_registry.md"


def generate_run_id() -> str:
    """
    Gera um ID único e legível para a execução.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:4]
    return f"run_{timestamp}_{suffix}"


def append_run_start(
    run_id: str,
    user_goal: str,
    status: str = "started",
) -> None:
    """
    Registra início de uma execução.
    """
    RUN_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    entry = f"""

---

## Run — {run_id}

### Started At
{timestamp}

### User Goal
{user_goal}

### Initial Status
{status}
"""

    with RUN_REGISTRY_PATH.open("a", encoding="utf-8") as file:
        file.write(entry)


def append_run_end(
    run_id: str,
    final_status: str,
    cos_decision: str = "",
    route_action: str = "",
    route_decision: str = "",
    human_escalation_created: bool = False,
) -> None:
    """
    Registra fechamento de uma execução.
    """
    RUN_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    entry = f"""

### Finished At
{timestamp}

### Final Status
{final_status}

### CoS Decision
{cos_decision}

### Route Action
{route_action}

### Route Decision
{route_decision}

### Human Escalation Created
{human_escalation_created}
"""

    with RUN_REGISTRY_PATH.open("a", encoding="utf-8") as file:
        file.write(entry)


def read_run_registry() -> str:
    """
    Lê o Run Registry completo.
    """
    if not RUN_REGISTRY_PATH.exists():
        return ""

    return RUN_REGISTRY_PATH.read_text(encoding="utf-8")