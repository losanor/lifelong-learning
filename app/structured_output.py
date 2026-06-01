"""Schemas e parsing para outputs criticos da Squad v5 Lite."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.cos_decision import VALID_DECISIONS, VALID_ROUTE_ACTIONS
from contracts import Agente, Bloqueio, ECE, Fase, ProximoPasso, ResumoEstruturado


SCHEMA_VERSION = "squad.agent_output.v1"

AGENT_IDENTITY = {
    "vision": (Agente.VISION, Fase.IDEACAO),
    "discovery": (Agente.DISCOVERY, Fase.DISCOVERY),
    "product": (Agente.PRODUCT, Fase.PRODUTO),
    "qa_planning": (Agente.QA, Fase.QA),
    "engineering": (Agente.ENGINEERING, Fase.ENGENHARIA),
    "operator": (Agente.OPERATOR, Fase.IMPLEMENTACAO),
    "engineering_review": (Agente.ENGINEERING, Fase.ENGENHARIA),
    "qa_execution": (Agente.QA, Fase.QA),
    "writing": (Agente.WRITING, Fase.RELEASE),
    "ux_ui": (Agente.UX_UI, Fase.PRODUTO),
    "privacy": (Agente.PRIVACY, Fase.ENGENHARIA),
    "appsec": (Agente.APPSEC, Fase.ENGENHARIA),
    "cos": (Agente.COS, Fase.RELEASE),
}

OUTPUT_CONTRACT_INSTRUCTION = """
CONTRATO DE SAIDA DO RUNTIME:
Responda somente com JSON valido. Nao use code fence e nao escreva texto fora do JSON.
O JSON deve conter:
{
  "schema_version": "squad.agent_output.v1",
  "agent_name": "<agent_name>",
  "artifact_markdown": "<artefato humano em Markdown seguindo o formato pedido acima>",
  "summary": {
    "agente_emissor": "<nome canonico do agente>",
    "fase": "<fase>",
    "decisao_ou_artefato": "<resumo curto>",
    "artefato_completo_ref": null,
    "ece": "C1|C2|C3",
    "justificativa_ece": "<por que este ECE>",
    "suposicoes": [],
    "bloqueios": [{"descricao": "...", "tipo": "tecnico|insumo|decisao|escopo|externo", "precisa_escalar": false, "dono_sugerido": null}],
    "proximo_passo": {"acao": "...", "dono": "<nome canonico do agente dono>", "prazo_sugerido": null},
    "precisa_escalar": false,
    "motivo_escalada": null
  },
  "operational_artifact": {
    "artifact_type": "<tipo curto do artefato>",
    "target_refs": ["<arquivo, documento, componente ou decisao afetada>"],
    "recommended_actions": ["<acao concreta do proximo dono>"],
    "verification_steps": ["<como validar aproveitamento>"],
    "git_actions": ["<acao Git local se Git existir>"],
    "documentation_actions": ["<acao de documentacao/log se couber>"],
    "execution_ready": true,
    "human_checkpoint": null
  }
}
O `artifact_markdown` continua sendo o documento legivel para humanos.
O campo `summary` e a fonte de verdade operacional para ECE, bloqueios e proximo passo.
O campo `operational_artifact` deve transformar o output em trabalho executavel no workspace real.
Quando o contexto disser que Git nao existe, deixe `git_actions` vazio e nao presuma PR, branch ou commit.
Para controlar custo e latencia, mantenha `artifact_markdown` conciso: ate 2500 caracteres,
salvo quando a tarefa pedir expressamente um documento mais longo. Nao repita contexto recebido.
""".strip()

COS_CONTRACT_EXTENSION = """
Para o CoS, o JSON tambem deve conter:
  "decision": "GO|GO_WITH_RESTRICTIONS|RETURN_TO_PRODUCT|RETURN_TO_QA|RETURN_TO_ENGINEERING|RETURN_TO_OPERATOR|ESCALATE_TO_HUMAN|NO_GO",
  "route_action": "END_CYCLE|ROUTE_TO_PRODUCT|ROUTE_TO_QA|ROUTE_TO_ENGINEERING|ROUTE_TO_OPERATOR|ESCALATE_HUMAN"
