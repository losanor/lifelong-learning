from pathlib import Path
from datetime import datetime

from app.scoped_storage import read_scoped_or_seed, scoped_path


BASE_DIR = Path(__file__).resolve().parent.parent
HANDOFF_LOG_PATH = BASE_DIR / "data" / "handoff_log.md"


def append_handoff(
    from_agent: str,
    to_agent: str,
    artifact: str,
    summary: str,
    ece: str,
    run_id: str = "",
    memory_namespace: str = "",
    blockers: str = "Nenhum bloqueio informado.",
    next_step: str = "Não informado.",
    escalate_to_cos: str = "Não."
) -> None:
    """
    Registra uma passagem entre agentes no Handoff Log.
    """
    path = scoped_path("handoff_log.md", memory_namespace)
    path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    entry = f"""

---

## Handoff — {from_agent} → {to_agent}

### Run ID
{run_id}

Data/hora: {timestamp}

### Artefato entregue
{artifact}

### Resumo da entrega
{summary}

### ECE
{ece}

### Bloqueios
{blockers}

### Próximo passo
{next_step}

### Escalar para CoS?
{escalate_to_cos}
"""

    with path.open("a", encoding="utf-8") as file:
        file.write(entry)


def read_handoff_log(memory_namespace: str = "") -> str:
    """
    Lê o Handoff Log completo.
    """
    return read_scoped_or_seed("handoff_log.md", memory_namespace)
