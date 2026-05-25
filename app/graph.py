from pathlib import Path
from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, START, END
from app.state import SquadState
from app.config import ANTHROPIC_API_KEY
from app.memory import read_shared_memory
from app.config import USE_MOCK_MODEL
from app.mock_model import MockModel
from app.handoff_log import append_handoff
from app.context_policy import build_agent_context
from app.cycle_report import append_cycle_report
from app.cos_decision import (
    validate_decision_route_consistency,
)
from app.routing import resolve_route
from app.retry_policy import resolve_retry_permission
from app.route_controller import decide_next_step, get_route_summary
from app.route_events import append_route_event
from app.orchestrator import (
    build_cmo_block,
    format_orchestrator_checks,
    update_orchestrator_checks,
)
from app.structured_output import (
    CoSOutputEnvelope,
    safe_parse_agent_output,
    with_output_contract,
)
from app.intake import decide_intake
from app.workspace_context import capture_workspace_context, format_workspace_context
from app.human_escalation import (
    append_human_escalation,
    should_create_human_escalation,
    build_escalation_reason,
    build_required_decision,
)



BASE_DIR = Path(__file__).resolve().parent

if USE_MOCK_MODEL:
    model = MockModel()
else:
    model = init_chat_model("claude-sonnet-4-6", temperature=0)


def read_prompt(name: str) -> str:
    return (BASE_DIR / "prompts" / name).read_text(encoding="utf-8")


class ValidatedResponse:
    def __init__(
        self,
        raw_content: str,
        envelope,
        validation_errors: list[str],
        *,
        repair_attempted: bool = False,
    ):
        self.raw_content = raw_content
        self.envelope = envelope
        self.validation_errors = validation_errors
        self.repair_attempted = repair_attempted
        self.content = envelope.artifact_markdown


def invoke_validated(agent_name: str, prompt: str) -> ValidatedResponse:
    raw_response = model.invoke(with_output_contract(prompt, agent_name))
    envelope, validation_errors = safe_parse_agent_output(raw_response.content, agent_name)
    if not validation_errors:
        return ValidatedResponse(raw_response.content, envelope, [])

    compact_errors = " | ".join(" ".join(error.split())[:500] for error in validation_errors)
    repair_prompt = (
        f"{with_output_contract(prompt, agent_name)}\n\n"
        "CORRECAO DE SCHEMA OBRIGATORIA:\n"
        "A resposta anterior foi rejeitada pelo runtime. Preserve o conteudo util do "
        "artefato, mas corrija estritamente o JSON conforme o contrato acima.\n"
        f"Erros de validacao: {compact_errors}\n\n"
        "Resposta anterior a corrigir:\n"
        f"{raw_response.content[:5000]}"
    )
    repaired_response = model.invoke(repair_prompt)
    repaired_envelope, repaired_errors = safe_parse_agent_output(repaired_response.content, agent_name)
    combined_raw = (
        f"INITIAL_ATTEMPT:\n{raw_response.content}\n\n"
        f"REPAIR_ATTEMPT:\n{repaired_response.content}"
    )
    return ValidatedResponse(
        combined_raw,
        repaired_envelope,
        repaired_errors,
        repair_attempted=True,
    )

def format_agent_context(agent_name: str) -> str:
    """
    Monta o bloco de contexto que será enviado ao agente.
    """

    context = build_agent_context(agent_name)
  
    return (
        f"Context Mode:\n{context['context_mode']}\n\n"
        f"Context Policy:\n{context['context_policy']}\n\n"
        f"Retry Policy:\n{context.get('retry_policy', '')}\n\n"
        f"Compact Memory da Squad:\n{context['compact_memory']}\n\n"
        f"Shared Memory da Squad:\n{context['shared_memory']}\n\n"
        f"Decision Log da Squad:\n{context['decision_log']}\n\n"
        f"Handoff Log da Squad:\n{context['handoff_log']}\n\n"
    )


def workspace_context_block(state: SquadState) -> str:
    return f"Workspace Context:\n{format_workspace_context(state.get('workspace_context'))}\n\n"


def cmo_block(
    state: SquadState,
    agent_name: str,
    task: str,
    primary_input: str,
    expected_output: str,
) -> str:
    return build_cmo_block(
        agent_name=agent_name,
        user_goal=state["user_goal"],
        task=task,
        primary_input=primary_input,
        expected_output=expected_output,
    )


def with_orchestrator_check(
    state: SquadState,
    agent_name: str,
    response: ValidatedResponse,
    payload: dict,
) -> dict:
    checks = update_orchestrator_checks(
        state,
        agent_name=agent_name,
        output=response.envelope,
        validation_errors=response.validation_errors,
    )
    checks[agent_name]["repair_attempted"] = response.repair_attempted
    return {
        **payload,
        "orchestrator_checks": checks,
        "structured_outputs": {
            **state.get("structured_outputs", {}),
            agent_name: response.envelope.model_dump(mode="json"),
        },
        "raw_model_outputs": {
            **state.get("raw_model_outputs", {}),
            agent_name: response.raw_content,
        },
    }


