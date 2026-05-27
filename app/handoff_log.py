from datetime import datetime
from pathlib import Path

from app.operational_store import DEFAULT_DB_PATH, record_handoff_event
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
    next_step: str = "Nao informado.",
    escalate_to_cos: str = "Nao.",
    db_path: str | Path | None = None,
) -> None:
    """Registra uma passagem textual e o evento estruturado da execucao."""
    path = scoped_path("handoff_log.md", memory_namespace)
    path.parent.mkdir(parents=True, exist_ok=True)

    entry = f"""

---

## Handoff - {from_agent} -> {to_agent}

### Run ID
{run_id}

Data/hora: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

### Artefato entregue
{artifact}

### Resumo da entrega
{summary}

### ECE
{ece}

### Bloqueios
{blockers}

### Proximo passo
{next_step}

### Escalar para CoS?
{escalate_to_cos}
"""

    with path.open("a", encoding="utf-8") as file:
        file.write(entry)
    record_handoff_event(
        run_id=run_id,
        source_agent=from_agent,
        target_agent=to_agent,
        artifact=artifact,
        summary=summary,
        ece=ece,
        blockers=blockers,
        next_step=next_step,
        escalate_to_cos=escalate_to_cos,
        memory_namespace=memory_namespace,
        db_path=db_path or DEFAULT_DB_PATH,
    )


def read_handoff_log(memory_namespace: str = "") -> str:
    """Le o Handoff Log completo."""
    return read_scoped_or_seed("handoff_log.md", memory_namespace)
