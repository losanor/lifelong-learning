"""
Squad v5 Lite — HandoffLog
===========================
Problema resolvido: a Shared Memory não registrava o que cada agente
recebeu nem o que devolveu. Sem isso:
  - Não dá para auditar por que um agente tomou uma decisão
  - O CoS não consegue rastrear onde contexto foi perdido
  - As 5 métricas de saúde ficam impossíveis de calcular

Solução: EntradaHandoff registra cada transição de agente —
o que chegou, o que saiu, qual o ECE, se houve escalada.
O HandoffLog agrega todas as entradas e alimenta o
HealthMonitor que calcula as 5 métricas automaticamente.

Integração: SharedMemory ganha um campo `handoffs: list[EntradaHandoff]`
e o HandoffLog é o único ponto de escrita nesse campo.

Fluxo:
    Orchestrator aciona agente
    → agente devolve ResumoEstruturado
    → Orchestrator chama HandoffLog.registrar(cmo, resumo)
    → HandoffLog salva EntradaHandoff na SharedMemory
    → HealthMonitor.calcular(memoria) atualiza MetricasSaude
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, model_validator

from contracts import (
    ECE, Agente, Fase, SharedMemory,
    ResumoEstruturado, MetricasSaude,
)
from cmo_especializado import CMOBase


# ---------------------------------------------------------------------------
# EntradaHandoff — registro de uma transição entre agentes
# ---------------------------------------------------------------------------

class EntradaHandoff(BaseModel):
    """
    Registro atômico de uma sessão de execução de agente.
    Criado pelo Orchestrator imediatamente após receber o ResumoEstruturado.

    Nunca editado após criação — é um log imutável.
    """

    # Identificação
    id: str = Field(..., description="Identificador único. Ex: 'HO-001'.")
    timestamp_inicio: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Quando o CMO foi injetado no agente."
    )
    timestamp_fim: Optional[datetime] = Field(
        default=None,
        description="Quando o ResumoEstruturado foi recebido pelo Orchestrator."
    )

    # Identidade da sessão
    agente: Agente
    fase: Fase
    sessao_numero: int = Field(
        ..., ge=1,
        description="Número sequencial de sessões deste agente nesta iniciativa."
    )

    # O que o agente recebeu (CMO resumido)
    cmo_resumo: str = Field(
        ..., max_length=400,
        description="Primeiras linhas do CMO serializado — para auditoria sem reprocessar."
    )
    tarefa_recebida: str = Field(..., max_length=300)

    # O que o agente devolveu
    artefato_produzido: str = Field(
        ..., max_length=400,
        description="Resumo do decisao_ou_artefato do ResumoEstruturado."
    )
    artefato_ref: Optional[str] = Field(
        default=None,
        description="Caminho ou ID do artefato completo."
    )

    # Qualidade do output
    ece_output: ECE
    justificativa_ece: str = Field(..., max_length=200)
    suposicoes: list[str] = Field(default_factory=list, max_length=4)

    # Bloqueios e escalada
    teve_bloqueio: bool = Field(default=False)
    descricao_bloqueio: Optional[str] = Field(default=None, max_length=200)
    escalou: bool = Field(default=False)
    motivo_escalada: Optional[str] = Field(default=None, max_length=200)

    # Resultado do handoff
    foi_aproveitado: Optional[bool] = Field(
        default=None,
        description=(
            "True = output virou insumo de execução. "
            "False = morreu na conversa ou foi bloqueado. "
            "None = ainda não determinado."
        )
    )
    motivo_nao_aproveitado: Optional[str] = Field(
        default=None, max_length=200,
        description="Por que o output não foi aproveitado (se foi_aproveitado=False)."
    )
    numero_retornos: int = Field(
        default=0, ge=0,
        description="Quantas vezes este item voltou ao agente por retrabalho."
    )

    @model_validator(mode="after")
    def validar_consistencia(self) -> "EntradaHandoff":
        if self.escalou and not self.motivo_escalada:
            raise ValueError("motivo_escalada obrigatório quando escalou=True.")
        if self.teve_bloqueio and not self.descricao_bloqueio:
            raise ValueError("descricao_bloqueio obrigatório quando teve_bloqueio=True.")
        if self.foi_aproveitado is False and not self.motivo_nao_aproveitado:
            raise ValueError(
                "motivo_nao_aproveitado obrigatório quando foi_aproveitado=False."
            )
        return self

    def duracao_segundos(self) -> Optional[float]:
        if self.timestamp_fim:
            return (self.timestamp_fim - self.timestamp_inicio).total_seconds()
        return None

    def e_c3(self) -> bool:
        return self.ece_output == ECE.C3


# ---------------------------------------------------------------------------
# HandoffLog — ponto central de registro de handoffs
# ---------------------------------------------------------------------------

class HandoffLog:
    """
    Único ponto de escrita de EntradaHandoff na SharedMemory.
    O Orchestrator chama este objeto — nunca escreve diretamente no campo.

    Responsabilidades:
    - Gerar IDs sequenciais por agente
    - Registrar a entrada na SharedMemory
    - Marcar timestamp_fim ao fechar o handoff
    - Atualizar foi_aproveitado quando o Orchestrator souber o resultado
    """

    def __init__(self, memoria: SharedMemory):
        self.memoria = memoria

    def _proximo_id(self) -> str:
        n = len(self.memoria.handoffs) + 1
        return f"HO-{str(n).zfill(3)}"

    def _numero_sessao_agente(self, agente: Agente) -> int:
        return sum(1 for h in self.memoria.handoffs if h.agente == agente) + 1

    def registrar(
        self,
        cmo: CMOBase,
        resumo: ResumoEstruturado,
        timestamp_inicio: Optional[datetime] = None,
    ) -> EntradaHandoff:
        """
        Cria e registra um EntradaHandoff a partir do CMO injetado
        e do ResumoEstruturado recebido.

        Chamado pelo Orchestrator logo após receber o output do agente.
        """
        agora = datetime.now(timezone.utc)

        entrada = EntradaHandoff(
            id=self._proximo_id(),
            timestamp_inicio=timestamp_inicio or agora,
            timestamp_fim=agora,
            agente=resumo.agente_emissor,
            fase=resumo.fase,
            sessao_numero=self._numero_sessao_agente(resumo.agente_emissor),
            cmo_resumo=cmo.serializar()[:400],
            tarefa_recebida=cmo.tarefa[:300],
            artefato_produzido=resumo.decisao_ou_artefato[:400],
            artefato_ref=resumo.artefato_completo_ref,
            ece_output=resumo.ece,
            justificativa_ece=resumo.justificativa_ece[:200],
            suposicoes=resumo.suposicoes[:4],
            teve_bloqueio=len(resumo.bloqueios) > 0,
            descricao_bloqueio=(
                resumo.bloqueios[0].descricao[:200] if resumo.bloqueios else None
            ),
            escalou=resumo.precisa_escalar,
            motivo_escalada=resumo.motivo_escalada,
        )

        self.memoria.handoffs.append(entrada)
        self.memoria.ultima_atualizacao = agora
        return entrada

    def marcar_aproveitado(self, handoff_id: str, aproveitado: bool,
                            motivo_nao_aproveitado: str | None = None) -> None:
        """
        O Orchestrator chama este método quando sabe se o output
        foi realmente usado como insumo ou morreu na conversa.
        """
        for h in self.memoria.handoffs:
            if h.id == handoff_id:
                h.foi_aproveitado = aproveitado
                if not aproveitado:
                    if not motivo_nao_aproveitado:
                        raise ValueError(
                            "motivo_nao_aproveitado obrigatório quando aproveitado=False."
                        )
                    h.motivo_nao_aproveitado = motivo_nao_aproveitado
                return
        raise ValueError(f"HandoffLog: ID '{handoff_id}' não encontrado.")

    def registrar_retorno(self, handoff_id: str) -> None:
        """Incrementa o contador de retrabalho quando um item volta ao agente."""
        for h in self.memoria.handoffs:
            if h.id == handoff_id:
                h.numero_retornos += 1
                return
        raise ValueError(f"HandoffLog: ID '{handoff_id}' não encontrado.")

    def handoffs_do_agente(self, agente: Agente) -> list[EntradaHandoff]:
        return [h for h in self.memoria.handoffs if h.agente == agente]

    def ultimo_handoff_do_agente(self, agente: Agente) -> Optional[EntradaHandoff]:
        handoffs = self.handoffs_do_agente(agente)
        return handoffs[-1] if handoffs else None

    def handoffs_c3(self) -> list[EntradaHandoff]:
        return [h for h in self.memoria.handoffs if h.e_c3()]

    def handoffs_nao_aproveitados(self) -> list[EntradaHandoff]:
        return [h for h in self.memoria.handoffs if h.foi_aproveitado is False]


# ---------------------------------------------------------------------------
# HealthMonitor — calcula as 5 métricas automaticamente
# ---------------------------------------------------------------------------

class AlertaSaude(BaseModel):
    """Um alerta gerado pelo HealthMonitor."""
    metrica: str
    valor_atual: Optional[float]
    limiar: float
    mensagem: str
    agente_afetado: Optional[Agente] = None
    severidade: str = Field(default="aviso", description="'aviso' | 'critico'")


class RelatorioSaude(BaseModel):
    """Resultado completo da verificação de saúde da squad."""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_handoffs: int
    metricas: MetricasSaude
    alertas: list[AlertaSaude]
    saudavel: bool = Field(description="True se não houver alertas críticos.")

    def resumo(self) -> str:
        if not self.alertas:
            return f"[OK] Squad saudável. {self.total_handoffs} handoff(s) registrado(s)."
        criticos = [a for a in self.alertas if a.severidade == "critico"]
        avisos = [a for a in self.alertas if a.severidade == "aviso"]
        linhas = [
            f"{'[CRITICO]' if criticos else '[AVISO]'} Squad com {len(criticos)} alerta(s) crítico(s) "
            f"e {len(avisos)} aviso(s). {self.total_handoffs} handoff(s) registrado(s)."
        ]
        for a in self.alertas:
            emoji = "[CRITICO]" if a.severidade == "critico" else "[AVISO]"
            agente_str = f" [{a.agente_afetado}]" if a.agente_afetado else ""
            linhas.append(f"  {emoji} {a.metrica}{agente_str}: {a.mensagem}")
        return "\n".join(linhas)


class HealthMonitor:
    """
    Calcula as 5 métricas de saúde da squad a partir do HandoffLog.
    O CoS chama este monitor ao final de cada ciclo relevante.

    Métricas:
    1. Taxa de aproveitamento de output (alerta se < 75%)
    2. Taxa de retrabalho (alerta se qualquer item voltou > 2x)
    3. Taxa de C3 por agente (alerta se > 40% ou 0% por muito tempo)
    4. Taxa de bloqueio do Operator (alerta se recorrente)
    5. Taxa de aprovação sem refação (alerta se < 60% em ciclo atual)
    """

    # Limiares
    LIMIAR_APROVEITAMENTO = 0.75     # Alerta se < 75% dos outputs aproveitados
    LIMIAR_RETRABALHO = 2            # Alerta se item voltou mais de 2x
    LIMIAR_C3_ALTO = 0.40            # Alerta se > 40% dos outputs de um agente são C3
    LIMIAR_C3_ZERO_MIN_HANDOFFS = 5  # Suspeito se 0% C3 após >= 5 handoffs
    LIMIAR_APROVACAO_SEM_REFACAO = 0.60  # Alerta se < 60%

    def calcular(self, memoria: SharedMemory) -> RelatorioSaude:
        handoffs = memoria.handoffs
        alertas: list[AlertaSaude] = []

        if not handoffs:
            metricas = MetricasSaude()
            return RelatorioSaude(
                total_handoffs=0,
                metricas=metricas,
                alertas=[],
                saudavel=True,
            )

        # --- Métrica 1: Taxa de aproveitamento ---
        com_resultado = [h for h in handoffs if h.foi_aproveitado is not None]
        taxa_aprov = None
        if com_resultado:
            aproveitados = sum(1 for h in com_resultado if h.foi_aproveitado)
            taxa_aprov = aproveitados / len(com_resultado)
            if taxa_aprov < self.LIMIAR_APROVEITAMENTO:
                alertas.append(AlertaSaude(
                    metrica="taxa_aproveitamento_output",
                    valor_atual=round(taxa_aprov * 100, 1),
                    limiar=self.LIMIAR_APROVEITAMENTO * 100,
                    mensagem=(
                        f"{round(taxa_aprov * 100, 1)}% dos outputs aproveitados "
                        f"(limiar: {self.LIMIAR_APROVEITAMENTO * 100}%). "
                        "Revisar se agentes estão sendo acionados sem gatilho claro."
                    ),
                    severidade="aviso",
                ))

        # --- Métrica 2: Taxa de retrabalho ---
        com_retrabalho = [h for h in handoffs if h.numero_retornos > self.LIMIAR_RETRABALHO]
        taxa_retrabalho = len(com_retrabalho) / len(handoffs) if handoffs else None
        for h in com_retrabalho:
            alertas.append(AlertaSaude(
                metrica="taxa_retrabalho",
                valor_atual=float(h.numero_retornos),
                limiar=float(self.LIMIAR_RETRABALHO),
                mensagem=(
                    f"Handoff {h.id} ({h.agente}) voltou {h.numero_retornos}x. "
                    "Revisar prompt/papel ou insumo fornecido."
                ),
                agente_afetado=h.agente,
                severidade="critico" if h.numero_retornos > 3 else "aviso",
            ))

        # --- Métrica 3: Taxa de C3 por agente ---
        agentes_unicos = list({h.agente for h in handoffs})
        taxa_c3_por_agente: dict[str, float] = {}

        for agente in agentes_unicos:
            handoffs_agente = [h for h in handoffs if h.agente == agente]
            if not handoffs_agente:
                continue
            c3s = sum(1 for h in handoffs_agente if h.e_c3())
            taxa = c3s / len(handoffs_agente)
            taxa_c3_por_agente[agente] = round(taxa * 100, 1)

            if taxa > self.LIMIAR_C3_ALTO:
                alertas.append(AlertaSaude(
                    metrica="taxa_c3_por_agente",
                    valor_atual=round(taxa * 100, 1),
                    limiar=self.LIMIAR_C3_ALTO * 100,
                    mensagem=(
                        f"{round(taxa * 100, 1)}% dos outputs são C3 "
                        f"(limiar: {self.LIMIAR_C3_ALTO * 100}%). "
                        "Agente está recebendo insumos insuficientes."
                    ),
                    agente_afetado=agente,
                    severidade="critico",
                ))
            elif taxa == 0 and len(handoffs_agente) >= self.LIMIAR_C3_ZERO_MIN_HANDOFFS:
                alertas.append(AlertaSaude(
                    metrica="taxa_c3_por_agente",
                    valor_atual=0.0,
                    limiar=0.0,
                    mensagem=(
                        f"0% de C3 após {len(handoffs_agente)} sessões. "
                        "Verificar se agente está aplicando ECE corretamente."
                    ),
                    agente_afetado=agente,
                    severidade="aviso",
                ))

        # --- Métrica 4: Taxa de bloqueio do Operator ---
        handoffs_operator = [h for h in handoffs if h.agente == Agente.OPERATOR]
        taxa_bloqueio_op = None
        if handoffs_operator:
            bloqueados = sum(1 for h in handoffs_operator if h.teve_bloqueio)
            taxa_bloqueio_op = bloqueados / len(handoffs_operator)
            # Alerta se o mesmo item travou (número de retornos > 0)
            travados_recorrentes = [
                h for h in handoffs_operator
                if h.teve_bloqueio and h.numero_retornos > 0
            ]
            if travados_recorrentes:
                alertas.append(AlertaSaude(
                    metrica="taxa_bloqueio_operator",
                    valor_atual=round(taxa_bloqueio_op * 100, 1),
                    limiar=0.0,
                    mensagem=(
                        f"{len(travados_recorrentes)} item(ns) travaram repetidamente. "
                        "Revisar Especificação Técnica fornecida."
                    ),
                    agente_afetado=Agente.OPERATOR,
                    severidade="critico",
                ))

        # --- Métrica 5: Taxa de aprovação sem refação ---
        # Aprovação sem refação = handoffs com foi_aproveitado=True e numero_retornos == 0
        aprovados_sem_retorno = [
            h for h in handoffs
            if h.foi_aproveitado is True and h.numero_retornos == 0
        ]
        total_aprovados = [h for h in handoffs if h.foi_aproveitado is True]
        taxa_aprov_sem_refacao = None
        if total_aprovados:
            taxa_aprov_sem_refacao = len(aprovados_sem_retorno) / len(total_aprovados)
            if taxa_aprov_sem_refacao < self.LIMIAR_APROVACAO_SEM_REFACAO:
                alertas.append(AlertaSaude(
                    metrica="taxa_aprovacao_sem_refacao",
                    valor_atual=round(taxa_aprov_sem_refacao * 100, 1),
                    limiar=self.LIMIAR_APROVACAO_SEM_REFACAO * 100,
                    mensagem=(
                        f"{round(taxa_aprov_sem_refacao * 100, 1)}% aprovados sem refação "
                        f"(limiar: {self.LIMIAR_APROVACAO_SEM_REFACAO * 100}%). "
                        "Revisar prompts e qualidade dos insumos."
                    ),
                    severidade="aviso",
                ))

        metricas = MetricasSaude(
            taxa_aproveitamento_output=(
                round(taxa_aprov * 100, 1) if taxa_aprov is not None else None
            ),
            taxa_retrabalho=(
                round(taxa_retrabalho * 100, 1) if taxa_retrabalho is not None else None
            ),
            taxa_c3_por_agente=taxa_c3_por_agente,
            taxa_bloqueio_operator=(
                round(taxa_bloqueio_op * 100, 1) if taxa_bloqueio_op is not None else None
            ),
            taxa_aprovacao_sem_refacao=(
                round(taxa_aprov_sem_refacao * 100, 1)
                if taxa_aprov_sem_refacao is not None else None
            ),
        )

        # Atualiza a SharedMemory com as métricas calculadas
        memoria.metricas = metricas

        criticos = [a for a in alertas if a.severidade == "critico"]
        return RelatorioSaude(
            total_handoffs=len(handoffs),
            metricas=metricas,
            alertas=alertas,
            saudavel=len(criticos) == 0,
        )


# ---------------------------------------------------------------------------
# Patch: adicionar campo handoffs à SharedMemory existente
# ---------------------------------------------------------------------------

def patch_shared_memory():
    """
    Adiciona o campo handoffs à SharedMemory sem quebrar o código existente.
    Chame esta função uma vez antes de usar o HandoffLog.
    """
    from pydantic import fields
    import contracts

    # Adicionar campo handoffs se não existir
    if not hasattr(contracts.SharedMemory, "_handoffs_patched"):
        contracts.SharedMemory.model_fields["handoffs"] = fields.FieldInfo(
            default_factory=list,
            description="Log de handoffs entre agentes. Escrito pelo HandoffLog, nunca diretamente.",
        )
        contracts.SharedMemory.model_rebuild(force=True)
        contracts.SharedMemory._handoffs_patched = True


# ---------------------------------------------------------------------------
# Exemplo de uso completo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")

    # Importar e fazer patch na SharedMemory
    patch_shared_memory()

    from contracts import SharedMemory, Fase, Agente, ECE, Bloqueio, ProximoPasso
    from onboarding import (
        OnboardingForm, OnboardingParser, OnboardingValidator,
        ContextoNegocio, RestricoesCEO, CriteriosSuccesso,
        DecisaoPrevia, HipoteseInicial,
    )
    from cmo_especializado import CMOFactory

    # 1. Montar SharedMemory via onboarding
    form = OnboardingForm(
        projeto="agendamento-clinicas-v1",
        contexto=ContextoNegocio(
            problema_central="Clínicas perdem consultas por falta de sistema de agendamento.",
            por_que_agora="Concorrente lançou produto. 3 clínicas pediram solução.",
            custo_de_nao_resolver="Perder janela de mercado nos próximos 60 dias.",
        ),
        restricoes=RestricoesCEO(
            prazo="6 semanas",
            stack_ou_tecnologia="Python + FastAPI + React",
            equipe="1 dev senior, 1 junior",
        ),
        decisoes_previas=[
            DecisaoPrevia(
                descricao="MVP sem integração com prontuário.",
                rationale="Integração travou projeto anterior.",
                irreversivel=True,
            )
        ],
        hipoteses=[
            HipoteseInicial(
                hipotese="Clínicas pagarão R$200-400/mês.",
                base="Conversas com 3 proprietários.",
                critica=True,
            )
        ],
        criterios=CriteriosSuccesso(
            definicao_de_sucesso="3 clínicas ativas com 20+ agendamentos/mês em 60 dias.",
            mvp_minimo="Cadastro de clínica, agenda semanal, link público, email.",
        ),
    )
    form.aprovado_pelo_cos = True
    memoria = OnboardingParser().converter(form)

    # Garantir campo handoffs
    if not hasattr(memoria, "handoffs"):
        memoria.handoffs = []

    log = HandoffLog(memoria)
    monitor = HealthMonitor()

    print("=" * 60)
    print("SQUAD V5 LITE — HANDOFFLOG: SIMULAÇÃO DE CICLO")
    print("=" * 60)

    # 2. Simular handoff do Vision Agent (C2, aproveitado)
    cmo_vision = CMOFactory.para_vision(
        memoria=memoria,
        tarefa="Produzir Mapa de Visão completo.",
        contexto_ceo="Quero sistema para clínicas agendarem sem WhatsApp.",
    )
    resumo_vision = ResumoEstruturado(
        agente_emissor=Agente.VISION,
        fase=Fase.IDEACAO,
        decisao_ou_artefato="Mapa de Visão: caminho Balanceado escolhido. MVP web sem mobile.",
        artefato_completo_ref="artefatos/mapa_visao_v1.md",
        ece=ECE.C2,
        justificativa_ece="Problema validado pelo CEO mas sem pesquisa de mercado ainda.",
        proximo_passo=ProximoPasso(acao="CoS transforma em brief.", dono=Agente.COS),
    )
    ho_vision = log.registrar(cmo_vision, resumo_vision)
    log.marcar_aproveitado(ho_vision.id, aproveitado=True)
    print(f"\n[HO registrado] {ho_vision.id} — {ho_vision.agente} | ECE: {ho_vision.ece_output}")

    # 3. Simular handoff do Discovery (C3, não aproveitado)
    cmo_disc = CMOFactory.para_discovery(
        memoria=memoria,
        tarefa="Validar hipótese de preço e levantar TAM.",
        hipoteses_para_validar=["Clínicas pagarão R$200-400/mês."],
        perguntas_mercado=["Qual o TAM de clínicas no Brasil?"],
    )
    resumo_disc = ResumoEstruturado(
        agente_emissor=Agente.DISCOVERY,
        fase=Fase.DISCOVERY,
        decisao_ou_artefato="Não encontrei fontes confiáveis para TAM de clínicas no Brasil.",
        ece=ECE.C3,
        justificativa_ece="Sem fonte primária ou secundária verificável.",
        bloqueios=[Bloqueio(
            descricao="Sem acesso a dados de mercado de saúde no Brasil.",
            tipo="insumo",
            precisa_escalar=True,
            dono_sugerido=Agente.COS,
        )],
        proximo_passo=ProximoPasso(
            acao="CoS decide se busca fonte externa ou arquiva.",
            dono=Agente.COS,
        ),
        precisa_escalar=True,
        motivo_escalada="Output C3 sem insumo para destravar internamente.",
    )
    ho_disc = log.registrar(cmo_disc, resumo_disc)
    log.marcar_aproveitado(
        ho_disc.id,
        aproveitado=False,
        motivo_nao_aproveitado="C3 bloqueado — não pode virar insumo de execução.",
    )
    print(f"[HO registrado] {ho_disc.id} — {ho_disc.agente} | ECE: {ho_disc.ece_output} [C3]")

    # 4. Simular handoff do Operator com retrabalho
    from cmo_especializado import CMOFactory
    cmo_op = CMOFactory.para_operator(
        memoria=memoria,
        tarefa="Implementar POST /clinica.",
        spec_tecnica_ref="artefatos/spec_v1.md",
        spec_ece=ECE.C1,
        test_cases_ref="artefatos/test_cases_v1.md",
        tarefa_implementacao="Criar endpoint POST /clinica.",
    )
    resumo_op = ResumoEstruturado(
        agente_emissor=Agente.OPERATOR,
        fase=Fase.IMPLEMENTACAO,
        decisao_ou_artefato="Endpoint POST /clinica implementado. Erro 500 em validação de CNPJ.",
        ece=ECE.C2,
        justificativa_ece="Implementação parcial. Bloqueio em validação de CNPJ.",
        bloqueios=[Bloqueio(
            descricao="Biblioteca de validação de CNPJ não especificada na spec.",
            tipo="tecnico",
            precisa_escalar=True,
            dono_sugerido=Agente.ENGINEERING,
        )],
        proximo_passo=ProximoPasso(
            acao="Eng Lead ajusta spec com biblioteca de validação.",
            dono=Agente.ENGINEERING,
        ),
        precisa_escalar=True,
        motivo_escalada="Bloqueio técnico após 1ª tentativa. Spec incompleta.",
    )
    ho_op = log.registrar(cmo_op, resumo_op)
    log.registrar_retorno(ho_op.id)  # voltou ao Eng Lead e retornou
    log.marcar_aproveitado(ho_op.id, aproveitado=True)
    print(f"[HO registrado] {ho_op.id} — {ho_op.agente} | ECE: {ho_op.ece_output} (retrabalho: {ho_op.numero_retornos}x)")

    # 5. Calcular métricas de saúde
    print(f"\n{'='*60}")
    print("RELATÓRIO DE SAÚDE DA SQUAD")
    print("=" * 60)
    relatorio = monitor.calcular(memoria)
    print(relatorio.resumo())

    print(f"\nMétricas detalhadas:")
    m = relatorio.metricas
    print(f"  Taxa de aproveitamento: {m.taxa_aproveitamento_output}%")
    print(f"  Taxa de retrabalho: {m.taxa_retrabalho}%")
    print(f"  C3 por agente: {m.taxa_c3_por_agente}")
    print(f"  Bloqueio Operator: {m.taxa_bloqueio_operator}%")
    print(f"  Aprovação sem refação: {m.taxa_aprovacao_sem_refacao}%")

    print(f"\nTotal de handoffs registrados: {len(memoria.handoffs)}")
    print(f"C3s no log: {len(log.handoffs_c3())}")
    print(f"Não aproveitados: {len(log.handoffs_nao_aproveitados())}")