def build_operational_packet(
    state: SquadState,
    response: ValidatedResponse,
    *,
    decision: str,
    route_action: str,
) -> dict:
    artifact = response.envelope.operational_artifact
    workspace = state.get("workspace_context", {})
    artifacts = sorted({*state.get("structured_outputs", {}).keys(), "cos"})
    return {
        "active_flow": state.get("active_flow", ""),
        "decision": decision,
        "route_action": route_action,
        "workspace_root": workspace.get("root", ""),
        "git_repo": workspace.get("git_repo", False),
        "git_branch": workspace.get("git_branch", ""),
        "final_artifact_type": artifact.artifact_type,
        "target_refs": artifact.target_refs,
        "recommended_actions": artifact.recommended_actions,
        "verification_steps": artifact.verification_steps,
        "git_actions": artifact.git_actions if workspace.get("git_repo", False) else [],
        "documentation_actions": artifact.documentation_actions,
        "execution_ready": artifact.execution_ready,
        "human_checkpoint": artifact.human_checkpoint,
        "supporting_artifacts": artifacts,
    }


def next_after_check(state: SquadState, agent_name: str, next_node: str) -> str:
    check = state.get("orchestrator_checks", {}).get(agent_name, {})
    if check.get("needs_cos_attention", False):
        return "cos"
    return next_node


def intake_node(state: SquadState):
    override = state.get("needs_discovery") if "needs_discovery" in state else None
    intake = decide_intake(
        state["user_goal"],
        needs_discovery_override=override,
        active_flow_override=state.get("active_flow"),
    ).as_state()
    context = capture_workspace_context(state.get("workspace_root"))
    return {
        **intake,
        "workspace_root": context.root,
        "workspace_context": context.as_state(),
    }


def route_after_intake(state: SquadState) -> str:
    active_flow = state.get("active_flow", "delivery_core")
    first_node_by_flow = {
        "delivery_core": "product",
        "delivery_with_discovery": "discovery",
        "bugfix": "engineering",
        "docs": "writing",
        "review": "engineering_review",
        "decision_only": "product",
        "research_only": "discovery",
    }
    return first_node_by_flow.get(active_flow, "product")

def discovery_node(state: SquadState):
    prompt = read_prompt("discovery.txt")
    run_id = state.get("run_id", "")
    cmo = cmo_block(
        state,
        "discovery",
        "Levantar evidencias e lacunas relevantes ao objetivo.",
        "Objetivo inicial recebido do CEO ou CoS.",
        "Discovery com fontes, lacunas, ECE e Resumo Estruturado.",
    )

    response = invoke_validated("discovery",
        f"[[AGENT:DISCOVERY]]\n\n"
        f"{prompt}\n\n"
        f"{cmo}\n\n"
        f"Objetivo do projeto:\n{state['user_goal']}\n\n"
        f"{workspace_context_block(state)}"
    )
    
    append_handoff(
        run_id=run_id,
        from_agent="Discovery",
        to_agent="Product Lead",
        artifact="Discovery / Market Research",
        summary=response.content[:500],
        ece="C2/C3",
        blockers="Ausência de fontes verificadas no modo mock ou quando não houver contexto externo.",
        next_step="Product Lead deve transformar o discovery em hipótese e escopo preliminar.",
        escalate_to_cos="Não."
    )


    return with_orchestrator_check(state, "discovery", response, {
        "discovery_output": response.content,
        "confidence_by_agent": {
            **state.get("confidence_by_agent", {}),
            "discovery": "C2"
        },
        "summaries_by_agent": {
            **state.get("summaries_by_agent", {}),
            "discovery": response.content[:300]
        }
    })


def writing_node(state: SquadState):
    prompt = read_prompt("writing.txt")
    run_id = state.get("run_id", "")
    context = format_agent_context("writing")
    cmo = cmo_block(
        state,
        "writing",
        "Organizar o artefato documental solicitado sem alterar escopo.",
        state.get("user_goal", ""),
        "Documento claro com decisoes, pendencias, ECE e resumo.",
    )

    response = invoke_validated("writing",
        f"[[AGENT:WRITING]]\n\n"
        f"{prompt}\n\n"
        f"{cmo}\n\n"
        f"Objetivo do projeto:\n{state['user_goal']}\n\n"
        f"{workspace_context_block(state)}"
        f"{context}"
    )

    append_handoff(
        run_id=run_id,
        from_agent="Writing / Documentation",
        to_agent="CoS / Orchestrator",
        artifact="Writing Artifact",
        summary=response.content[:500],
        ece="C1/C2",
        blockers="Nenhum bloqueio documental informado.",
        next_step="CoS deve decidir registro, distribuicao ou novo ajuste.",
        escalate_to_cos="Sim. Fechamento do fluxo documental."
    )

    return with_orchestrator_check(state, "writing", response, {
        "writing_output": response.content,
        "confidence_by_agent": {
            **state.get("confidence_by_agent", {}),
            "writing": "C1"
        },
        "summaries_by_agent": {
            **state.get("summaries_by_agent", {}),
            "writing": response.content[:300]
        }
    })


