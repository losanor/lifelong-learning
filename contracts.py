"""
Squad v5 Lite — Contratos de Comunicação
=========================================
Schemas Pydantic para as 3 estruturas centrais de comunicação entre agentes:
  1. CMO   — Contexto Mínimo Operacional (injetado pelo Orchestrator em cada agente)
  2. ResumoEstruturado — Output padrão devolvido por todo agente ao Orchestrator
  3. SharedMemory — Estado compartilhado persistente da squad

Uso:
    from contracts import CMO, ResumoEstruturado, SharedMemory, ECE, Agente
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ECE(str, Enum):
    """Escala de Confiança Explícita — obrigatória em todo output relevante."""
    C1 = "C1"  # Decide   — base suficiente, pode seguir
    C2 = "C2"  # Recomenda — base parcial, segue com ressalva
    C3 = "C3"  # Sinaliza  — base insuficiente, NÃO pode virar insumo de execução


class Agente(str, Enum):
    """Identificadores canônicos de cada agente da squad."""
    CEO             = "CEO"
    VISION          = "Vision Agent"
    COS             = "Chief of Staff"
    ORCHESTRATOR    = "Orchestrator"
    DISCOVERY       = "Discovery"
    PRODUCT         = "Product Lead"
    ENGINEERING     = "Engineering Lead"
    OPERATOR        = "Implementation Operator"
    UX_UI           = "UX/UI Lead"
    QA              = "QA Lead"
    PRIVACY         = "Privacy & Compliance"
    APPSEC          = "AppSec / Security"
    WRITING         = "Writing / Documentation"
    DATA_SCIENTIST  = "Data Scientist"


class Fase(str, Enum):
    """Fase atual do ciclo de vida da iniciativa."""
    IDEACAO      = "ideacao"
    DISCOVERY    = "discovery"
    PRODUTO      = "produto"
    ENGENHARIA   = "engenharia"
    IMPLEMENTACAO = "implementacao"
    QA           = "qa"
    RELEASE      = "release"


# ---------------------------------------------------------------------------
# 1. CMO — Contexto Mínimo Operacional
# ---------------------------------------------------------------------------

class CMO(BaseModel):
    """
    Bloco de até ~200 tokens injetado pelo Orchestrator em cada agente
    antes de sua execução. Contém apenas o mínimo para a tarefa atual.

    O Orchestrator monta um CMO específico por agente — não existe
    um CMO genérico para toda a squad.
    """

    # Identificação
    agente_destino: Agente = Field(
        ...,
        description="Para qual agente este CMO está sendo injetado."
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Momento de criação do CMO (UTC)."
    )

    # Contexto da iniciativa
    objetivo: str = Field(
        ...,
        max_length=300,
        description="Objetivo de negócio da iniciativa em 1-2 frases."
    )
    fase_atual: Fase = Field(
        ...,
        description="Em qual fase do ciclo de desenvolvimento a squad está."
    )

    # Tarefa específica
    tarefa: str = Field(
        ...,
        max_length=400,
        description="O que exatamente este agente deve fazer nesta sessão."
    )
    input_principal: str = Field(
        ...,
        max_length=600,
        description="Artefato ou dado de entrada para esta tarefa (resumido)."
    )

    # Restrições e decisões relevantes
    restricoes: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Restrições inegociáveis relevantes para esta tarefa (máx 5)."
    )
    decisoes_previas: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Decisões já tomadas que este agente não deve reabrir (máx 5)."
    )

    # Saída esperada
    output_esperado: str = Field(
        ...,
        max_length=200,
        description="Formato e conteúdo esperado no output deste agente."
    )
    ece_minimo: ECE = Field(
        default=ECE.C2,
        description="ECE mínimo aceitável para este output. Abaixo disso, escala ao CoS."
    )

    class Config:
        use_enum_values = True

    def resumo_tokens(self) -> str:
        """Serializa o CMO em texto compacto para injeção no prompt do agente."""
        linhas = [
            f"[CMO → {self.agente_destino}]",
            f"Objetivo: {self.objetivo}",
            f"Fase: {self.fase_atual}",
            f"Tarefa: {self.tarefa}",
            f"Input: {self.input_principal}",
        ]
        if self.restricoes:
            linhas.append("Restrições: " + " | ".join(self.restricoes))
        if self.decisoes_previas:
            linhas.append("Decisões fechadas: " + " | ".join(self.decisoes_previas))
        linhas.append(f"Output esperado: {self.output_esperado}")
        linhas.append(f"ECE mínimo aceitável: {self.ece_minimo}")
        return "\n".join(linhas)


# ---------------------------------------------------------------------------
# 2. Resumo Estruturado — Output padrão de todo agente
# ---------------------------------------------------------------------------

class Bloqueio(BaseModel):
    """Representa um bloqueio identificado pelo agente durante a execução."""
    descricao: str = Field(..., description="O que está bloqueando.")
    tipo: str = Field(
        ...,
        description="Tipo: 'tecnico' | 'insumo' | 'decisao' | 'escopo' | 'externo'."
    )
    precisa_escalar: bool = Field(
        default=False,
        description="True se precisar do CoS ou CEO para destravar."
    )
    dono_sugerido: Optional[Agente] = Field(
        default=None,
        description="Agente sugerido para resolver o bloqueio."
    )


class ProximoPasso(BaseModel):
    """Próximo passo claro com dono definido."""
    acao: str = Field(..., description="O que deve ser feito.")
    dono: Agente = Field(..., description="Quem é responsável por executar.")
    prazo_sugerido: Optional[str] = Field(
        default=None,
        description="Prazo sugerido em linguagem natural (ex: 'próxima sessão', '24h')."
    )


class ResumoEstruturado(BaseModel):
    """
    Output padrão obrigatório de todo agente ao final de sua execução.
    O Orchestrator não aceita output sem este formato.

    Regra: outputs sem ECE voltam ao agente emissor.
    """

    # Identificação
    agente_emissor: Agente = Field(..., description="Quem produziu este resumo.")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    fase: Fase = Field(..., description="Fase em que o agente estava operando.")

    # Artefato produzido
    decisao_ou_artefato: str = Field(
        ...,
        max_length=800,
        description="Decisão tomada ou artefato produzido nesta sessão (resumo)."
    )
    artefato_completo_ref: Optional[str] = Field(
        default=None,
        description="Referência para o artefato completo (caminho, ID ou chave)."
    )

    # Qualidade e confiança
    ece: ECE = Field(
        ...,
        description="Classificação de confiança deste output. C3 bloqueia execução."
    )
    justificativa_ece: str = Field(
        ...,
        max_length=300,
        description="Por que este output recebeu este ECE. Obrigatório."
    )
    suposicoes: list[str] = Field(
        default_factory=list,
        description="Suposições feitas que ainda não foram validadas."
    )

    # Bloqueios e próximos passos
    bloqueios: list[Bloqueio] = Field(
        default_factory=list,
        description="Bloqueios identificados. Lista vazia = nenhum bloqueio."
    )
    proximo_passo: ProximoPasso = Field(
        ...,
        description="Próximo passo claro com dono definido. Obrigatório."
    )

    # Escalada
    precisa_escalar: bool = Field(
        default=False,
        description="True se este output ou algum bloqueio precisar subir ao CoS/CEO."
    )
    motivo_escalada: Optional[str] = Field(
        default=None,
        description="Por que está escalando. Obrigatório se precisa_escalar=True."
    )

    class Config:
        use_enum_values = True

    def e_c3(self) -> bool:
        return self.ece == ECE.C3

    def valida_escalada(self) -> None:
        """Garante que escaladas sempre tenham motivo."""
        if self.precisa_escalar and not self.motivo_escalada:
            raise ValueError("motivo_escalada é obrigatório quando precisa_escalar=True.")

    def model_post_init(self, __context) -> None:
        self.valida_escalada()


# ---------------------------------------------------------------------------
# 3. Shared Memory — Estado compartilhado da squad
# ---------------------------------------------------------------------------

class EntradaDecisao(BaseModel):
    """Uma entrada no Decision Log da Shared Memory."""
    id: str = Field(..., description="Identificador único (ex: 'D-001').")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agente: Agente = Field(..., description="Quem tomou ou registrou a decisão.")
    decisao: str = Field(..., max_length=300, description="A decisão em si.")
    rationale: str = Field(..., max_length=300, description="Por quê foi tomada.")
    irreversivel: bool = Field(
        default=False,
        description="True = não pode ser reaberta por nenhum agente."
    )
    ece: ECE = Field(..., description="ECE no momento da decisão.")


class EntradaRisco(BaseModel):
    """Um risco registrado na Shared Memory."""
    id: str = Field(..., description="Identificador único (ex: 'R-001').")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agente_origem: Agente
    descricao: str = Field(..., max_length=300)
    severidade: str = Field(
        ...,
        description="'baixa' | 'media' | 'alta' | 'critica'."
    )
    status: str = Field(
        default="aberto",
        description="'aberto' | 'mitigado' | 'aceito' | 'encerrado'."
    )
    dono: Optional[Agente] = None


class EntradaHipotese(BaseModel):
    """Uma hipótese aberta ainda não validada."""
    id: str = Field(..., description="Identificador único (ex: 'H-001').")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agente_origem: Agente
    hipotese: str = Field(..., max_length=300)
    status: str = Field(
        default="aberta",
        description="'aberta' | 'validada' | 'refutada' | 'arquivada'."
    )
    proxima_acao: Optional[str] = Field(default=None, max_length=200)


class EntradaPendencia(BaseModel):
    """Uma pendência com dono definido."""
    id: str = Field(..., description="Identificador único (ex: 'P-001').")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    descricao: str = Field(..., max_length=300)
    dono: Agente
    prazo: Optional[str] = None
    status: str = Field(default="aberta", description="'aberta' | 'resolvida'.")


class MetricasSaude(BaseModel):
    """
    5 métricas de saúde da squad monitoradas pelo CoS.
    Atualizar ao fim de cada ciclo relevante.
    """
    taxa_aproveitamento_output: Optional[float] = Field(
        default=None,
        description="% de outputs que viraram execução. Alerta se <75% (>25% morrendo)."
    )
    taxa_retrabalho: Optional[float] = Field(
        default=None,
        description="% de itens que voltaram mais de 2 vezes. Alerta se >0."
    )
    taxa_c3_por_agente: dict[str, float] = Field(
        default_factory=dict,
        description="% de outputs C3 por agente. Alerta se >40% ou 0% por muito tempo."
    )
    taxa_bloqueio_operator: Optional[float] = Field(
        default=None,
        description="% de tarefas que travaram no Operator. Alerta se recorrente."
    )
    taxa_aprovacao_sem_refacao: Optional[float] = Field(
        default=None,
        description="% de aprovações sem refação. Alerta se <60% em 2 ciclos."
    )


class SharedMemory(BaseModel):
    """
    Estado compartilhado persistente da squad.

    Regras:
    - Máximo 2.500 tokens (responsabilidade do CoS compactar periodicamente)
    - Entradas curtas e padronizadas
    - Somente o CoS e o Orchestrator podem escrever
    - Todo agente pode ler (via CMO injetado pelo Orchestrator)
    """

    # Identificação do projeto
    projeto: str = Field(..., description="Nome ou código do projeto.")
    versao: int = Field(default=1, description="Versão da Shared Memory (incrementa a cada compactação).")
    ultima_atualizacao: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    atualizado_por: Agente = Field(default=Agente.COS)

    # Contexto vivo
    objetivo_atual: str = Field(
        ...,
        max_length=300,
        description="Objetivo de negócio atual em 1-2 frases."
    )
    fase_atual: Fase = Field(..., description="Fase atual do ciclo de desenvolvimento.")
    restricoes_ativas: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Restrições inegociáveis vigentes."
    )

    # Logs
    decisoes: list[EntradaDecisao] = Field(
        default_factory=list,
        description="Decision log. Máx ~10 entradas antes de compactar."
    )
    riscos: list[EntradaRisco] = Field(
        default_factory=list,
        description="Riscos ativos. Encerrados podem ser removidos na compactação."
    )
    hipoteses: list[EntradaHipotese] = Field(
        default_factory=list,
        description="Hipóteses abertas."
    )
    pendencias: list[EntradaPendencia] = Field(
        default_factory=list,
        description="Pendências abertas com dono."
    )

    # Aprendizados
    aprendizados: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Aprendizados de discovery e execução. Só o que realmente importa."
    )

    # Saúde
    metricas: MetricasSaude = Field(default_factory=MetricasSaude)

    class Config:
        use_enum_values = True

    def decisoes_irreversiveis(self) -> list[EntradaDecisao]:
        """Retorna apenas as decisões que não podem ser reabertas."""
        return [d for d in self.decisoes if d.irreversivel]

    def pendencias_abertas(self) -> list[EntradaPendencia]:
        return [p for p in self.pendencias if p.status == "aberta"]

    def riscos_ativos(self) -> list[EntradaRisco]:
        return [r for r in self.riscos if r.status in ("aberto",)]

    def snapshot_para_cmo(self, max_chars: int = 800) -> str:
        """
        Gera um snapshot compacto da Shared Memory para injeção no CMO.
        O Orchestrator usa isso para montar o campo 'decisoes_previas' do CMO.
        """
        linhas = [
            f"[Shared Memory — {self.projeto} | Fase: {self.fase_atual}]",
            f"Objetivo: {self.objetivo_atual}",
        ]
        if self.restricoes_ativas:
            linhas.append("Restrições: " + " | ".join(self.restricoes_ativas))

        irreversiveis = self.decisoes_irreversiveis()
        if irreversiveis:
            linhas.append("Decisões fechadas:")
            for d in irreversiveis[-3:]:  # últimas 3
                linhas.append(f"  [{d.id}] {d.decisao}")

        pendencias = self.pendencias_abertas()
        if pendencias:
            linhas.append("Pendências abertas:")
            for p in pendencias[-3:]:
                linhas.append(f"  [{p.id}] {p.descricao} → {p.dono}")

        snapshot = "\n".join(linhas)
        return snapshot[:max_chars]
