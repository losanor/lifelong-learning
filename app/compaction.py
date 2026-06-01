from pathlib import Path
from datetime import datetime

from app.scoped_storage import read_scoped_or_seed, scoped_path


BASE_DIR = Path(__file__).resolve().parent.parent

SHARED_MEMORY_PATH = BASE_DIR / "data" / "shared_memory.md"
DECISION_LOG_PATH = BASE_DIR / "data" / "decision_log.md"
HANDOFF_LOG_PATH = BASE_DIR / "data" / "handoff_log.md"
COMPACT_MEMORY_PATH = BASE_DIR / "data" / "compact_memory.md"


def read_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_compact_memory(content: str, memory_namespace: str = "") -> None:
    path = scoped_path("compact_memory.md", memory_namespace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_compact_memory(memory_namespace: str = "") -> str:
    return read_scoped_or_seed("compact_memory.md", memory_namespace)


def generate_manual_compaction(memory_namespace: str = "") -> str:
    """
    Gera uma compactação determinística sem chamada de LLM.

    Todas as seções derivam exclusivamente dos logs reais (shared_memory,
    decision_log, handoff_log). Nenhum literal de projeto é embutido aqui.
    """

    shared_memory = read_scoped_or_seed("shared_memory.md", memory_namespace)
    decision_log = read_scoped_or_seed("decision_log.md", memory_namespace)
    handoff_log = read_scoped_or_seed("handoff_log.md", memory_namespace)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _tail(text: str, n: int) -> str:
        return text[-n:].strip() if text and text.strip() else "sem dados ainda"

    compact = f"""# Compact Memory — Squad v5 Lite

Última compactação: {timestamp}

Use este arquivo como contexto prioritário para reduzir custo de tokens, latência e ruído.

---

## 1. Estado Atual

{_tail(shared_memory, 1200)}

---

## 2. Decisões e Bloqueios Registrados

{_tail(decision_log, 1500)}

---

## 3. Último Ciclo Conhecido

{_tail(handoff_log, 1000)}

---

## 4. Sinais Recentes do Handoff Log

{handoff_log[-3000:].strip() if handoff_log and handoff_log.strip() else "sem dados ainda"}

---

## 5. Contexto Recente da Shared Memory

{shared_memory[-3000:].strip() if shared_memory and shared_memory.strip() else "sem dados ainda"}

---

## 6. Contexto Recente do Decision Log

{decision_log[-3000:].strip() if decision_log and decision_log.strip() else "sem dados ainda"}

---

## 7. Próximo Passo Recomendado

{_tail(shared_memory, 600)}

---

## 8. Regra de Uso

Este arquivo deve ser usado como contexto operacional prioritário.

Os logs completos continuam disponíveis para auditoria, mas não devem ser enviados integralmente aos agentes em ciclos normais.
"""

    return compact


def compact_memory(memory_namespace: str = "") -> str:
    """
    Gera e salva a memória compactada.
    """
    compact = generate_manual_compaction(memory_namespace)
    write_compact_memory(compact, memory_namespace)
    return compact