def ux_ui_node(state: SquadState):
    prompt = read_prompt("ux_ui.txt")
    product = state.get("product_output", "")
    run_id = state.get("run_id", "")
    response = invoke_validated("ux_ui",
        f"[[AGENT:UX_UI]]\n\n"
        f"{prompt}\n\n"
        f"{cmo_block(state, 'ux_ui', 'Revisar experiencia e estados da entrega.', product, 'Gate UX/UI com criterios verificaveis, ECE e resumo.')}\n\n"
        f"Objetivo do projeto:\n{state['user_goal']}\n\n"
        f"{workspace_context_block(state)}"
        f"Product Brief:\n{product}"
    )
    append_handoff(
        run_id=run_id,
        from_agent="UX/UI Lead",
        to_agent="QA Planning",
        artifact="UX/UI Gate",
        summary=response.content[:500],
        ece="C1/C2",
        blockers="Bloqueios de experiencia devem ser incorporados aos criterios de aceite.",
        next_step="QA Planning deve cobrir estados e friccoes sinalizadas.",
        escalate_to_cos="Somente se houver output C3."
    )
    return with_orchestrator_check(state, "ux_ui", response, {
        "ux_ui_output": response.content,
        "confidence_by_agent": {**state.get("confidence_by_agent", {}), "ux_ui": response.envelope.summary.ece},
        "summaries_by_agent": {**state.get("summaries_by_agent", {}), "ux_ui": response.content[:300]},
    })


def privacy_node(state: SquadState):
    prompt = read_prompt("privacy.txt")
    engineering = state.get("engineering_output", "")
    engineering_review = state.get("engineering_review_output", "")
    review_source = engineering or engineering_review or state["user_goal"]
    run_id = state.get("run_id", "")
    response = invoke_validated("privacy",
        f"[[AGENT:PRIVACY]]\n\n"
        f"{prompt}\n\n"
        f"{cmo_block(state, 'privacy', 'Revisar riscos de dados no fluxo ativo.', review_source, 'Gate Privacy com riscos, controles, ECE e resumo.')}\n\n"
        f"Objetivo do projeto:\n{state['user_goal']}\n\n"
        f"{workspace_context_block(state)}"
        f"Engineering Specification:\n{engineering}\n\n"
        f"Engineering Review:\n{engineering_review}"
    )
    append_handoff(
        run_id=run_id,
        from_agent="Privacy & Compliance",
        to_agent="AppSec / Implementation Operator",
        artifact="Privacy Gate",
        summary=response.content[:500],
        ece="C1/C2/C3",
        blockers="Riscos C3 de dados impedem implementacao automatica.",
        next_step="Continuar somente respeitando controles e limitacoes registradas.",
        escalate_to_cos="Sim quando C3."
    )
    return with_orchestrator_check(state, "privacy", response, {
        "privacy_output": response.content,
        "confidence_by_agent": {**state.get("confidence_by_agent", {}), "privacy": response.envelope.summary.ece},
        "summaries_by_agent": {**state.get("summaries_by_agent", {}), "privacy": response.content[:300]},
    })


def appsec_node(state: SquadState):
    prompt = read_prompt("appsec.txt")
    engineering = state.get("engineering_output", "")
    engineering_review = state.get("engineering_review_output", "")
    privacy = state.get("privacy_output", "")
    review_source = engineering or engineering_review or state["user_goal"]
    run_id = state.get("run_id", "")
    response = invoke_validated("appsec",
        f"[[AGENT:APPSEC]]\n\n"
        f"{prompt}\n\n"
        f"{cmo_block(state, 'appsec', 'Revisar seguranca no fluxo ativo.', review_source, 'Gate AppSec com ameacas, controles, ECE e resumo.')}\n\n"
        f"Objetivo do projeto:\n{state['user_goal']}\n\n"
        f"{workspace_context_block(state)}"
        f"Engineering Specification:\n{engineering}\n\n"
        f"Engineering Review:\n{engineering_review}\n\n"
        f"Privacy Gate:\n{privacy}"
    )
    append_handoff(
        run_id=run_id,
        from_agent="AppSec / Security",
        to_agent="Implementation Operator",
        artifact="AppSec Gate",
        summary=response.content[:500],
        ece="C1/C2/C3",
        blockers="Riscos C3 de seguranca impedem implementacao automatica.",
        next_step="Operator deve incorporar controles aprovados ao pacote.",
        escalate_to_cos="Sim quando C3."
    )
    return with_orchestrator_check(state, "appsec", response, {
        "appsec_output": response.content,
        "confidence_by_agent": {**state.get("confidence_by_agent", {}), "appsec": response.envelope.summary.ece},
        "summaries_by_agent": {**state.get("summaries_by_agent", {}), "appsec": response.content[:300]},
    })


