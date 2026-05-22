"""
Contextos Minimos Operacionais especializados para a Squad v5 Lite.

Esta camada fica pequena de proposito: o CMO mantem somente o contexto que o
agente precisa para a sessao atual, enquanto artefatos completos continuam em
referencias e logs.
"""

from __future__ import annotations

from typing import Iterable

from pydantic import Field

from contracts import Agente, CMO, ECE, Fase, SharedMemory


def _clip_items(items: Iterable[str] | None, limit: int = 5) -> list[str]:
    return [item for item in (items or []) if item][:limit]


class CMOBase(CMO):
    """CMO canonico com serializacao usada pelo HandoffLog."""

    notas_operacionais: list[str] = Field(default_factory=list, max_length=5)

    def serializar(self) -> str:
        linhas = [self.resumo_tokens()]
        if self.notas_operacionais:
            linhas.append("Notas operacionais: " + " | ".join(self.notas_operacionais))
        return "\n".join(linhas)


class CMOFactory:
    """Factories curtas para os agentes criticos do fluxo."""

    @staticmethod
    def _base(
        *,
        memoria: SharedMemory,
        agente: Agente,
        fase: Fase,
        tarefa: str,
        input_principal: str,
        output_esperado: str,
        ece_minimo: ECE = ECE.C2,
        notas_operacionais: Iterable[str] | None = None,
    ) -> CMOBase:
        decisoes = [f"[{d.id}] {d.decisao}" for d in memoria.decisoes_irreversiveis()[-3:]]
        return CMOBase(
            agente_destino=agente,
            objetivo=memoria.objetivo_atual,
            fase_atual=fase,
            tarefa=tarefa,
            input_principal=input_principal,
            restricoes=_clip_items(memoria.restricoes_ativas),
            decisoes_previas=_clip_items(decisoes),
            output_esperado=output_esperado,
            ece_minimo=ece_minimo,
            notas_operacionais=_clip_items(notas_operacionais),
        )

    @classmethod
    def para_vision(
        cls,
        *,
        memoria: SharedMemory,
        tarefa: str,
        contexto_ceo: str,
    ) -> CMOBase:
        return cls._base(
            memoria=memoria,
            agente=Agente.VISION,
            fase=Fase.IDEACAO,
            tarefa=tarefa,
            input_principal=contexto_ceo,
            output_esperado="Mapa de Visao com caminhos, lacunas, riscos e ECE.",
            notas_operacionais=["Nao gerar brief operacional.", "Maximo de duas sessoes por objetivo."],
        )

    @classmethod
    def para_discovery(
        cls,
        *,
        memoria: SharedMemory,
        tarefa: str,
        hipoteses_para_validar: Iterable[str] | None = None,
        perguntas_mercado: Iterable[str] | None = None,
    ) -> CMOBase:
        notas = [
            *[f"Hipotese: {item}" for item in _clip_items(hipoteses_para_validar, 3)],
            *[f"Pergunta: {item}" for item in _clip_items(perguntas_mercado, 2)],
        ]
        return cls._base(
            memoria=memoria,
            agente=Agente.DISCOVERY,
            fase=Fase.DISCOVERY,
            tarefa=tarefa,
            input_principal=memoria.snapshot_para_cmo(max_chars=600),
            output_esperado="Evidencias, lacunas, fontes e resumo estruturado.",
            notas_operacionais=notas,
        )

    @classmethod
    def para_product(
        cls,
        *,
        memoria: SharedMemory,
        tarefa: str,
        discovery_ref: str,
    ) -> CMOBase:
        return cls._base(
            memoria=memoria,
            agente=Agente.PRODUCT,
            fase=Fase.PRODUTO,
            tarefa=tarefa,
            input_principal=discovery_ref,
            output_esperado="PRD ou Product Brief com MVP Sujo, EME e ECE.",
        )

    @classmethod
    def para_engineering(
        cls,
        *,
        memoria: SharedMemory,
        tarefa: str,
        product_ref: str,
        qa_ref: str,
    ) -> CMOBase:
        return cls._base(
            memoria=memoria,
            agente=Agente.ENGINEERING,
            fase=Fase.ENGENHARIA,
            tarefa=tarefa,
            input_principal=f"Product: {product_ref}. QA: {qa_ref}.",
            output_esperado="Especificacao Tecnica com trade-offs, 3 Desastres e ECE.",
        )

    @classmethod
    def para_operator(
        cls,
        *,
        memoria: SharedMemory,
        tarefa: str,
        spec_tecnica_ref: str,
        spec_ece: ECE,
        test_cases_ref: str,
        tarefa_implementacao: str,
    ) -> CMOBase:
        if spec_ece == ECE.C3:
            raise ValueError("Operator nao pode receber especificacao C3.")

        return cls._base(
            memoria=memoria,
            agente=Agente.OPERATOR,
            fase=Fase.IMPLEMENTACAO,
            tarefa=tarefa,
            input_principal=(
                f"Spec: {spec_tecnica_ref} ({spec_ece}). "
                f"Test cases: {test_cases_ref}. Tarefa: {tarefa_implementacao}."
            ),
            output_esperado="Pacote de implementacao, bloqueios e resumo estruturado.",
            ece_minimo=ECE.C2,
            notas_operacionais=["Voltar ao Engineering Lead apos duas tentativas bloqueadas."],
        )

    @classmethod
    def para_qa(
        cls,
        *,
        memoria: SharedMemory,
        tarefa: str,
        artefato_ref: str,
        fase: Fase = Fase.QA,
    ) -> CMOBase:
        return cls._base(
            memoria=memoria,
            agente=Agente.QA,
            fase=fase,
            tarefa=tarefa,
            input_principal=artefato_ref,
            output_esperado="Plano ou relatorio QA com EME, severidade, go/no-go e ECE.",
        )
