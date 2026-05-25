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
    Gera uma compactação simples, determinística e sem chamada de LLM.

    Esta versão não resume semanticamente com IA.
    Ela cria um contexto operacional compacto baseado nos arquivos existentes,
    pegando trechos finais dos logs e mantendo decisões/bloqueios principais.
    """

    shared_memory = read_scoped_or_seed("shared_memory.md", memory_namespace)
    decision_log = read_scoped_or_seed("decision_log.md", memory_namespace)
    handoff_log = read_scoped_or_seed("handoff_log.md", memory_namespace)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    compact = f"""# Compact Memory — Squad v5 Lite

Última compactação: {timestamp}

Este arquivo contém o contexto operacional compacto da squad.

Use este arquivo como contexto prioritário para reduzir custo de tokens, latência e ruído.

---

## 1. Estado Atual

A Squad v5 Lite possui fluxo funcional:

Discovery → Product → QA Planning → Engineering Lead → Implementation Operator → Engineering Review → QA Execution

Componentes ativos:
- Shared Memory
- Decision Log
- Handoff Log
- Modo Mock
- QA Gate

---

## 2. Decisões Ativas

- D-001: Tarefa concluída permanece visível com texto riscado e pode ser desmarcada.
- D-002: MVP Sujo usa `localStorage`, sem backend, autenticação ou banco remoto.
- D-003: Tracking mínimo usa `analytics` com `first_visit_at`, `last_visit_at`, `visit_count` e `visit_dates`.
- D-004: Interface em português.
- D-005: MVP Sujo aprovado para experimento com usuários.

---

## 3. Bloqueios Ativos

- B-001: Engineering completa não aprovada.
- B-002: Critérios do experimento com usuários ainda precisam ser definidos.

---

## 4. Último Ciclo Conhecido

MVP Sujo de tarefas pessoais:
- Implementado via Claude Code.
- Validado manualmente.
- QA Execution aprovou para experimento com usuários.
- Engineering completa permanece bloqueada.

---

## 5. Sinais Recentes do Handoff Log

{handoff_log[-3000:] if handoff_log else "Nenhum handoff registrado ainda."}

---

## 6. Contexto Recente da Shared Memory

{shared_memory[-3000:] if shared_memory else "Shared Memory vazia ou não encontrada."}

---

## 7. Contexto Recente do Decision Log

{decision_log[-3000:] if decision_log else "Decision Log vazio ou não encontrado."}

---

## 8. Próximo Passo Recomendado

Fechar B-002 antes de expor o MVP a usuários reais:
- número de usuários;
- duração;
- critério mínimo de sucesso;
- forma de coleta dos dados.

---

## 9. Regra de Uso

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