def product_node(state: SquadState):
    prompt = read_prompt("product.txt")
    discovery = state.get("discovery_output", "")
    agent_context = format_agent_context("product")
    run_id = state.get("run_id", "")
    cmo = cmo_block(
        state,
        "product",
        "Transformar Discovery em escopo funcional testavel.",
        discovery,
        "Product Brief com MVP Sujo, EME, ECE e Resumo Estruturado.",
    )

    response = invoke_validated("product",
        f"[[AGENT:PRODUCT]]\n\n"
        f"{prompt}\n\n"
        f"{cmo}\n\n"
        f"Objetivo do projeto:\n{state['user_goal']}\n\n"
        f"{workspace_context_block(state)}"
        f"{agent_context}"
        f"Discovery:\n{discovery}"
    )

    append_handoff(
        run_id=run_id,
        from_agent="Product Lead",
        to_agent="QA Planning",
        artifact="Product Brief",
        summary=response.content[:500],
        ece="C2",
        blockers="Engineering completa bloqueada se houver dependências C3 críticas.",
        next_step="QA Planning deve avaliar testabilidade e definir Go/No-Go.",
        escalate_to_cos="Não."
    )

    return with_orchestrator_check(state, "product", response, {
        "product_output": response.content,
        "confidence_by_agent": {
            **state.get("confidence_by_agent", {}),
            "product": "C2"
        },
        "summaries_by_agent": {
            **state.get("summaries_by_agent", {}),
            "product": response.content[:300]
        }
    })


def qa_planning_node(state: SquadState):
    prompt = read_prompt("qa.txt")
    product = state.get("product_output", "")
    discovery = state.get("discovery_output", "")
    ux_ui = state.get("ux_ui_output", "")
    run_id = state.get("run_id", "")
    cmo = cmo_block(
        state,
        "qa_planning",
        "Converter escopo em criterios e test cases minimos.",
        product,
        "Plano QA com casos, EME, go/no-go, ECE e resumo.",
    )

    response = invoke_validated("qa_planning",
        f"[[AGENT:QA_PLANNING]]\n\n"
        f"{prompt}\n\n"
        f"{cmo}\n\n"
        f"Objetivo do projeto:\n{state['user_goal']}\n\n"
        f"{workspace_context_block(state)}"
        f"Discovery:\n{discovery}\n\n"
        f"Product Brief:\n{product}\n\n"
        f"UX/UI Gate:\n{ux_ui}"
    )

    append_handoff(
        run_id=run_id,
        from_agent="QA Planning",
        to_agent="Engineering Lead",
        artifact="QA Planning",
        summary=response.content[:500],
        ece="C2",
        blockers="Dependências críticas da Engineering podem impactar a testabilidade.",
        next_step="QA Planning deve definir os critérios de aprovação.",
        escalate_to_cos="Não."
    )

    return with_orchestrator_check(state, "qa_planning", response, {
        "qa_plan_output": response.content,
        "confidence_by_agent": {
            **state.get("confidence_by_agent", {}),
            "qa_planning": "C2"
        },
        "summaries_by_agent": {
            **state.get("summaries_by_agent", {}),
            "qa_planning": response.content[:300]
        }
    })


def engineering_node(state: SquadState):
    prompt = read_prompt("engineering.txt")
    discovery = state.get("discovery_output", "")
    product = state.get("product_output", "")
    qa_plan = state.get("qa_plan_output", "")
    ux_ui = state.get("ux_ui_output", "")
    agent_context = format_agent_context("engineering")
    run_id = state.get("run_id", "")
    cmo = cmo_block(
        state,
        "engineering",
        "Produzir especificacao tecnica implementavel e proporcional.",
        f"Product Brief: {product}\nQA Planning: {qa_plan}",
        "Spec tecnica com trade-offs, 3 Desastres, ECE e resumo.",
    )

    response = invoke_validated("engineering",
        f"[[AGENT:ENGINEERING]]\n\n"
        f"{prompt}\n\n"
        f"{cmo}\n\n"
        f"Objetivo do projeto:\n{state['user_goal']}\n\n"
        f"{workspace_context_block(state)}"
        f"{agent_context}"
        f"Discovery:\n{discovery}\n\n"
        f"Product Brief:\n{product}\n\n"
        f"QA Planning:\n{qa_plan}\n\n"
        f"UX/UI Gate:\n{ux_ui}"
    )

    append_handoff(
        run_id=run_id,
        from_agent="Engineering Lead",
        to_agent="Implementation Operator",
        artifact="Engineering Specification",
        summary=response.content[:500],
        ece="C1/C2",
        blockers="Nenhum bloqueio para MVP Sujo quando o escopo técnico estiver restrito.",
        next_step="Implementation Operator deve gerar pacote operacional para Claude Code.",
        escalate_to_cos="Não."
    )

    return with_orchestrator_check(state, "engineering", response, {
        "engineering_output": response.content,
        "confidence_by_agent": {
            **state.get("confidence_by_agent", {}),
            "engineering": "C2"
        },
        "summaries_by_agent": {
            **state.get("summaries_by_agent", {}),
            "engineering": response.content[:300]
        }
    })


