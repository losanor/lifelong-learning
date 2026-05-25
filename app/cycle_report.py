from pathlib import Path
from datetime import datetime

from app.scoped_storage import read_scoped_or_seed, scoped_path


BASE_DIR = Path(__file__).resolve().parent.parent
CYCLE_REPORT_PATH = BASE_DIR / "data" / "cycle_reports.md"


def append_cycle_report(
    user_goal: str,
    cos_output: str,
    decision: str = "UNKNOWN",
    route_action: str = "UNKNOWN",
    ece: str = "UNKNOWN",
    run_id: str = "",
    memory_namespace: str = "",
) -> None:
    """
    Registra o relatório final de ciclo emitido pelo CoS.
    """
    path = scoped_path("cycle_reports.md", memory_namespace)
    path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    entry = f"""

---

## Cycle Report — {timestamp}

### Run ID
{run_id}

### Objetivo
{user_goal}

### Decisão Executiva
{decision}

### Ação de Roteamento
{route_action}

### ECE
{ece}

### Relatório CoS
{cos_output}
"""

    with path.open("a", encoding="utf-8") as file:
        file.write(entry)


def read_cycle_reports(memory_namespace: str = "") -> str:
    """
    Lê os relatórios de ciclo.
    """
    return read_scoped_or_seed("cycle_reports.md", memory_namespace)
