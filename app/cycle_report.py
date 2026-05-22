from pathlib import Path
from datetime import datetime


BASE_DIR = Path(__file__).resolve().parent.parent
CYCLE_REPORT_PATH = BASE_DIR / "data" / "cycle_reports.md"


def append_cycle_report(
    user_goal: str,
    cos_output: str,
    decision: str = "UNKNOWN",
    route_action: str = "UNKNOWN",
    ece: str = "UNKNOWN",
    run_id: str = "",
) -> None:
    """
    Registra o relatório final de ciclo emitido pelo CoS.
    """
    CYCLE_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

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

    with CYCLE_REPORT_PATH.open("a", encoding="utf-8") as file:
        file.write(entry)


def read_cycle_reports() -> str:
    """
    Lê os relatórios de ciclo.
    """
    if not CYCLE_REPORT_PATH.exists():
        return ""

    return CYCLE_REPORT_PATH.read_text(encoding="utf-8")