def operator_node(state: SquadState):
    prompt = read_prompt("operator.txt")
    engineering = state.get("engineering_output", "")
    qa_plan = state.get("qa_plan_output", "")
    product = state.get("product_output", "")
    privacy = state.get("privacy_output", "")
    appsec = state.get("appsec_output", "")
    run_id = state.get("run_id", "")
    cmo = cmo_block(
        state,
        "operator",
        "Gerar pacote de implementacao fiel a spec e aos test cases.",
        f"Engineering: {engineering}\nQA Planning: {qa_plan}",
        "Pacote para Claude Code com restricoes, ECE e resumo.",
    )

    response = invoke_validated("operator",
        f"[[AGENT:OPERATOR]]\n\n"
        f"{prompt}\n\n"
        f"{cmo}\n\n"
        f"Objetivo do projeto:\n{state['user_goal']}\n\n"
        f"{workspace_context_block(state)}"
        f"Product Brief:\n{product}\n\n"
        f"QA Planning:\n{qa_plan}\n\n"
        f"Engineering Specification:\n{engineering}\n\n"
        f"Privacy Gate:\n{privacy}\n\n"
        f"AppSec Gate:\n{appsec}"
    )

    append_handoff(
        run_id=run_id,
        from_agent="Implementation Operator",
        to_agent="Engineering Review",
        artifact="Implementation Package",
        summary=response.content[:500],
        ece="C2",
        blockers="Nenhum bloqueio para MVP Sujo quando o escopo técnico estiver restrito.",
        next_step="Engineering Lead deve revisar aderencia antes do QA Execution.",
        escalate_to_cos="Não."
    )

    return with_orchestrator_check(state, "operator", response, {
        "operator_output": response.content,
        "confidence_by_agent": {
            **state.get("confidence_by_agent", {}),
            "operator": "C2"
        },
        "summaries_by_agent": {
            **state.get("summaries_by_agent", {}),
            "operator": response.content[:300]
        }
    })

def engineering_review_node(state: SquadState):
    prompt = read_prompt("engineering_review.txt")
    product = state.get("product_output", "")
    qa_plan = state.get("qa_plan_output", "")
    engineering = state.get("engineering_output", "")
    operator = state.get("operator_output", "")
    privacy = state.get("privacy_output", "")
    appsec = state.get("appsec_output", "")
    run_id = state.get("run_id", "")
    review_input = (
        f"Engineering Specification: {engineering}\nOperator Package: {operator}"
        if operator or engineering
        else f"Review solicitado no intake: {state['user_goal']}"
    )
    cmo = cmo_block(
        state,
        "engineering_review",
        "Revisar o artefato tecnico disponivel para o fluxo ativo.",
        review_input,
        "Engineering Review com parecer, riscos, ECE e resumo.",
    )

    response = invoke_validated("engineering_review",
        f"[[AGENT:ENGINEERING_REVIEW]]\n\n"
        f"{prompt}\n\n"
        f"{cmo}\n\n"
        f"Objetivo do projeto:\n{state['user_goal']}\n\n"
        f"{workspace_context_block(state)}"
        f"Product Brief:\n{product}\n\n"
        f"QA Planning:\n{qa_plan}\n\n"
        f"Engineering Specification:\n{engineering}\n\n"
        f"Implementation Operator Package:\n{operator}\n\n"
        f"Privacy Gate:\n{privacy}\n\n"
        f"AppSec Gate:\n{appsec}"
    )

    append_handoff(
        run_id=run_id,
        from_agent="Engineering Review",
        to_agent="QA Execution",
        artifact="Engineering Review",
        summary=response.content[:500],
        ece="C1/C2",
        blockers="QA deve respeitar ressalvas tecnicas registradas no review.",
        next_step="QA Execution deve validar build e evidencias conforme o plano.",
        escalate_to_cos="NÃ£o."
    )

    return with_orchestrator_check(state, "engineering_review", response, {
        "engineering_review_output": response.content,
        "confidence_by_agent": {
            **state.get("confidence_by_agent", {}),
            "engineering_review": "C2"
        },
        "summaries_by_agent": {
            **state.get("summaries_by_agent", {}),
            "engineering_review": response.content[:300]
        }
    })


