"""Execution request lifecycle with an explicit human approval gate."""

from __future__ import annotations

import hashlib
import difflib
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from app.operational_store import (
    DEFAULT_DB_PATH,
    execution_request_snapshot,
    record_handoff_event,
    record_human_decision,
    record_apply_decision,
    record_execution_decision,
    record_execution_application,
    record_git_delivery,
    record_execution_preparation,
    record_execution_request,
    record_workflow_checkpoint,
    update_run_status,
    update_execution_request_target_refs,
)


EXECUTION_MODE = "dry_run_only"
PENDING_APPROVAL = "pending_approval"
NOT_ACTIONABLE = "not_actionable"
APPROVED_FOR_DRY_RUN = "approved_for_dry_run"
AWAITING_PATCH = "awaiting_patch"
AWAITING_APPLY_APPROVAL = "awaiting_apply_approval"
APPROVED_FOR_APPLY = "approved_for_apply"
APPLIED_VALIDATED = "applied_validated"
GIT_COMMITTED = "git_committed"
PR_OPENED = "pr_opened"
ROLLED_BACK = "rolled_back"
MAX_PATCH_BYTES = 1_000_000
VALIDATION_PRESETS = {
    "git_diff_check": ["git", "diff", "--check"],
    "python_compile": [sys.executable, "-m", "compileall", "app", "tests"],
    "unit_tests": [sys.executable, "-m", "unittest", "tests.test_operational_contracts", "-v"],
}
AUTO_PATCH_SUPPORTED_FILES = {"bot.py", "test_bot.py", "requirements.txt", ".env.example", ".gitignore", "README.md"}

FILE_CREATION_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".txt", ".toml", ".json", ".yaml", ".yml", ".env", ".example"}


def requires_execution_gate(packet: dict[str, Any]) -> bool:
    if packet.get("execution_ready"):
        return False
    if packet.get("governance_blocked") and packet.get("decision") not in {"NO_GO", "RETURN_TO_OPERATOR", "RETURN_TO_ENGINEERING", "GO_WITH_RESTRICTIONS"}:
        return False
    workspace_root = str(packet.get("workspace_root", "")).strip()
    if not workspace_root:
        return False
    target_refs = [str(ref) for ref in packet.get("target_refs", [])]
    if not target_refs:
        return False
    actionable_ref = False
    for ref in target_refs:
        name = Path(ref).name.lower()
        suffixes = {Path(name).suffix.lower()}
        if name == ".env.example":
            suffixes.add(".example")
        if suffixes.intersection(FILE_CREATION_SUFFIXES) and not name.endswith(("decision_log.md", "handoff_log.md")):
            actionable_ref = True
            break
    if not actionable_ref:
        return False
    action_text = " ".join(str(item) for item in packet.get("recommended_actions", []))
    return bool(re.search(r"\b(criar|copiar|gerar|implementar|executar|install|pip|python|arquivo|bot\.py)\b", action_text, re.IGNORECASE))


def _request_target_refs(packet: dict[str, Any]) -> list[str]:
    workspace_root = str(packet.get("workspace_root", "")).strip()
    if not workspace_root:
        return list(packet.get("target_refs", []))
    root = Path(workspace_root).resolve()
    refs: list[str] = []
    for raw_ref in packet.get("target_refs", []):
        ref = str(raw_ref)
        try:
            path = Path(ref)
            if path.is_absolute():
                resolved = path.resolve()
                if resolved == root or root in resolved.parents:
                    ref = resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            pass
        refs.append(ref)
    return refs


def _normalize_relative_ref(ref: str) -> str | None:
    normalized = ref.replace("\\", "/").removeprefix("./")
    if not normalized or normalized.startswith("/") or ":" in normalized:
        return None
    if ".." in Path(normalized).parts:
        return None
    return normalized


def _patch_target_refs(patch_text: str) -> list[str]:
    targets: list[str] = []
    patterns = (
        r"^(?:---|\+\+\+)\s+([^\t\r\n ]+)",
        r"^diff --git\s+([^\s]+)\s+([^\s]+)",
        r"^(?:rename|copy) (?:from|to)\s+(.+)$",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, patch_text, flags=re.MULTILINE):
            for raw_ref in match.groups():
                if raw_ref == "/dev/null":
                    continue
                relative_ref = raw_ref.removeprefix("a/").removeprefix("b/")
                normalized = _normalize_relative_ref(relative_ref)
                if normalized and normalized not in targets:
                    targets.append(normalized)
    return targets


def _workspace_target_evidence(
    target_refs: list[str],
    *,
    workspace_root: Path,
) -> tuple[list[dict[str, Any]], set[str]]:
    evidence: list[dict[str, Any]] = []
    allowed_refs: set[str] = set()
    root = workspace_root.resolve()
    for raw_ref in target_refs:
        normalized = _normalize_relative_ref(raw_ref)
        if normalized is None:
            evidence.append({"target_ref": raw_ref, "eligible_path": False, "exists": False})
            continue
        resolved = (root / normalized).resolve()
        in_workspace = resolved == root or root in resolved.parents
        if in_workspace:
            allowed_refs.add(normalized)
        evidence.append(
            {
                "target_ref": raw_ref,
                "normalized_ref": normalized,
                "eligible_path": in_workspace,
                "exists": resolved.exists() if in_workspace else False,
            }
        )
    return evidence, allowed_refs


