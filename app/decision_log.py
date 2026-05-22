from pathlib import Path
from datetime import datetime


BASE_DIR = Path(__file__).resolve().parent.parent
DECISION_LOG_PATH = BASE_DIR / "data" / "decision_log.md"


def read_decision_log() -> str:
    """
    Lê o Decision Log formal da squad.
    """
    if not DECISION_LOG_PATH.exists():
        return ""

    return DECISION_LOG_PATH.read_text(encoding="utf-8")


def append_decision(
    decision_id: str,
    title: str,
    context: str,
    decision: str,
    impact: str,
    owner: str,
    status: str = "Ativa",
    review_criteria: str = "Revisar quando houver mudança relevante de contexto."
) -> None:
    """
    Adiciona uma decisão formal ao Decision Log.
    """
    DECISION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    date = datetime.now().strftime("%Y-%m-%d")

    entry = f"""

---

## {decision_id} — {title}

Data: {date}  
Status: {status}  
Dono: {owner}  

### Contexto
{context}

### Decisão
{decision}

### Impacto
{impact}

### Critérios de revisão
{review_criteria}
"""

    with DECISION_LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(entry)