def qa_execution_node(state: SquadState):
    prompt = read_prompt("qa_execution.txt")
    qa_plan = state.get("qa_plan_output", "")
    engineering = state.get("engineering_output", "")
    operator = state.get("operator_output", "")
    engineering_review = state.get("engineering_review_output", "")
    privacy = state.get("privacy_output", "")
    appsec = state.get("appsec_output", "")
    validation = state.get("manual_validation_result", "")
    agent_context = format_agent_context("qa_execution")
    run_id = state.get("run_id", "")
    cmo = cmo_block(
        state,
        "qa_execution",
        "Comparar resultado validado com plano, spec, pacote e review.",
        validation,
        "Relatorio QA com EME, bugs, go/no-go, ECE e resumo.",
    )

    response = invoke_validated("qa_execution",
        f"[[AGENT:QA_EXECUTION]]\n\n"
        f"{prompt}\n\n"
        f"{cmo}\n\n"
        f"Objetivo do projeto:\n{state['user_goal']}\n\n"
        f"{workspace_context_block(state)}"
        f"{agent_context}"
        f"QA Planning:\n{qa_plan}\n\n"
        f"Engineering Specification:\n{engineering}\n\n"
        f"Implementation Operator Package:\n{operator}\n\n"
        f"Engineering Review:\n{engineering_review}\n\n"
        f"Privacy Gate:\n{privacy}\n\n"
        f"AppSec Gate:\n{appsec}\n\n"
        f"Resultado da validação manual:\n{validation}"
    )

    append_handoff(
        run_id=run_id,
        from_agent="QA Execution",
        to_agent="CoS / Orchestrator",
        artifact="QA Execution Report",
        summary=response.content[:500],
        ece="C1",
        blockers="B-001 e B-002 podem permanecer abertos dependendo do ciclo.",
        next_step="CoS deve avaliar coerência do ciclo, bloqueios e decisão de roteamento.",
        escalate_to_cos="Sim. Avaliação final do ciclo é responsabilidade do CoS."
    )

    return with_orchestrator_check(state, "qa_execution", response, {
        "qa_exec_output": response.content,
        "confidence_by_agent": {
            **state.get("confidence_by_agent", {}),
            "qa_execution": "C1"
        },
        "summaries_by_agent": {
            **state.get("summaries_by_agent", {}),
            "qa_execution": response.content[:300]
        }
    })

