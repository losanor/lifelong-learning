from pathlib import Path

from app.scoped_storage import read_scoped_or_seed


BASE_DIR = Path(__file__).resolve().parent.parent

CONTEXT_POLICY_PATH = BASE_DIR / "data" / "context_policy.md"
COMPACT_MEMORY_PATH = BASE_DIR / "data" / "compact_memory.md"
SHARED_MEMORY_PATH = BASE_DIR / "data" / "shared_memory.md"
DECISION_LOG_PATH = BASE_DIR / "data" / "decision_log.md"
HANDOFF_LOG_PATH = BASE_DIR / "data" / "handoff_log.md"
RETRY_POLICY_PATH = BASE_DIR / "data" / "retry_policy.md"


def read_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def read_context_policy() -> str:
    return read_file(CONTEXT_POLICY_PATH)


def build_context_bundle(mode: str = "normal", memory_namespace: str = "") -> dict[str, str]:
    """
    Monta o pacote de contexto conforme a política de contexto.

    Modos:
    - normal
    - decision_critical
    - audit_debug
    - full_context
    """

    compact_memory = read_scoped_or_seed("compact_memory.md", memory_namespace)
    shared_memory = read_scoped_or_seed("shared_memory.md", memory_namespace)
    decision_log = read_scoped_or_seed("decision_log.md", memory_namespace)
    handoff_log = read_scoped_or_seed("handoff_log.md", memory_namespace)
    context_policy = read_context_policy()
    retry_policy = read_file(RETRY_POLICY_PATH)

    if mode == "normal":
        return {
            "context_mode": "normal",
            "context_policy": context_policy,
            "retry_policy": retry_policy,
            "compact_memory": compact_memory,
            "shared_memory": "",
            "decision_log": "",
            "handoff_log": "",
        }

    if mode == "decision_critical":
        return {
            "context_mode": "decision_critical",
            "context_policy": context_policy,
            "retry_policy": retry_policy,
            "compact_memory": compact_memory,
            "shared_memory": "",
            "decision_log": decision_log,
            "handoff_log": "",
        }

    if mode == "audit_debug":
        return {
            "context_mode": "audit_debug",
            "context_policy": context_policy,
            "compact_memory": compact_memory,
            "retry_policy": retry_policy,
            "shared_memory": "",
            "decision_log": decision_log,
            "handoff_log": handoff_log,
        }

    if mode == "full_context":
        return {
            "context_mode": "full_context",
            "context_policy": context_policy,
            "retry_policy": retry_policy,
            "compact_memory": compact_memory,
            "shared_memory": shared_memory,
            "decision_log": decision_log,
            "handoff_log": handoff_log,
        }

    raise ValueError(f"Modo de contexto inválido: {mode}")

def get_agent_context_mode(agent_name: str) -> str:
    """
    Define qual modo de contexto cada agente deve usar.
    """

    mapping = {
        "intake": "normal",
        "discovery": "normal",
        "product": "normal",
        "qa_planning": "normal",
        "engineering": "decision_critical",
        "operator": "normal",
        "qa_execution": "decision_critical",
        "writing": "normal",
        "ux_ui": "normal",
        "privacy": "decision_critical",
        "appsec": "decision_critical",
        # CoS opera no caminho quente com memoria compacta + decisoes.
        # full_context fica reservado para auditoria/debug para evitar logs
        # crescentes no prompt normal.
        "cos": "decision_critical",
    }

    return mapping.get(agent_name, "normal")


def build_agent_context(agent_name: str, memory_namespace: str = "") -> dict[str, str]:
    """
    Monta o pacote de contexto adequado para um agente específico.
    """

    mode = get_agent_context_mode(agent_name)
    return build_context_bundle(mode=mode, memory_namespace=memory_namespace)