def _effective_target_refs_for_generation(request: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for raw_ref in request.get("target_refs", []):
        normalized = _normalize_relative_ref(str(raw_ref))
        if not normalized:
            continue
        ref = ".env.example" if normalized == ".env" else normalized
        if ref not in refs:
            refs.append(ref)
    action_text = " ".join(str(item) for item in request.get("recommended_actions", []))
    packet_text = " ".join(
        [
            action_text,
            " ".join(str(item) for item in request.get("verification_steps", [])),
            str(request.get("operational_packet", {}).get("decision", "")),
        ]
    )
    looks_like_telegram_bot = bool(
        {"bot.py", "requirements.txt"}.intersection(refs)
        or re.search(r"\b(bot|telegram|python)\b", packet_text, re.IGNORECASE)
    )
    if looks_like_telegram_bot:
        for ref in ("bot.py", "test_bot.py", "requirements.txt", ".env.example", ".gitignore", "README.md"):
            if ref not in refs:
                refs.append(ref)
    return refs


def _render_new_file_diff(path: str, content: str) -> str:
    normalized = _normalize_relative_ref(path)
    if not normalized:
        raise ValueError(f"Invalid generated patch target: {path}")
    lines = content.splitlines()
    if content.endswith("\n"):
        trailing_marker = ""
    else:
        trailing_marker = "\n\\ No newline at end of file"
    body = "\n".join(f"+{line}" for line in lines)
    hunk_count = max(len(lines), 1)
    if body:
        body = f"{body}\n"
    return (
        f"diff --git a/{normalized} b/{normalized}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{normalized}\n"
        f"@@ -0,0 +1,{hunk_count} @@\n"
        f"{body}{trailing_marker}"
    )


def _render_existing_file_diff(path: str, before: str, after: str) -> str:
    normalized = _normalize_relative_ref(path)
    if not normalized:
        raise ValueError(f"Invalid generated patch target: {path}")
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{normalized}",
            tofile=f"b/{normalized}",
        )
    )


def _git_apply_command(workspace_root: Path, *, check: bool = False, reverse: bool = False) -> list[str]:
    command = ["git", "apply"]
    if check:
        command.append("--check")
    if reverse:
        command.append("--reverse")
    command.append("--recount")
    try:
        top_level = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if top_level.returncode == 0:
            repo_root = Path(top_level.stdout.strip()).resolve()
            root = workspace_root.resolve()
            if root != repo_root and repo_root in root.parents:
                command.append(f"--directory={root.relative_to(repo_root).as_posix()}")
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    command.append("-")
    return command