def cos_node(state: SquadState):
    prompt = read_prompt("cos.txt")

    product = state.get("product_output", "")
    qa_plan = state.get("qa_plan_output", "")
    engineering = state.get("engineering_output", "")
    operator = state.get("operator_output", "")
    engineering_review = state.get("engineering_review_output", "")
    qa_exec = state.get("qa_exec_output", "")
    writing = state.get("writing_output", "")
    ux_ui = state.get("ux_ui_output", "")
    privacy = state.get("privacy_output", "")
    appsec = state.get("appsec_output", "")
    validation = state.get("manual_validation_result", "")
    run_id = state.get("run_id", "")

    agent_context = format_agent_context("cos")
    cmo = cmo_block(
        state,
        "cos",
        "Consolidar coerencia do ciclo e decidir a rota operacional.",
        qa_exec,
        "Decisao executiva, rota, logs recomendados, ECE e resumo.",
    )

    response = invoke_validated("cos",
        f"[[AGENT:COS]]\n\n"
        f"{prompt}\n\n"
        f"{cmo}\n\n"
        f"Objetivo do projeto:\n{state['user_goal']}\n\n"
        f"Fluxo ativo:\n{state.get('active_flow', '')}\n\n"
        f"Justificativa do intake:\n{state.get('intake_rationale', '')}\n\n"
        "Artefatos ausentes podem ser esperados em fluxos curtos como docs, review, research_only, decision_only e bugfix.\n\n"
        f"{workspace_context_block(state)}"
        f"{agent_context}"
        f"Product Brief:\n{product}\n\n"
        f"QA Planning:\n{qa_plan}\n\n"
        f"Engineering Specification:\n{engineering}\n\n"
        f"Implementation Operator Package:\n{operator}\n\n"
        f"Engineering Review:\n{engineering_review}\n\n"
        f"QA Execution Report:\n{qa_exec}\n\n"
        f"Writing Artifact:\n{writing}\n\n"
        f"UX/UI Gate:\n{ux_ui}\n\n"
        f"Privacy Gate:\n{privacy}\n\n"
        f"AppSec Gate:\n{appsec}\n\n"
        f"Checks operacionais do Orchestrator:\n{format_orchestrator_checks(state)}\n\n"
        f"Resultado da validação manual:\n{validation}"
    )

    # 1. Extrair decisão estruturada do CoS
    cos_envelope = response.envelope
    if not isinstance(cos_envelope, CoSOutputEnvelope):
        raise TypeError("Envelope do CoS deveria ser CoSOutputEnvelope.")
    decision = cos_envelope.decision
    route_action = cos_envelope.route_action
    ece = cos_envelope.summary.ece

    # 2. Fallback seguro para outputs inválidos ou ambíguos
    # 2.1. Validar coerência entre decisão executiva e ação de roteamento
    is_consistent, consistency_error = validate_decision_route_consistency(
        decision=decision,
        route_action=route_action,
    )

    if not is_consistent:
        decision = "ESCALATE_TO_HUMAN"
        route_action = "ESCALATE_HUMAN"
        ece = "C3"

    # 3. Resolver rota solicitada pelo CoS
    route = resolve_route(route_action=route_action)

    # 4. Avaliar política de retry
    retry_state = state.get("retry_state", {})
    retry_result = resolve_retry_permission(
        route_action=route_action,
        retry_state=retry_state
    )

    # 5. Transformar decisão do CoS em próximo passo operacional
    route_decision = decide_next_step({
        **state,
        "cos_decision": decision,
        "cos_route_action": route_action,
        "route_status": route["route_status"],
        "route_target": route["route_target"],
        "route_reason": route["route_reason"],
        "retry_state": retry_result,
        "retry_allowed": retry_result.get("retry_allowed", False),
        "retry_target": retry_result.get("retry_target", ""),
        "retry_blocked": retry_result.get("retry_blocked", False),
        "retry_block_reason": retry_result.get("retry_block_reason", ""),
    })

    # 6. Consolidar estado final de rota para logs e escaladas
    route_state = {
        **state,
        "run_id": run_id,
        "cos_decision": decision,
        "cos_route_action": route_action,
        "cos_ece": ece,
        "route_status": route["route_status"],
        "route_target": route["route_target"],
        "route_reason": route["route_reason"],
        "route_decision": route_decision,
        "retry_state": retry_result,
        "retry_allowed": retry_result.get("retry_allowed", False),
        "retry_target": retry_result.get("retry_target", ""),
        "retry_blocked": retry_result.get("retry_blocked", False),
        "retry_block_reason": retry_result.get("retry_block_reason", ""),
        "consistency_error": consistency_error,
    }

    # 7. Registrar parecer final do ciclo
    append_cycle_report(
        run_id=run_id,
        user_goal=state["user_goal"],
        cos_output=response.content,
        decision=decision,
        route_action=route_action,
        ece=ece
    )

    # 8. Registrar evento operacional de roteamento
    append_route_event(
        run_id=run_id,
        user_goal=state["user_goal"],
        route_summary=get_route_summary(route_state),
    )

    # 9. Avaliar e registrar escalada humana formal, quando necessário
    human_escalation_created = should_create_human_escalation(route_state)
    human_escalation_reason = ""
    human_required_decision = ""

    if human_escalation_created:
        human_escalation_reason = build_escalation_reason(route_state)
        human_required_decision = build_required_decision(route_state)

        append_human_escalation(
            run_id=run_id,
            user_goal=state["user_goal"],
            reason=human_escalation_reason,
            required_decision=human_required_decision,
            source_agent="CoS / Orchestrator",
            blockers=route_state.get("route_reason", "Não informado."),
            recommendation="Aguardar decisão humana antes de continuar.",
            status="Aberta",
            metadata={
                "cos_decision": decision,
                "cos_route_action": route_action,
                "route_status": route["route_status"],
                "route_target": route["route_target"],
                "route_decision": route_decision,
                "retry_allowed": retry_result.get("retry_allowed", False),
                "retry_blocked": retry_result.get("retry_blocked", False),
                "retry_target": retry_result.get("retry_target", ""),
            },
        )

    # 10. Definir handoff final do CoS conforme a decisão operacional
    if route_decision == "end_cycle":
        if decision == "GO_WITH_RESTRICTIONS":
            handoff_to = "Product Lead / Human Checkpoint"
            handoff_blockers = (
                "B-001 permanece ativo: Engineering completa não aprovada. "
                "B-002 permanece ativo: critérios do experimento precisam ser definidos."
            )
            handoff_next_step = (
                "Product Lead deve definir número de usuários, duração, "
                "critérios mínimos de sucesso e forma de coleta antes do experimento."
            )
            handoff_escalation = (
                "Não. Existe checkpoint humano de negócio, "
                "mas não escalada operacional."
            )
        else:
            handoff_to = "Cycle Closed"
            handoff_blockers = "Nenhum bloqueio informado."
            handoff_next_step = "Ciclo encerrado conforme decisão do CoS."
            handoff_escalation = "Não."

    elif route_decision == "human_escalation":
        handoff_to = "Human Decision Maker"
        handoff_blockers = human_escalation_reason or route["route_reason"]
        handoff_next_step = (
            human_required_decision
            or "Humano deve decidir o próximo passo antes de qualquer continuação."
        )
        handoff_escalation = "Sim."

    elif route_decision == "return_requested":
        target_names = {
            "product": "Product Lead",
            "qa_planning": "QA Planning",
            "engineering": "Engineering Lead",
            "operator": "Implementation Operator",
        }

        handoff_to = target_names.get(
            route["route_target"],
            route["route_target"]
        )

        handoff_blockers = route["route_reason"]

        handoff_next_step = (
            f"{handoff_to} deve revisar o artefato apontado pelo CoS "
            "e devolver nova versão para reavaliação."
        )

        handoff_escalation = (
            "Não, desde que retry permaneça permitido."
        )

    else:
        handoff_to = "Human Decision Maker"
        handoff_blockers = (
            "Rota do CoS não reconhecida ou inconsistente. "
            "Execução automática não permitida."
        )
        handoff_next_step = (
            "Humano deve revisar a decisão e a ação de roteamento."
        )
        handoff_escalation = "Sim."

    append_handoff(
        run_id=run_id,
        from_agent="CoS / Orchestrator",
        to_agent=handoff_to,
        artifact="CoS / Orchestrator Report",
        summary=response.content[:500],
        ece=ece,
        blockers=handoff_blockers,
        next_step=handoff_next_step,
        escalate_to_cos=handoff_escalation,
    )

    # 11. Retornar estado consolidado ao LangGraph
    return with_orchestrator_check(state, "cos", response, {
        "run_id": run_id,
        "cos_output": response.content,
        "cos_decision": decision,
        "cos_route_action": route_action,
        "cos_ece": ece,
        "route_status": route["route_status"],
        "route_target": route["route_target"],
        "route_reason": route["route_reason"],
        "route_decision": route_decision,
        "retry_state": retry_result,
        "retry_allowed": retry_result.get("retry_allowed", False),
        "retry_target": retry_result.get("retry_target", ""),
        "retry_blocked": retry_result.get("retry_blocked", False),
        "retry_block_reason": retry_result.get("retry_block_reason", ""),
        "human_escalation_created": human_escalation_created,
        "human_escalation_reason": human_escalation_reason,
        "human_required_decision": human_required_decision,
        "operational_packet": build_operational_packet(
            state,
            response,
            decision=decision,
            route_action=route_action,
        ),
        "confidence_by_agent": {
            **state.get("confidence_by_agent", {}),
            "cos": ece
        },
        "summaries_by_agent": {
            **state.get("summaries_by_agent", {}),
            "cos": response.content[:300]
        }
    })