Esses campos devem ser coerentes com o artefato Markdown.
""".strip()


class OperationalArtifact(BaseModel):
    """Pacote acionavel que aproxima o output do trabalho diario real."""

    artifact_type: str = Field(..., min_length=1, max_length=80)
    target_refs: list[str] = Field(..., min_length=1)
    recommended_actions: list[str] = Field(..., min_length=1)
    verification_steps: list[str] = Field(..., min_length=1)
    git_actions: list[str] = Field(default_factory=list)
    documentation_actions: list[str] = Field(default_factory=list)
    execution_ready: bool
    human_checkpoint: str | None = None


class AgentOutputEnvelope(BaseModel):
    schema_version: Literal["squad.agent_output.v1"] = SCHEMA_VERSION
    agent_name: str = Field(..., description="ID interno do agente no grafo.")
    artifact_markdown: str = Field(..., min_length=1)
    summary: ResumoEstruturado
    operational_artifact: OperationalArtifact

    @model_validator(mode="after")
    def validate_identity(self) -> "AgentOutputEnvelope":
        identity = AGENT_IDENTITY.get(self.agent_name)
        if identity is None:
            raise ValueError(f"agent_name desconhecido: {self.agent_name}")

        expected_agent, expected_phase = identity
        if self.summary.agente_emissor != expected_agent.value:
            raise ValueError(
                "summary.agente_emissor diverge do agente do envelope: "
                f"{self.summary.agente_emissor!r} != {expected_agent.value!r}"
            )
        if self.summary.fase != expected_phase.value:
            raise ValueError(
                "summary.fase diverge da fase do agente: "
                f"{self.summary.fase!r} != {expected_phase.value!r}"
            )
        return self


class CoSOutputEnvelope(AgentOutputEnvelope):
    decision: str
    route_action: str

    @model_validator(mode="after")
    def validate_cos_route(self) -> "CoSOutputEnvelope":
        if self.agent_name != "cos":
            raise ValueError("CoSOutputEnvelope aceita somente agent_name='cos'.")
        if self.decision not in VALID_DECISIONS:
            raise ValueError(f"decision invalida: {self.decision}")
        if self.route_action not in VALID_ROUTE_ACTIONS:
            raise ValueError(f"route_action invalida: {self.route_action}")
        return self


def output_contract_instruction(agent_name: str) -> str:
    instruction = OUTPUT_CONTRACT_INSTRUCTION.replace("<agent_name>", agent_name)
    agente, fase = AGENT_IDENTITY[agent_name]
    agentes_validos = " | ".join(item.value for item in Agente)
    instruction = (
        f"{instruction}\n"
        f"Para este agente use exatamente `summary.agente_emissor`: `{agente.value}`.\n"
        f"Para este agente use exatamente `summary.fase`: `{fase.value}`.\n"
        f"Valores validos para donos e agentes: {agentes_validos}."
    )
    if agent_name == "cos":
        instruction = f"{instruction}\n\n{COS_CONTRACT_EXTENSION}"
    return instruction


def with_output_contract(prompt: str, agent_name: str) -> str:
    return f"{prompt}\n\n{output_contract_instruction(agent_name)}"


def _json_payload(raw_content: str) -> dict[str, Any]:
    text = raw_content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def parse_agent_output(raw_content: str, agent_name: str) -> AgentOutputEnvelope:
    payload = _json_payload(raw_content)
    if payload.get("agent_name") != agent_name:
        raise ValueError(
            f"Envelope retornou agent_name={payload.get('agent_name')!r}; esperado {agent_name!r}."
        )
    model = CoSOutputEnvelope if agent_name == "cos" else AgentOutputEnvelope
    return model.model_validate(payload)


def fallback_invalid_output(
    agent_name: str,
    raw_content: str,
    error: Exception,
) -> AgentOutputEnvelope:
    agente, fase = AGENT_IDENTITY[agent_name]
    error_text = str(error).splitlines()[0][:260]
    artifact = (
        "# Structured Output Rejected\n\n"
        f"O runtime rejeitou a saida de `{agent_name}` antes de usa-la como insumo.\n\n"
        f"Motivo: {error_text}\n\n"
        "Amostra do output bruto:\n\n"
        f"```text\n{raw_content[:1200]}\n```"
    )
    summary = ResumoEstruturado(
        agente_emissor=agente,
        fase=fase,
        decisao_ou_artefato=f"Saida de {agent_name} rejeitada por validacao de schema.",
        ece=ECE.C3,
        justificativa_ece="O output nao passou no contrato JSON/Pydantic.",
        bloqueios=[
            Bloqueio(
                descricao=error_text or "Falha ao validar envelope estruturado.",
                tipo="insumo",
                precisa_escalar=True,
                dono_sugerido=Agente.COS,
            )
        ],
        proximo_passo=ProximoPasso(
            acao="CoS deve revisar o output invalido e decidir retorno ao agente.",
            dono=Agente.COS,
        ),
        precisa_escalar=True,
        motivo_escalada="Output critico invalido; texto livre nao pode alimentar execucao.",
    )
    payload: dict[str, Any] = {
        "agent_name": agent_name,
        "artifact_markdown": artifact,
        "summary": summary,
        "operational_artifact": OperationalArtifact(
            artifact_type="structured_output_rejection",
            target_refs=[agent_name],
            recommended_actions=["Regerar ou revisar o output antes de reutiliza-lo."],
            verification_steps=["Validar o envelope JSON contra o contrato Pydantic."],
            execution_ready=False,
            human_checkpoint="CoS deve decidir se retorna ao agente ou escala.",
        ),
    }
    if agent_name == "cos":
        payload.update(decision="ESCALATE_TO_HUMAN", route_action="ESCALATE_HUMAN")
        return CoSOutputEnvelope.model_validate(payload)
    return AgentOutputEnvelope.model_validate(payload)


def safe_parse_agent_output(raw_content: str, agent_name: str) -> tuple[AgentOutputEnvelope, list[str]]:
    try:
        return parse_agent_output(raw_content, agent_name), []
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as error:
        return fallback_invalid_output(agent_name, raw_content, error), [str(error)]


def mock_agent_output(
    agent_name: str,
    artifact_markdown: str,
    *,
    ece: ECE,
    decision: str = "",
    route_action: str = "",
) -> str:
    agente, fase = AGENT_IDENTITY[agent_name]
    next_owner = {
        "vision": Agente.COS,
        "discovery": Agente.PRODUCT,
        "product": Agente.QA,
        "qa_planning": Agente.ENGINEERING,
        "engineering": Agente.OPERATOR,
        "operator": Agente.ENGINEERING,
        "engineering_review": Agente.QA,
        "qa_execution": Agente.COS,
        "writing": Agente.COS,
        "ux_ui": Agente.QA,
        "privacy": Agente.ENGINEERING,
        "appsec": Agente.OPERATOR,
        "cos": Agente.PRODUCT,
    }[agent_name]
    summary = ResumoEstruturado(
        agente_emissor=agente,
        fase=fase,
        decisao_ou_artefato=f"Artefato mock validado para {agent_name}.",
        ece=ece,
        justificativa_ece="Fixture mock deterministica para validar o fluxo.",
        proximo_passo=ProximoPasso(
            acao=f"Continuar fluxo apos {agent_name}.",
            dono=next_owner,
        ),
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "agent_name": agent_name,
        "artifact_markdown": artifact_markdown,
        "summary": summary.model_dump(mode="json"),
        "operational_artifact": {
            "artifact_type": f"{agent_name}_artifact",
            "target_refs": [f"{agent_name}:artifact"],
            "recommended_actions": [f"Executar proximo passo recomendado por {agent_name}."],
            "verification_steps": [f"Conferir ECE e bloqueios do output de {agent_name}."],
            "git_actions": [],
            "documentation_actions": [f"Registrar artefato {agent_name} se virar handoff."],
            "execution_ready": ece != ECE.C3,
            "human_checkpoint": None,
        },
    }
    if agent_name == "cos":
        payload["decision"] = decision or "GO_WITH_RESTRICTIONS"
        payload["route_action"] = route_action or "END_CYCLE"
    return json.dumps(payload, ensure_ascii=False)