def _is_git_worktree(workspace_root: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return completed.returncode == 0 and completed.stdout.strip() == "true"
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def _run_validation_preset(preset: str, *, workspace_root: Path) -> dict[str, Any]:
    command = VALIDATION_PRESETS[preset]
    if preset == "git_diff_check" and not _is_git_worktree(workspace_root):
        return {
            "command": command,
            "passed": True,
            "return_code": 0,
            "skipped": True,
            "output": "Workspace is not a Git repository; git diff --check was skipped after patch apply --check passed.",
        }
    return _run_command(command, workspace_root=workspace_root)


def _diff_stat_evidence(*, workspace_root: Path) -> dict[str, Any]:
    command = ["git", "diff", "--stat"]
    if not _is_git_worktree(workspace_root):
        return {
            "command": command,
            "passed": True,
            "return_code": 0,
            "skipped": True,
            "output": "Workspace is not a Git repository; diff stat is not available.",
        }
    return _run_command(command, workspace_root=workspace_root)


def _post_apply_smoke_check(request: dict[str, Any], *, workspace_root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    errors: list[str] = []
    for ref in request.get("target_refs", []):
        normalized = _normalize_relative_ref(str(ref))
        if not normalized:
            errors.append(f"Target ref is not a safe relative file: {ref}")
            continue
        path = workspace_root / normalized
        exists = path.exists()
        files.append({"target_ref": normalized, "exists": exists, "bytes": path.stat().st_size if exists else 0})
        if not exists:
            errors.append(f"Expected file was not created: {normalized}")
    python_targets = [item["target_ref"] for item in files if item["exists"] and item["target_ref"].endswith(".py")]
    compile_results = [
        {
            "target_ref": ref,
            **_run_command([sys.executable, "-m", "py_compile", ref], workspace_root=workspace_root, timeout=30),
        }
        for ref in python_targets
    ]
    for result in compile_results:
        if not result["passed"]:
            errors.append(f"Python compile failed for {result['target_ref']}: {result['output']}")
    contract_results: list[dict[str, Any]] = []
    if (workspace_root / "test_bot.py").exists():
        result = _run_command(
            [sys.executable, "-m", "unittest", "test_bot", "-v"],
            workspace_root=workspace_root,
            timeout=30,
        )
        contract_results.append(result)
        if not result["passed"]:
            errors.append(f"Generated bot contract failed: {result['output']}")
    external_inputs: list[dict[str, str]] = []
    if (workspace_root / "bot.py").exists() and (workspace_root / ".env.example").exists():
        env_path = workspace_root / ".env"
        token_present = False
        if env_path.exists():
            token_present = bool(re.search(r"^TELEGRAM_TOKEN=\\S+", env_path.read_text(encoding="utf-8-sig", errors="replace"), re.MULTILINE))
        if not token_present:
            external_inputs.append(
                {
                    "key": "TELEGRAM_TOKEN",
                    "title": "Token do bot no Telegram",
                    "reason": "O produto foi criado, mas precisa do token externo do BotFather para iniciar o bot real.",
                    "instruction": "Crie o arquivo .env a partir de .env.example e preencha TELEGRAM_TOKEN.",
                }
            )
    return {
        "passed": not errors,
        "files": files,
        "compile_results": compile_results,
        "contract_results": contract_results,
        "external_inputs": external_inputs,
        "errors": errors,
    }


def _record_post_apply_continuation(
    request: dict[str, Any],
    *,
    workspace_root: Path,
    smoke: dict[str, Any],
    db_path: str | Path,
) -> None:
    run_id = request["run_id"]
    memory_namespace = request.get("operational_packet", {}).get("memory_namespace", "")
    record_handoff_event(
        run_id=run_id,
        source_agent="Implementation Operator",
        target_agent="QA Execution",
        artifact="Applied Workspace Candidate",
        summary="Arquivos aplicados no workspace; QA operacional iniciou validacao smoke automatica.",
        ece="C2",
        blockers="",
        next_step="QA Execution deve validar arquivos criados e identificar pendencias externas para uso real.",
        memory_namespace=memory_namespace,
        db_path=db_path,
    )
    record_workflow_checkpoint(
        thread_id=run_id,
        run_id=run_id,
        stage="qa_smoke_completed",
        status="blocked_external_input" if smoke["external_inputs"] else "passed" if smoke["passed"] else "failed",
        payload={"request_id": request["request_id"], "smoke": smoke},
        db_path=db_path,
    )
    if smoke["external_inputs"]:
        first_input = smoke["external_inputs"][0]
        record_human_decision(
            decision_id=f"decision_{run_id}_external_input",
            run_id=run_id,
            project_id=request.get("project_id", ""),
            initiative_id=request.get("initiative_id", ""),
            source_agent="QA Execution",
            reason=first_input["reason"],
            question=(
                f"Para continuar ate o bot funcionar no Telegram, informe/configure {first_input['key']} "
                f"no workspace {workspace_root}."
            ),
            recommendation=first_input["instruction"],
            db_path=db_path,
        )
        update_run_status(
            run_id,
            status="human_escalation",
            operational_packet={
                **request.get("operational_packet", {}),
                "post_apply_status": "blocked_external_input",
                "product_acceptance_status": "blocked_external_input",
                "external_inputs": smoke["external_inputs"],
                "qa_smoke": smoke,
            },
            db_path=db_path,
        )
        record_workflow_checkpoint(
            thread_id=run_id,
            run_id=run_id,
            stage="external_input_required",
            status="pending_human_input",
            payload={"request_id": request["request_id"], "external_inputs": smoke["external_inputs"]},
            db_path=db_path,
        )
        return
    requires_functional_qa = (workspace_root / "bot.py").exists()
    packet = {
        **request.get("operational_packet", {}),
        "post_apply_status": "qa_smoke_passed" if smoke["passed"] else "qa_smoke_failed",
        "product_acceptance_status": (
            "functional_qa_pending"
            if smoke["passed"] and requires_functional_qa
            else "product_accepted"
            if smoke["passed"]
            else "functional_qa_failed"
        ),
        "qa_smoke": smoke,
    }
    update_run_status(
        run_id,
        status="return_requested" if smoke["passed"] and requires_functional_qa else "ended" if smoke["passed"] else "failed",
        operational_packet=packet,
        db_path=db_path,
    )
    if smoke["passed"] and requires_functional_qa:
        record_workflow_checkpoint(
            thread_id=run_id,
            run_id=run_id,
            stage="functional_qa_pending",
            status="pending_manual_validation",
            payload={
                "request_id": request["request_id"],
                "next_step": "Execute o roteiro funcional no canal real e registre a evidencia antes de concluir o objetivo.",
            },
            db_path=db_path,
        )


def _telegram_bot_files() -> dict[str, str]:
    bot_py = '''"""Telegram study bot generated by the Squad Implementation Operator."""

from __future__ import annotations

from enum import Enum
import os
from pathlib import Path
import re

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


BOT_CONTEXT_PATH = Path(__file__).with_name("BOT.txt")
MAX_HISTORY = 20
MAX_VISIBLE_LINES = 4
MAX_TELEGRAM_CHARS = 3900
SYSTEM_PROMPT = """Voce e um tutor pessoal no Telegram.
Use o metodo de aprendizagem descrito no arquivo BOT.txt.
Conduza somente a etapa solicitada. Responda em portugues com blocos curtos.
"""


def load_bot_context() -> str:
    if not BOT_CONTEXT_PATH.exists():
        raise RuntimeError("BOT.txt e obrigatorio para iniciar o tutor.")
    return BOT_CONTEXT_PATH.read_text(encoding="utf-8")[:12000]


class SessionStage(str, Enum):
    CONNECTION = "connection"
    CASE = "case"
    SOCRATIC = "socratic"
    SYNTHESIS = "synthesis"
    DECISION = "decision"


STAGE_ORDER = [
    SessionStage.CONNECTION,
    SessionStage.CASE,
    SessionStage.SOCRATIC,
    SessionStage.SYNTHESIS,
    SessionStage.DECISION,
]

SABOTAGE_PATTERNS = (
    r"\\bnao sei\\b", r"\\bnão sei\\b", r"\\bdepois\\b", r"\\boutro assunto\\b",
    r"\\bmudar de assunto\\b", r"\\bnao consigo\\b", r"\\bnão consigo\\b",
)


def new_session() -> dict:
    return {"stage": SessionStage.CONNECTION.value, "theme": "", "history": []}


def is_sabotage(text: str) -> bool:
    normalized = text.casefold()
    return any(re.search(pattern, normalized) for pattern in SABOTAGE_PATTERNS)


def advance_session(session: dict, user_text: str) -> tuple[SessionStage, str]:
    stage = SessionStage(session.get("stage", SessionStage.CONNECTION.value))
    if is_sabotage(user_text):
        return stage, "Vamos reduzir o passo. Responda com uma frase simples sobre o ponto que travou."
    if stage == SessionStage.CONNECTION:
        session["theme"] = user_text
    next_index = min(STAGE_ORDER.index(stage) + 1, len(STAGE_ORDER) - 1)
    session["stage"] = STAGE_ORDER[next_index].value
    return stage, ""


def split_messages(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    messages: list[str] = []
    current: list[str] = []
    for line in lines or [text.strip()]:
        if current and (len(current) >= MAX_VISIBLE_LINES or len("\\n".join([*current, line])) > MAX_TELEGRAM_CHARS):
            messages.append("\\n".join(current))
            current = []
        while len(line) > MAX_TELEGRAM_CHARS:
            messages.append(line[:MAX_TELEGRAM_CHARS])
            line = line[MAX_TELEGRAM_CHARS:]
        if line:
            current.append(line)
    if current:
        messages.append("\\n".join(current))
    return messages or ["Nao consegui montar a resposta. Tente reformular em uma frase."]


async def call_llm(user_text: str, history: list[str], stage: SessionStage) -> str:
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    model = os.getenv("LLM_MODEL", "")
    api_key = os.getenv("LLM_API_KEY", "")
    context = load_bot_context()
    prompt = (
        f"{SYSTEM_PROMPT}\\n\\nDocumento de referencia:\\n{context}\\n\\n"
        f"Etapa atual: {stage.value}\\nHistorico recente:\\n{chr(10).join(history[-MAX_HISTORY:])}\\n\\nAluno: {user_text}\\nTutor:"
    )

    if provider == "openai" and api_key:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key)
        response = await client.responses.create(
            model=model or "gpt-4.1-mini",
            input=prompt,
            max_output_tokens=700,
        )
        return response.output_text.strip()

    if provider == "anthropic" and api_key:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model or "claude-3-5-haiku-latest",
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        return "\\n".join(block.text for block in response.content if getattr(block, "text", "")).strip()

    mock_by_stage = {
        SessionStage.CONNECTION: "Tema registrado.\\nQual situacao concreta voce quer resolver primeiro?",
        SessionStage.CASE: "Vamos usar esse caso.\\nO que voce acredita que explica o problema?",
        SessionStage.SOCRATIC: "Boa hipotese.\\nQual evidencia confirmaria ou refutaria essa ideia?",
        SessionStage.SYNTHESIS: "Resuma em tres pontos.\\nDepois escolha uma acao para testar seu entendimento.",
        SessionStage.DECISION: "Ciclo concluido.\\nQual decisao pratica voce leva deste estudo?",
    }
    return mock_by_stage[stage]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    context.user_data.update(new_session())
    await update.message.reply_text(
        "Pronto. Qual assunto voce quer estudar neste ciclo?"
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    context.user_data.update(new_session())
    await update.message.reply_text("Ciclo reiniciado. Qual assunto vem agora?")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = context.user_data or new_session()
    await update.message.reply_text(
        f"Etapa atual: {session.get('stage', SessionStage.CONNECTION.value)}\\n"
        f"Tema: {session.get('theme') or 'a definir'}"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text.strip()
    if not context.user_data:
        context.user_data.update(new_session())
    history = context.user_data.setdefault("history", [])
    history.append(f"Aluno: {user_text}")
    del history[:-MAX_HISTORY]
    stage, intercept = advance_session(context.user_data, user_text)
    await update.message.chat.send_action(ChatAction.TYPING)
    if intercept:
        answer = intercept
    else:
        try:
            answer = await call_llm(user_text, history, stage)
        except Exception:
            answer = "Nao consegui consultar o tutor agora. Tente novamente em alguns instantes."
    history.append(f"Tutor: {answer}")
    del history[:-MAX_HISTORY]
    for message in split_messages(answer):
        await update.message.reply_text(message)


def main() -> None:
    load_dotenv()
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("Defina TELEGRAM_TOKEN no arquivo .env antes de iniciar.")

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()


if __name__ == "__main__":
    main()
'''
    test_bot = '''"""Static contract checks for the generated study bot."""

from pathlib import Path
import unittest


class GeneratedBotContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path("bot.py").read_text(encoding="utf-8")

    def test_conversation_state_machine_exists(self):
        for token in ("class SessionStage", "CONNECTION", "CASE", "SOCRATIC", "SYNTHESIS", "DECISION"):
            self.assertIn(token, self.source)

    def test_channel_and_history_limits_exist(self):
        for token in ("MAX_HISTORY", "MAX_VISIBLE_LINES", "MAX_TELEGRAM_CHARS", "split_messages"):
            self.assertIn(token, self.source)

    def test_recovery_and_reset_exist(self):
        for token in ("SABOTAGE_PATTERNS", "advance_session", "CommandHandler(\\"reset\\"", "CommandHandler(\\"status\\""):
            self.assertIn(token, self.source)

    def test_missing_context_fails_fast(self):
        self.assertIn("BOT.txt e obrigatorio", self.source)


if __name__ == "__main__":
    unittest.main()
'''
    requirements = """python-telegram-bot>=20.7
python-dotenv>=1.0.1
openai>=1.30.0
anthropic>=0.28.0
"""
    env_example = """TELEGRAM_TOKEN=
LLM_PROVIDER=mock
LLM_API_KEY=
LLM_MODEL=
"""
    gitignore = """.env
.venv/
__pycache__/
*.pyc
"""
    readme = """# Bot de estudos no Telegram

Bot criado pela Squad para conduzir estudos a partir do documento `BOT.txt`.

## Como rodar

1. Crie um bot no BotFather e copie o token.
2. Copie `.env.example` para `.env`.
3. Preencha `TELEGRAM_TOKEN`.
4. Opcionalmente configure `LLM_PROVIDER`, `LLM_API_KEY` e `LLM_MODEL`.
5. Instale dependencias:

```bash
pip install -r requirements.txt
```

6. Rode o contrato local:

```bash
python -m unittest test_bot -v
```

7. Inicie:

```bash
python bot.py
```

## Validacao

Abra o Telegram, envie `/start` e percorra um ciclo. Use `/status` para conferir
a etapa e `/reset` para reiniciar.
"""
    return {
        "bot.py": bot_py,
        "test_bot.py": test_bot,
        "requirements.txt": requirements,
        ".env.example": env_example,
        ".gitignore": gitignore,
        "README.md": readme,
    }


def generate_candidate_patch(request: dict[str, Any], *, workspace_root: Path) -> tuple[str, list[str]]:
    target_refs = _effective_target_refs_for_generation(request)
    supported_refs = [ref for ref in target_refs if ref in AUTO_PATCH_SUPPORTED_FILES]
    if not {"bot.py", "requirements.txt"}.intersection(supported_refs):
        return "", target_refs
    files = _telegram_bot_files()
    patches: list[str] = []
    for ref in ("bot.py", "test_bot.py", "requirements.txt", ".env.example", ".gitignore", "README.md"):
        if ref not in target_refs:
            continue
        target = workspace_root / ref
        if target.exists():
            before = target.read_text(encoding="utf-8", errors="replace")
            if before != files[ref]:
                patches.append(_render_existing_file_diff(ref, before, files[ref]))
            continue
        patches.append(_render_new_file_diff(ref, files[ref]))
    return "\n".join(patch.rstrip() for patch in patches) + ("\n" if patches else ""), target_refs


def _validate_patch(
    patch_text: str,
    *,
    workspace_root: Path,
    allowed_refs: set[str],
) -> dict[str, Any]:
    patch_refs = _patch_target_refs(patch_text)
    errors: list[str] = []
    if len(patch_text.encode("utf-8")) > MAX_PATCH_BYTES:
        errors.append("Candidate patch exceeds the 1 MB preparation limit.")
    if "GIT binary patch" in patch_text:
        errors.append("Binary patches are not accepted by the controlled executor.")
    if not patch_refs:
        errors.append("No file targets were found in the unified diff.")
    unapproved_refs = sorted(set(patch_refs).difference(allowed_refs))
    if unapproved_refs:
        errors.append(f"Patch includes unapproved target refs: {', '.join(unapproved_refs)}.")
    git_check = {"executed": False, "passed": False, "details": ""}
    if not errors:
        try:
            completed = subprocess.run(
                _git_apply_command(workspace_root, check=True),
                cwd=workspace_root,
                input=patch_text.encode("utf-8"),
                capture_output=True,
                check=False,
                timeout=10,
            )
            details = (completed.stderr or completed.stdout).decode(
                "utf-8", errors="replace"
            ).strip()
            git_check = {
                "executed": True,
                "passed": completed.returncode == 0,
                "details": details[:600],
            }
            if completed.returncode != 0:
                errors.append("Git rejected the candidate patch during apply --check.")
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
            errors.append(f"Unable to validate candidate patch with Git: {error}.")
    return {
        "passed": not errors,
        "patch_target_refs": patch_refs,
        "unapproved_target_refs": unapproved_refs,
        "git_apply_check": git_check,
        "errors": errors,
    }


def build_execution_request(
    result: dict[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    packet = result.get("operational_packet", {})
    policy = result.get("execution_policy", packet.get("execution_policy", {}))
    scope = result.get("work_scope", {})
    execution_ready = bool(packet.get("execution_ready", False))
    gate_required = requires_execution_gate(packet)
    requested_effects: list[str] = []
    if execution_ready or gate_required:
        requested_effects.append("write_files")
        if packet.get("git_actions"):
            requested_effects.append("git_commit")
    requires_approval = bool(requested_effects)
    return {
        "request_id": f"exec_{run_id}",
        "run_id": run_id,
        "project_id": scope.get("project_id", packet.get("project_id", "")),
        "initiative_id": scope.get("initiative_id", packet.get("initiative_id", "")),
        "status": PENDING_APPROVAL if execution_ready or gate_required else NOT_ACTIONABLE,
        "execution_mode": EXECUTION_MODE,
        "approval_required": requires_approval,
        "requested_effects": requested_effects,
        "target_refs": _request_target_refs(packet),
        "recommended_actions": packet.get("recommended_actions", []),
        "verification_steps": packet.get("verification_steps", []),
        "execution_policy": policy,
        "operational_packet": packet,
        "effects_enabled": False,
        "status_reason": (
            "Human approval is required before future workspace effects."
            if execution_ready
            else "Supervised file creation is required before QA can validate the product."
            if gate_required
            else "The operational packet is not eligible for execution."
        ),
    }


def create_execution_request(
    result: dict[str, Any],
    *,
    run_id: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    request = build_execution_request(result, run_id=run_id)
    record_execution_request(request, db_path=db_path)
    record_workflow_checkpoint(
        thread_id=run_id,
        run_id=run_id,
        stage="execution_request_created",
        status=request["status"],
        payload={"request_id": request["request_id"], "target_refs": request["target_refs"]},
        db_path=db_path,
    )
    return request


def decide_execution_request(
    request_id: str,
    *,
    decision: str,
    decided_by: str,
    notes: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    decided = record_execution_decision(
        request_id,
        decision=decision,
        decided_by=decided_by,
        notes=notes,
        db_path=db_path,
    )
    record_workflow_checkpoint(
        thread_id=decided["run_id"],
        run_id=decided["run_id"],
        stage="preparation_approval_decided",
        status=decided["status"],
        payload={"request_id": request_id, "decision": decision, "decided_by": decided_by},
        db_path=db_path,
    )
    return decided


def prepare_execution_request(
    request_id: str,
    *,
    patch_text: str = "",
    workspace_root: str | Path = ".",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    snapshot = execution_request_snapshot(request_id=request_id, db_path=db_path)
    if not snapshot["requests"]:
        raise ValueError("Execution request not found.")
    request = snapshot["requests"][0]
    if request["status"] not in {APPROVED_FOR_DRY_RUN, AWAITING_PATCH, AWAITING_APPLY_APPROVAL}:
        raise ValueError("Execution request must be approved before preparation.")
    root = Path(workspace_root).resolve()
    generated_patch = False
    if not patch_text:
        patch_text, generated_target_refs = generate_candidate_patch(request, workspace_root=root)
        if patch_text:
            generated_patch = True
            if generated_target_refs != request.get("target_refs", []):
                request = update_execution_request_target_refs(
                    request_id,
                    target_refs=generated_target_refs,
                    db_path=db_path,
                )
    target_evidence, allowed_refs = _workspace_target_evidence(
        request.get("target_refs", []),
        workspace_root=root,
    )
    if patch_text:
        validation = _validate_patch(patch_text, workspace_root=root, allowed_refs=allowed_refs)
        preparation_status = "patch_validated" if validation["passed"] else "patch_rejected"
        request_status = AWAITING_APPLY_APPROVAL if validation["passed"] else AWAITING_PATCH
        status_reason = (
            (
                "Implementation Operator generated and validated a candidate diff; human apply approval is required."
                if generated_patch
                else "Candidate diff validated; human apply approval is required."
            )
            if validation["passed"]
            else "Candidate diff rejected; submit a corrected scoped patch."
        )
    else:
        validation = {
            "passed": False,
            "patch_target_refs": [],
            "unapproved_target_refs": [],
            "git_apply_check": {"executed": False, "passed": False, "details": ""},
            "errors": ["A candidate unified diff is required before apply approval."],
        }
        preparation_status = AWAITING_PATCH
        request_status = AWAITING_PATCH
        status_reason = "Preparation approved; awaiting a scoped candidate diff."
    preparation = {
        "request_id": request_id,
        "request_status": request_status,
        "status_reason": status_reason,
        "preparation_status": preparation_status,
        "workspace_root": str(root),
        "patch_sha256": hashlib.sha256(patch_text.encode("utf-8")).hexdigest() if patch_text else "",
        "patch_text": patch_text,
        "target_evidence": target_evidence,
        "validation": {**validation, "generated_by_operator": generated_patch},
    }
    prepared = record_execution_preparation(preparation, db_path=db_path)
    record_workflow_checkpoint(
        thread_id=prepared["run_id"],
        run_id=prepared["run_id"],
        stage="candidate_patch_prepared",
        status=prepared["status"],
        payload={
            "request_id": request_id,
            "preparation_status": preparation_status,
            "patch_sha256": preparation["patch_sha256"],
            "validation": validation,
        },
        db_path=db_path,
    )
    return prepared


def decide_apply_execution(
    request_id: str,
    *,
    decision: str,
    decided_by: str,
    notes: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    decided = record_apply_decision(
        request_id,
        decision=decision,
        decided_by=decided_by,
        notes=notes,
        db_path=db_path,
    )
    record_workflow_checkpoint(
        thread_id=decided["run_id"],
        run_id=decided["run_id"],
        stage="apply_approval_decided",
        status=decided["status"],
        payload={"request_id": request_id, "decision": decision, "decided_by": decided_by},
        db_path=db_path,
    )
    return decided


def _run_command(command: list[str], *, workspace_root: Path, timeout: int = 120) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=workspace_root,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
        output = (completed.stdout + completed.stderr).strip()
        return {
            "command": command,
            "passed": completed.returncode == 0,
            "return_code": completed.returncode,
            "output": output[-2000:],
        }
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        return {
            "command": command,
            "passed": False,
            "return_code": None,
            "output": str(error),
        }


def _apply_patch_bytes(
    patch_text: str,
    *,
    workspace_root: Path,
    reverse: bool = False,
) -> dict[str, Any]:
    command = _git_apply_command(workspace_root, reverse=reverse)
    try:
        completed = subprocess.run(
            command,
            cwd=workspace_root,
            input=patch_text.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=20,
        )
        output = (completed.stderr or completed.stdout).decode(
            "utf-8", errors="replace"
        ).strip()
        return {"passed": completed.returncode == 0, "details": output[:1000]}
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        return {"passed": False, "details": str(error)}


def _record_application_with_checkpoint(
    application: dict[str, Any],
    *,
    stage: str,
    db_path: str | Path,
) -> dict[str, Any]:
    recorded = record_execution_application(application, db_path=db_path)
    record_workflow_checkpoint(
        thread_id=recorded["run_id"],
        run_id=recorded["run_id"],
        stage=stage,
        status=recorded["status"],
        payload={
            "request_id": application["request_id"],
            "application_status": application["application_status"],
            "validation": application.get("validation", {}),
            "rollback_reason": application.get("rollback_reason", ""),
        },
        db_path=db_path,
    )
    return recorded


def apply_execution_request(
    request_id: str,
    *,
    workspace_root: str | Path = ".",
    validations: list[str] | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    snapshot = execution_request_snapshot(request_id=request_id, db_path=db_path)
    if not snapshot["requests"]:
        raise ValueError("Execution request not found.")
    request = snapshot["requests"][0]
    retrying_validation_rollback = (
        request["status"] == ROLLED_BACK
        and request.get("application", {}).get("application_status") == "rolled_back_validation_failed"
    )
    if request["status"] != APPROVED_FOR_APPLY and not retrying_validation_rollback:
        raise ValueError("A validated patch requires apply approval before execution.")
    preparation = request.get("preparation")
    if not preparation or not preparation.get("validation", {}).get("passed"):
        raise ValueError("No validated candidate patch is available.")
    root = Path(workspace_root).resolve()
    if str(root) != preparation["workspace_root"]:
        raise ValueError("Workspace root differs from the prepared execution workspace.")
    patch_text = preparation["patch_text"]
    patch_digest = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
    if patch_digest != preparation["patch_sha256"]:
        raise ValueError("Candidate patch digest does not match the approved preparation.")
    target_evidence, allowed_refs = _workspace_target_evidence(
        request["target_refs"], workspace_root=root
    )
    revalidation = _validate_patch(patch_text, workspace_root=root, allowed_refs=allowed_refs)
    if not revalidation["passed"]:
        return _record_application_with_checkpoint(
            {
                "request_id": request_id,
                "request_status": AWAITING_PATCH,
                "status_reason": "Approved patch is no longer applicable; submit a new candidate diff.",
                "application_status": "pre_apply_validation_failed",
                "workspace_root": str(root),
                "patch_sha256": patch_digest,
                "effects_enabled": False,
                "validation": {"pre_apply": revalidation, "presets": []},
                "git_evidence": {"target_evidence": target_evidence},
            },
            stage="application_precheck_failed",
            db_path=db_path,
        )
    applied = _apply_patch_bytes(patch_text, workspace_root=root)
    if not applied["passed"]:
        return _record_application_with_checkpoint(
            {
                "request_id": request_id,
                "request_status": AWAITING_PATCH,
                "status_reason": "Patch application failed without recorded workspace effects.",
                "application_status": "apply_failed",
                "workspace_root": str(root),
                "patch_sha256": patch_digest,
                "effects_enabled": False,
                "validation": {"pre_apply": revalidation, "presets": []},
                "git_evidence": {"target_evidence": target_evidence, "apply": applied},
            },
            stage="application_failed",
            db_path=db_path,
        )
    requested_validations = validations or ["git_diff_check"]
    invalid_presets = sorted(set(requested_validations).difference(VALIDATION_PRESETS))
    if invalid_presets:
        rollback = _apply_patch_bytes(patch_text, workspace_root=root, reverse=True)
        raise ValueError(
            f"Validation presets are not allowed: {', '.join(invalid_presets)}. "
            f"Patch rollback passed={rollback['passed']}."
        )
    validation_results = [
        {
            "preset": preset,
            **_run_validation_preset(preset, workspace_root=root),
        }
        for preset in requested_validations
    ]
    all_passed = all(result["passed"] for result in validation_results)
    git_evidence = {
        "target_evidence": target_evidence,
        "apply": applied,
        "diff_stat": _diff_stat_evidence(workspace_root=root),
    }
    if not all_passed:
        rollback = _apply_patch_bytes(patch_text, workspace_root=root, reverse=True)
        return _record_application_with_checkpoint(
            {
                "request_id": request_id,
                "request_status": ROLLED_BACK,
                "status_reason": "Patch was rolled back after validation failure.",
                "application_status": "rolled_back_validation_failed",
                "workspace_root": str(root),
                "patch_sha256": patch_digest,
                "effects_enabled": False,
                "validation": {"pre_apply": revalidation, "presets": validation_results},
                "git_evidence": {**git_evidence, "rollback": rollback},
                "rollback_reason": "One or more approved validation presets failed.",
            },
            stage="application_rolled_back",
            db_path=db_path,
        )
    application = _record_application_with_checkpoint(
        {
            "request_id": request_id,
            "request_status": APPLIED_VALIDATED,
            "status_reason": "Patch applied and validation evidence recorded; Git delivery awaits human action.",
            "application_status": "applied_validated",
            "workspace_root": str(root),
            "patch_sha256": patch_digest,
            "effects_enabled": True,
            "validation": {"pre_apply": revalidation, "presets": validation_results},
            "git_evidence": git_evidence,
        },
        stage="application_validated",
        db_path=db_path,
    )
    smoke = _post_apply_smoke_check(application, workspace_root=root)
    _record_post_apply_continuation(application, workspace_root=root, smoke=smoke, db_path=db_path)
    return execution_request_snapshot(request_id=request_id, db_path=db_path)["requests"][0]


def _validated_branch_name(value: str, request_id: str) -> str:
    branch_name = value.strip() or f"squad/delivery-{request_id.removeprefix('exec_')[:24]}"
    if (
        len(branch_name) > 120
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch_name)
        or ".." in branch_name
        or "//" in branch_name
        or branch_name.endswith(("/", "."))
    ):
        raise ValueError("Branch name is invalid for supervised Git delivery.")
    return branch_name


def commit_git_delivery(
    request_id: str,
    *,
    workspace_root: str | Path = ".",
    branch_name: str = "",
    commit_message: str = "",
    remote_name: str = "origin",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    snapshot = execution_request_snapshot(request_id=request_id, db_path=db_path)
    if not snapshot["requests"]:
        raise ValueError("Execution request not found.")
    request = snapshot["requests"][0]
    if request["status"] != APPLIED_VALIDATED:
        raise ValueError("Only an applied and validated delivery can be committed.")
    application = request.get("application") or {}
    root = Path(workspace_root).resolve()
    if str(root) != application.get("workspace_root"):
        raise ValueError("Workspace root differs from the applied execution workspace.")
    branch = _validated_branch_name(branch_name, request_id)
    message = commit_message.strip()[:240] or f"Deliver {request.get('initiative_id') or request_id}"
    base_result = _run_command(["git", "branch", "--show-current"], workspace_root=root)
    if not base_result["passed"]:
        raise ValueError("Unable to identify the current Git branch.")
    base_branch = base_result["output"].strip()
    changed_result = _run_command(
        ["git", "diff", "--name-only", "--", *request["target_refs"]],
        workspace_root=root,
    )
    if not changed_result["passed"] or not changed_result["output"].strip():
        raise ValueError("No approved target changes are available to commit.")
    switch = _run_command(["git", "switch", "-c", branch], workspace_root=root)
    if not switch["passed"]:
        raise ValueError(f"Unable to create delivery branch: {switch['output']}")
    add = _run_command(["git", "add", "--", *request["target_refs"]], workspace_root=root)
    if not add["passed"]:
        raise ValueError(f"Unable to stage approved targets: {add['output']}")
    commit = _run_command(["git", "commit", "-m", message, "--", *request["target_refs"]], workspace_root=root)
    if not commit["passed"]:
        raise ValueError(f"Unable to create delivery commit: {commit['output']}")
    sha = _run_command(["git", "rev-parse", "HEAD"], workspace_root=root)
    if not sha["passed"]:
        raise ValueError("Commit was created but its SHA could not be recorded.")
    recorded = record_git_delivery(
        {
            "request_id": request_id,
            "request_status": GIT_COMMITTED,
            "status_reason": "Branch and commit created; publication and draft PR require human confirmation.",
            "delivery_status": GIT_COMMITTED,
            "workspace_root": str(root),
            "branch_name": branch,
            "base_branch": base_branch,
            "commit_sha": sha["output"].strip(),
            "commit_message": message,
            "remote_name": remote_name.strip()[:80] or "origin",
            "evidence": {"changed_targets": changed_result, "switch": switch, "add": add, "commit": commit},
        },
        db_path=db_path,
    )
    record_workflow_checkpoint(
        thread_id=recorded["run_id"],
        run_id=recorded["run_id"],
        stage="git_commit_created",
        status=recorded["status"],
        payload={"request_id": request_id, "branch_name": branch, "commit_sha": sha["output"].strip()},
        db_path=db_path,
    )
    return recorded


def publish_git_delivery(
    request_id: str,
    *,
    workspace_root: str | Path = ".",
    pr_title: str = "",
    pr_body: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    snapshot = execution_request_snapshot(request_id=request_id, db_path=db_path)
    if not snapshot["requests"]:
        raise ValueError("Execution request not found.")
    request = snapshot["requests"][0]
    delivery = request.get("git_delivery")
    if request["status"] != GIT_COMMITTED or not delivery:
        raise ValueError("Create the supervised commit before publishing a draft PR.")
    root = Path(workspace_root).resolve()
    if str(root) != delivery["workspace_root"]:
        raise ValueError("Workspace root differs from the committed delivery workspace.")
    title = pr_title.strip()[:240] or delivery["commit_message"]
    body = pr_body.strip()[:8000] or "Entrega gerada pela squad e aguardando revisao humana."
    push = _run_command(
        ["git", "push", "-u", delivery["remote_name"], delivery["branch_name"]],
        workspace_root=root,
        timeout=180,
    )
    if not push["passed"]:
        raise ValueError(f"Unable to publish delivery branch: {push['output']}")
    pr = _run_command(
        [
            "gh", "pr", "create", "--draft", "--base", delivery["base_branch"],
            "--head", delivery["branch_name"], "--title", title, "--body", body,
        ],
        workspace_root=root,
        timeout=180,
    )
    if not pr["passed"]:
        raise ValueError(f"Branch published, but draft PR creation failed: {pr['output']}")
    match = re.search(r"https?://\S+", pr["output"])
    recorded = record_git_delivery(
        {
            "request_id": request_id,
            "request_status": PR_OPENED,
            "status_reason": "Draft PR published; merge remains a human decision.",
            "delivery_status": PR_OPENED,
            "workspace_root": str(root),
            "branch_name": delivery["branch_name"],
            "base_branch": delivery["base_branch"],
            "commit_sha": delivery["commit_sha"],
            "commit_message": delivery["commit_message"],
            "remote_name": delivery["remote_name"],
            "pr_url": match.group(0) if match else "",
            "evidence": {**delivery.get("evidence", {}), "push": push, "draft_pr": pr},
        },
        db_path=db_path,
    )
    record_workflow_checkpoint(
        thread_id=recorded["run_id"],
        run_id=recorded["run_id"],
        stage="draft_pr_published",
        status=recorded["status"],
        payload={"request_id": request_id, "pr_url": recorded["git_delivery"]["pr_url"]},
        db_path=db_path,
    )
    return recorded


def rollback_execution_request(
    request_id: str,
    *,
    workspace_root: str | Path = ".",
    reason: str = "Human requested rollback.",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    snapshot = execution_request_snapshot(request_id=request_id, db_path=db_path)
    if not snapshot["requests"]:
        raise ValueError("Execution request not found.")
    request = snapshot["requests"][0]
    if request["status"] != APPLIED_VALIDATED:
        raise ValueError("Only applied and validated executions can be rolled back.")
    preparation = request.get("preparation")
    root = Path(workspace_root).resolve()
    if not preparation or str(root) != preparation["workspace_root"]:
        raise ValueError("Workspace root differs from the applied execution workspace.")
    rollback = _apply_patch_bytes(preparation["patch_text"], workspace_root=root, reverse=True)
    if not rollback["passed"]:
        raise ValueError(f"Rollback could not be applied: {rollback['details']}")
    application = request.get("application", {})
    return _record_application_with_checkpoint(
        {
            "request_id": request_id,
            "request_status": ROLLED_BACK,
            "status_reason": "Applied patch rolled back by human request.",
            "application_status": "rolled_back",
            "workspace_root": str(root),
            "patch_sha256": preparation["patch_sha256"],
            "effects_enabled": False,
            "validation": application.get("validation", {}),
            "git_evidence": {**application.get("git_evidence", {}), "rollback": rollback},
            "rollback_reason": reason,
        },
        stage="application_rolled_back",
        db_path=db_path,
    )


def list_execution_requests(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    return execution_request_snapshot(db_path=db_path)


def _record_executor_checkpoint(
    run_id: str,
    adapter_name: str,
    result: Any,
    db_path: str | Path,
) -> None:
    if not run_id:
        return
    try:
        record_workflow_checkpoint(
            thread_id=run_id,
            run_id=run_id,
            stage="executor_run",
            status="success" if result.success else "failed",
            payload={
                "executor_used": adapter_name,
                "files_changed": result.files_changed,
                "tokens_used": result.tokens_used,
                "success": result.success,
            },
            db_path=db_path,
        )
    except Exception:
        pass


def invoke_executor(
    task_spec_dict: dict[str, Any],
    workspace_path: str | Path,
    policy: dict[str, Any],
    *,
    run_id: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> tuple[Any, str]:
    """Select an adapter and execute the task.

    Returns (ExecutionResult, adapter_name).
    Never raises — all errors are captured in ExecutionResult(success=False).
    """
    import dataclasses
    from app.executor.base import ExecutionResult, TaskSpec
    from app.executor.registry import select_adapter

    workspace = Path(workspace_path)

    # Build TaskSpec from dict, ignoring unknown fields
    try:
        valid_fields = {f.name for f in dataclasses.fields(TaskSpec)}
        filtered = {k: v for k, v in task_spec_dict.items() if k in valid_fields}
        spec = TaskSpec(**filtered)
    except Exception as exc:
        result = ExecutionResult(success=False, errors=f"Invalid task spec: {exc}", exit_code=-1)
        _record_executor_checkpoint(run_id, "", result, db_path)
        return result, ""

    # Select adapter
    adapter_name = ""
    try:
        adapter = select_adapter(spec, policy)
        adapter_name = adapter.name()
    except RuntimeError as exc:
        result = ExecutionResult(success=False, errors=str(exc), exit_code=-1)
        _record_executor_checkpoint(run_id, "", result, db_path)
        return result, ""

    # Execute (never raises per adapter contract)
    result = adapter.execute(spec, workspace)

    # Run validation commands if execution succeeded
    if result.success and spec.validation_commands:
        for cmd_str in spec.validation_commands:
            cmd = cmd_str.split() if isinstance(cmd_str, str) else list(cmd_str)
            validation = _run_command(cmd, workspace_root=workspace)
            if not validation["passed"]:
                _run_command(["git", "checkout", "--", "."], workspace_root=workspace)
                result = ExecutionResult(
                    success=False,
                    errors=(
                        f"Validation command {cmd_str!r} failed "
                        f"(exit {validation.get('return_code')}): "
                        f"{validation['output'][:400]}"
                    ),
                    exit_code=int(validation.get("return_code") or 1),
                    files_changed=result.files_changed,
                )
                break

    _record_executor_checkpoint(run_id, adapter_name, result, db_path)
    return result, adapter_name