def route_after_discovery(state: SquadState) -> str:
    if state.get("active_flow") == "research_only":
        return "cos"
    return next_after_check(state, "discovery", "product")


def route_after_product(state: SquadState) -> str:
    if state.get("active_flow") == "decision_only":
        return "cos"
    if "ux_ui" in state.get("on_demand_agents", []):
        return next_after_check(state, "product", "ux_ui")
    return next_after_check(state, "product", "qa_planning")


def route_after_ux_ui(state: SquadState) -> str:
    return next_after_check(state, "ux_ui", "qa_planning")


def route_after_engineering(state: SquadState) -> str:
    if "privacy" in state.get("on_demand_agents", []):
        return next_after_check(state, "engineering", "privacy")
    if "appsec" in state.get("on_demand_agents", []):
        return next_after_check(state, "engineering", "appsec")
    return next_after_check(state, "engineering", "operator")


def route_after_privacy(state: SquadState) -> str:
    if "appsec" in state.get("on_demand_agents", []):
        return next_after_check(state, "privacy", "appsec")
    if state.get("active_flow") == "review":
        return next_after_check(state, "privacy", "cos")
    return next_after_check(state, "privacy", "operator")


def route_after_appsec(state: SquadState) -> str:
    if state.get("active_flow") == "review":
        return next_after_check(state, "appsec", "cos")
    return next_after_check(state, "appsec", "operator")


def route_after_engineering_review(state: SquadState) -> str:
    if state.get("active_flow") == "review":
        if "privacy" in state.get("on_demand_agents", []):
            return next_after_check(state, "engineering_review", "privacy")
        if "appsec" in state.get("on_demand_agents", []):
            return next_after_check(state, "engineering_review", "appsec")
        return "cos"
    return next_after_check(state, "engineering_review", "qa_execution")


builder = StateGraph(SquadState)

builder.add_node("intake", intake_node)
builder.add_node("discovery", discovery_node)
builder.add_node("writing", writing_node)
builder.add_node("ux_ui", ux_ui_node)
builder.add_node("privacy", privacy_node)
builder.add_node("appsec", appsec_node)
builder.add_node("product", product_node)
builder.add_node("qa_planning", qa_planning_node)
builder.add_node("engineering", engineering_node)
builder.add_node("operator", operator_node)
builder.add_node("engineering_review", engineering_review_node)
builder.add_node("qa_execution", qa_execution_node)
builder.add_node("cos", cos_node)

builder.add_edge(START, "intake")
builder.add_conditional_edges(
    "intake",
    route_after_intake,
    ["discovery", "product", "engineering", "engineering_review", "writing"]
)

builder.add_conditional_edges(
    "discovery",
    route_after_discovery,
    ["product", "cos"]
)
builder.add_conditional_edges(
    "product",
    route_after_product,
    ["ux_ui", "qa_planning", "cos"]
)
builder.add_conditional_edges(
    "ux_ui",
    route_after_ux_ui,
    ["qa_planning", "cos"]
)
builder.add_conditional_edges(
    "qa_planning",
    lambda state: next_after_check(state, "qa_planning", "engineering"),
    ["engineering", "cos"]
)
builder.add_conditional_edges(
    "engineering",
    route_after_engineering,
    ["privacy", "appsec", "operator", "cos"]
)
builder.add_conditional_edges(
    "privacy",
    route_after_privacy,
    ["appsec", "operator", "cos"]
)
builder.add_conditional_edges(
    "appsec",
    route_after_appsec,
    ["operator", "cos"]
)
builder.add_conditional_edges(
    "operator",
    lambda state: next_after_check(state, "operator", "engineering_review"),
    ["engineering_review", "cos"]
)
builder.add_conditional_edges(
    "engineering_review",
    route_after_engineering_review,
    ["privacy", "appsec", "qa_execution", "cos"]
)
builder.add_conditional_edges(
    "writing",
    lambda state: next_after_check(state, "writing", "cos"),
    ["cos"]
)
builder.add_edge("qa_execution", "cos")
builder.add_edge("cos", END)

graph = builder.compile()
