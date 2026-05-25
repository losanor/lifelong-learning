from pathlib import Path
from datetime import datetime

from app.scoped_storage import read_scoped_or_seed, scoped_path


BASE_DIR = Path(__file__).resolve().parent.parent
DECISION_LOG_PATH = BASE_DIR / "data" / "decision_log.md"


def read_decision_log(memory_namespace: str = "") -> str:
    """
    Lê o Decision Log formal da squad.
    """
    return read_scoped_or_seed("decision_log.md", memory_namespace)


def append_decision(
    decision_id: str,
    title: str,
    context: str,
    decision: str,
    impact: str,
    owner: str,
    status: str = "Ativa",
    memory_namespace: str = "",
    review_criteria: str = "Revisar quando houver mudança relevante de contexto."
) -> None:
    """
    Adiciona uma decisão formal ao Decision Log.
    """
    path = scoped_path("decision_log.md", memory_namespace)
    path.parent.mkdir(parents=True, exist_ok=True)

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

    with path.open("a", encoding="utf-8") as file:
        file.write(entry)
