from pathlib import Path
from datetime import datetime


BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_PATH = BASE_DIR / "data" / "shared_memory.md"


def read_shared_memory() -> str:
    """
    Lê a memória compartilhada da squad.
    Se o arquivo ainda não existir, retorna string vazia.
    """
    if not MEMORY_PATH.exists():
        return ""

    return MEMORY_PATH.read_text(encoding="utf-8")


def append_to_shared_memory(section_title: str, content: str) -> None:
    """
    Adiciona uma nova entrada ao final da memória compartilhada.
    """
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    entry = f"""

---

## {section_title}

Data/hora: {timestamp}

{content}
"""

    with MEMORY_PATH.open("a", encoding="utf-8") as file:
        file.write(entry)


def register_decision(decision_id: str, title: str, content: str, status: str = "Ativa") -> None:
    """
    Registra uma decisão no final da memória compartilhada.
    """
    decision_entry = f"""
### {decision_id} — {title}

Status: {status}

{content}
"""

    append_to_shared_memory("Nova decisão registrada", decision_entry)


def register_cycle_summary(cycle_name: str, summary: str, status: str) -> None:
    """
    Registra um resumo de ciclo executado pela squad.
    """
    cycle_entry = f"""
Ciclo: {cycle_name}

Status: {status}

Resumo:
{summary}
"""

    append_to_shared_memory("Resumo de ciclo", cycle_entry)