"""
Squad v5 Lite — Política de Compactação da Shared Memory
==========================================================
Problema resolvido: a Shared Memory não tinha lógica de compactação.
Com o tempo, decisões, riscos, hipóteses e handoffs acumulam até
ultrapassar o limite de 2.500 tokens — e aí o CoS precisa compactar
manualmente, gerando inconsistência.

Solução: CompactionPolicy define QUANDO compactar, CompactionEngine
define O QUE preservar e O QUE descartar, e o resultado é uma nova
versão da SharedMemory dentro do limite, com um CompactionReport
auditável que registra o que foi removido e por quê.

Regras centrais da compactação:
1. Decisões irreversíveis NUNCA são removidas
2. Riscos críticos/altos abertos NUNCA são removidos
3. Handoffs são compactados em sumário por agente (não removidos individualmente)
4. O que foi encerrado/resolvido/arquivado pode ser descartado
5. Cada compactação incrementa `versao` na SharedMemory
6. Um CompactionReport é gerado e armazenado no próprio objeto

Fluxo:
    CoS chama CompactionPolicy.deve_compactar(memoria)
    → True: CompactionEngine.compactar(memoria) → SharedMemory nova + CompactionReport
    → False: nenhuma ação necessária
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field

from contracts import (
    ECE, Agente, Fase, SharedMemory,
    EntradaDecisao, EntradaRisco, EntradaHipotese,
    EntradaPendencia, MetricasSaude,
)


# ---------------------------------------------------------------------------
# Estimador de tokens
# ---------------------------------------------------------------------------

class TokenEstimator:
    """
    Estimativa rápida de tokens sem chamar a API.
    Regra: ~4 chars por token (aproximação conservadora para português).
    """
    CHARS_POR_TOKEN = 4

    @classmethod
    def estimar(cls, texto: str) -> int:
        return max(1, len(texto) // cls.CHARS_POR_TOKEN)

    @classmethod
    def estimar_memoria(cls, memoria: SharedMemory) -> int:
        """Estima o total de tokens da SharedMemory serializada."""
        serializada = memoria.model_dump_json()
        return cls.estimar(serializada)


# ---------------------------------------------------------------------------
# CompactionReport — auditoria do que foi removido
# ---------------------------------------------------------------------------

class ItemRemovido(BaseModel):
    tipo: str = Field(..., description="'decisao' | 'risco' | 'hipotese' | 'pendencia' | 'handoff' | 'aprendizado'")
    id: str
    motivo: str = Field(..., max_length=200)


class CompactionReport(BaseModel):
    """
    Registro auditável de cada compactação.
    Armazenado na SharedMemory para que o CoS possa revisar o que foi descartado.
    """
    versao_antes: int
    versao_depois: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    executado_por: Agente = Field(default=Agente.COS)

    tokens_antes: int
    tokens_depois: int
    reducao_pct: float

    itens_removidos: list[ItemRemovido] = Field(default_factory=list)
    itens_preservados_forcados: list[str] = Field(
        default_factory=list,
        description="IDs de itens que não podiam ser removidos (irreversíveis, críticos)."
    )

    sumario_handoffs: Optional[str] = Field(
        default=None,
        description="Sumário compacto dos handoffs arquivados."
    )

    observacao: Optional[str] = Field(default=None, max_length=300)

    def resumo(self) -> str:
        return (
            f"[Compactação v{self.versao_antes}→v{self.versao_depois}] "
            f"{self.tokens_antes}→{self.tokens_depois} tokens "
            f"({self.reducao_pct:.1f}% redução). "
            f"{len(self.itens_removidos)} item(ns) removido(s). "
            f"{len(self.itens_preservados_forcados)} preservado(s) por regra."
        )


# ---------------------------------------------------------------------------
# CompactionPolicy — decide QUANDO compactar
# ---------------------------------------------------------------------------

class CompactionPolicy:
    """
    Define os gatilhos que indicam que a SharedMemory precisa ser compactada.
    O CoS deve verificar após cada ciclo relevante.
    """

    # Limite máximo em tokens — acima disso compactação é obrigatória
    LIMITE_TOKENS = 2_500

    # Alertas preventivos — compactar antes de chegar no limite
    ALERTA_TOKENS = 2_000       # 80% do limite
    ALERTA_DECISOES = 10        # muitas decisões acumuladas
    ALERTA_HANDOFFS = 20        # handoffs antigos ocupando espaço
    ALERTA_HIPOTESES = 8        # hipóteses obsoletas
    ALERTA_RISCOS_ENCERRADOS = 5  # riscos encerrados ainda na memória

    def deve_compactar(self, memoria: SharedMemory) -> tuple[bool, str]:
        """
        Retorna (deve_compactar, motivo).
        O CoS usa este método para decidir se aciona o CompactionEngine.
        """
        tokens = TokenEstimator.estimar_memoria(memoria)

        # Obrigatório — acima do limite
        if tokens > self.LIMITE_TOKENS:
            return True, f"Limite excedido: {tokens} tokens (máx {self.LIMITE_TOKENS})."

        # Preventivo — acima do alerta de tokens
        if tokens > self.ALERTA_TOKENS:
            return True, f"Alerta preventivo: {tokens} tokens (alerta em {self.ALERTA_TOKENS})."

        # Preventivo — muitas decisões
        if len(memoria.decisoes) > self.ALERTA_DECISOES:
            return True, f"Muitas decisões: {len(memoria.decisoes)} (alerta em {self.ALERTA_DECISOES})."

        # Preventivo — handoffs antigos
        handoffs = getattr(memoria, "handoffs", [])
        if len(handoffs) > self.ALERTA_HANDOFFS:
            return True, f"Handoffs acumulados: {len(handoffs)} (alerta em {self.ALERTA_HANDOFFS})."

        # Preventivo — hipóteses obsoletas
        hipoteses_antigas = [
            h for h in memoria.hipoteses
            if h.status in ("validada", "refutada", "arquivada")
        ]
        if len(hipoteses_antigas) > self.ALERTA_HIPOTESES:
            return True, f"Hipóteses obsoletas: {len(hipoteses_antigas)}."

        # Preventivo — riscos encerrados acumulados
        riscos_encerrados = [
            r for r in memoria.riscos
            if r.status in ("mitigado", "aceito", "encerrado")
        ]
        if len(riscos_encerrados) > self.ALERTA_RISCOS_ENCERRADOS:
            return True, f"Riscos encerrados acumulados: {len(riscos_encerrados)}."

        return False, "Memória dentro dos limites. Compactação não necessária."


# ---------------------------------------------------------------------------
# CompactionEngine — executa a compactação
# ---------------------------------------------------------------------------

class CompactionEngine:
    """
    Executa a compactação da SharedMemory seguindo as regras de preservação.

    Regras de preservação (nunca removidos):
    - Decisões irreversíveis
    - Riscos com severidade 'critica' ou 'alta' e status 'aberto'
    - Pendências abertas com prazo definido
    - Hipóteses críticas ainda abertas

    Regras de remoção (candidatos):
    - Decisões reversíveis mais antigas (além das últimas N)
    - Riscos encerrados/mitigados/aceitos
    - Hipóteses validadas/refutadas/arquivadas
    - Pendências resolvidas
    - Handoffs antigos → compactados em sumário por agente

    Não toca em:
    - objetivo_atual
    - fase_atual
    - restricoes_ativas
    - aprendizados (CoS decide manualmente o que manter)
    - metricas
    """

    # Quantas decisões reversíveis manter (as mais recentes)
    MAX_DECISOES_REVERSIVEIS = 5

    # Quantos handoffs recentes manter intactos (restante vira sumário)
    MAX_HANDOFFS_RECENTES = 5

    def compactar(
        self,
        memoria: SharedMemory,
        executado_por: Agente = Agente.COS,
        observacao: Optional[str] = None,
    ) -> tuple[SharedMemory, CompactionReport]:
        """
        Retorna uma nova SharedMemory compactada + CompactionReport.
        A memória original não é modificada — o CoS deve substituí-la explicitamente.
        """
        tokens_antes = TokenEstimator.estimar_memoria(memoria)
        versao_antes = memoria.versao

        itens_removidos: list[ItemRemovido] = []
        itens_preservados_forcados: list[str] = []

        # --- Compactar decisões ---
        decisoes_novas, removidas_dec, preservadas_dec = self._compactar_decisoes(memoria)
        itens_removidos.extend(removidas_dec)
        itens_preservados_forcados.extend(preservadas_dec)

        # --- Compactar riscos ---
        riscos_novos, removidos_ris, preservados_ris = self._compactar_riscos(memoria)
        itens_removidos.extend(removidos_ris)
        itens_preservados_forcados.extend(preservados_ris)

        # --- Compactar hipóteses ---
        hipoteses_novas, removidas_hip = self._compactar_hipoteses(memoria)
        itens_removidos.extend(removidas_hip)

        # --- Compactar pendências ---
        pendencias_novas, removidas_pen = self._compactar_pendencias(memoria)
        itens_removidos.extend(removidas_pen)

        # --- Compactar handoffs ---
        handoffs_novos, sumario_handoffs = self._compactar_handoffs(memoria)

        # --- Montar nova SharedMemory ---
        nova_memoria = SharedMemory(
            projeto=memoria.projeto,
            versao=versao_antes + 1,
            ultima_atualizacao=datetime.now(timezone.utc),
            atualizado_por=executado_por,
            objetivo_atual=memoria.objetivo_atual,
            fase_atual=memoria.fase_atual,
            restricoes_ativas=memoria.restricoes_ativas,
            decisoes=decisoes_novas,
            riscos=riscos_novos,
            hipoteses=hipoteses_novas,
            pendencias=pendencias_novas,
            aprendizados=memoria.aprendizados,  # CoS decide manualmente
            metricas=memoria.metricas,
        )

        # Preservar handoffs compactados
        nova_memoria.handoffs = handoffs_novos

        # Preservar relatórios de compactação anteriores
        relatorios_anteriores = getattr(memoria, "compaction_reports", [])
        nova_memoria.compaction_reports = relatorios_anteriores  # será adicionado abaixo

        tokens_depois = TokenEstimator.estimar_memoria(nova_memoria)
        reducao = ((tokens_antes - tokens_depois) / tokens_antes * 100) if tokens_antes > 0 else 0.0

        report = CompactionReport(
            versao_antes=versao_antes,
            versao_depois=versao_antes + 1,
            executado_por=executado_por,
            tokens_antes=tokens_antes,
            tokens_depois=tokens_depois,
            reducao_pct=round(reducao, 1),
            itens_removidos=itens_removidos,
            itens_preservados_forcados=itens_preservados_forcados,
            sumario_handoffs=sumario_handoffs,
            observacao=observacao,
        )

        # Adicionar report à nova memória
        nova_memoria.compaction_reports = relatorios_anteriores + [report]

        return nova_memoria, report

    # -----------------------------------------------------------------------
    # Métodos internos
    # -----------------------------------------------------------------------

    def _compactar_decisoes(
        self, memoria: SharedMemory
    ) -> tuple[list[EntradaDecisao], list[ItemRemovido], list[str]]:
        removidos: list[ItemRemovido] = []
        preservados_forcados: list[str] = []

        # Separar irreversíveis (sempre preservar) das reversíveis
        irreversiveis = [d for d in memoria.decisoes if d.irreversivel]
        reversiveis = [d for d in memoria.decisoes if not d.irreversivel]

        for d in irreversiveis:
            preservados_forcados.append(d.id)

        # Manter apenas as N reversíveis mais recentes
        reversiveis_sorted = sorted(
            reversiveis,
            key=lambda d: d.timestamp,
            reverse=True
        )
        reversiveis_manter = reversiveis_sorted[:self.MAX_DECISOES_REVERSIVEIS]
        reversiveis_remover = reversiveis_sorted[self.MAX_DECISOES_REVERSIVEIS:]

        for d in reversiveis_remover:
            removidos.append(ItemRemovido(
                tipo="decisao",
                id=d.id,
                motivo=f"Decisão reversível mais antiga removida na compactação. "
                       f"Decisão: '{d.decisao[:80]}'.",
            ))

        decisoes_finais = irreversiveis + reversiveis_manter
        # Reordenar por timestamp
        decisoes_finais.sort(key=lambda d: d.timestamp)
        return decisoes_finais, removidos, preservados_forcados

    def _compactar_riscos(
        self, memoria: SharedMemory
    ) -> tuple[list[EntradaRisco], list[ItemRemovido], list[str]]:
        removidos: list[ItemRemovido] = []
        preservados_forcados: list[str] = []

        riscos_manter = []
        for r in memoria.riscos:
            # Preservar obrigatoriamente: críticos/altos abertos
            if r.severidade in ("critica", "alta") and r.status == "aberto":
                preservados_forcados.append(r.id)
                riscos_manter.append(r)
            # Remover: encerrados/mitigados/aceitos
            elif r.status in ("mitigado", "aceito", "encerrado"):
                removidos.append(ItemRemovido(
                    tipo="risco",
                    id=r.id,
                    motivo=f"Risco com status '{r.status}' removido. "
                           f"Descrição: '{r.descricao[:80]}'.",
                ))
            else:
                # Médios/baixos abertos: manter
                riscos_manter.append(r)

        return riscos_manter, removidos, preservados_forcados

    def _compactar_hipoteses(
        self, memoria: SharedMemory
    ) -> tuple[list[EntradaHipotese], list[ItemRemovido]]:
        removidos: list[ItemRemovido] = []
        hipoteses_manter = []

        for h in memoria.hipoteses:
            if h.status in ("validada", "refutada", "arquivada"):
                removidos.append(ItemRemovido(
                    tipo="hipotese",
                    id=h.id,
                    motivo=f"Hipótese com status '{h.status}' removida. "
                           f"Hipótese: '{h.hipotese[:80]}'.",
                ))
            else:
                hipoteses_manter.append(h)

        return hipoteses_manter, removidos

    def _compactar_pendencias(
        self, memoria: SharedMemory
    ) -> tuple[list[EntradaPendencia], list[ItemRemovido]]:
        removidos: list[ItemRemovido] = []
        pendencias_manter = []

        for p in memoria.pendencias:
            if p.status == "resolvida":
                removidos.append(ItemRemovido(
                    tipo="pendencia",
                    id=p.id,
                    motivo=f"Pendência resolvida removida. "
                           f"Descrição: '{p.descricao[:80]}'.",
                ))
            else:
                pendencias_manter.append(p)

        return pendencias_manter, removidos

    def _compactar_handoffs(
        self, memoria: SharedMemory
    ) -> tuple[list, Optional[str]]:
        handoffs = getattr(memoria, "handoffs", [])
        if not handoffs:
            return [], None

        if len(handoffs) <= self.MAX_HANDOFFS_RECENTES:
            return handoffs, None

        # Manter os N mais recentes intactos
        handoffs_sorted = sorted(handoffs, key=lambda h: h.timestamp_inicio, reverse=True)
        recentes = handoffs_sorted[:self.MAX_HANDOFFS_RECENTES]
        antigos = handoffs_sorted[self.MAX_HANDOFFS_RECENTES:]

        # Gerar sumário por agente dos handoffs antigos
        sumario_por_agente: dict[str, dict] = {}
        for h in antigos:
            agente = str(h.agente)
            if agente not in sumario_por_agente:
                sumario_por_agente[agente] = {
                    "total": 0, "c3": 0, "aproveitados": 0,
                    "bloqueios": 0, "ultimo_ece": None
                }
            s = sumario_por_agente[agente]
            s["total"] += 1
            if h.e_c3():
                s["c3"] += 1
            if h.foi_aproveitado:
                s["aproveitados"] += 1
            if h.teve_bloqueio:
                s["bloqueios"] += 1
            s["ultimo_ece"] = str(h.ece_output)

        linhas = [f"[Sumário handoffs arquivados — {len(antigos)} sessão(ões)]"]
        for agente, stats in sumario_por_agente.items():
            linhas.append(
                f"  {agente}: {stats['total']} sessão(ões), "
                f"{stats['aproveitados']} aproveitado(s), "
                f"{stats['c3']} C3, "
                f"{stats['bloqueios']} bloqueio(s)."
            )

        sumario = "\n".join(linhas)
        return list(reversed(recentes)), sumario  # mais antigos primeiro


# ---------------------------------------------------------------------------
# CompactionManager — interface única para o CoS e o Orchestrator
# ---------------------------------------------------------------------------

class CompactionManager:
    """
    Interface de alto nível que o CoS usa para gerenciar a compactação.
    Combina CompactionPolicy + CompactionEngine em um fluxo único.
    """

    def __init__(self):
        self.policy = CompactionPolicy()
        self.engine = CompactionEngine()

    def verificar_e_compactar(
        self,
        memoria: SharedMemory,
        executado_por: Agente = Agente.COS,
        forcar: bool = False,
        observacao: Optional[str] = None,
    ) -> tuple[SharedMemory, Optional[CompactionReport]]:
        """
        Verifica se a compactação é necessária e executa se for.
        Retorna (memoria_atualizada, report_ou_None).

        Se forcar=True, compacta independente dos limiares.
        """
        deve, motivo = self.policy.deve_compactar(memoria)

        if not deve and not forcar:
            return memoria, None

        print(f"[CompactionManager] Compactando: {motivo}")
        nova_memoria, report = self.engine.compactar(
            memoria,
            executado_por=executado_por,
            observacao=observacao or motivo,
        )
        return nova_memoria, report

    def status(self, memoria: SharedMemory) -> dict:
        """Retorna um diagnóstico do estado atual da memória."""
        tokens = TokenEstimator.estimar_memoria(memoria)
        deve, motivo = self.policy.deve_compactar(memoria)
        handoffs = getattr(memoria, "handoffs", [])
        reports = getattr(memoria, "compaction_reports", [])

        return {
            "projeto": memoria.projeto,
            "versao": memoria.versao,
            "tokens_estimados": tokens,
            "limite_tokens": CompactionPolicy.LIMITE_TOKENS,
            "uso_pct": round(tokens / CompactionPolicy.LIMITE_TOKENS * 100, 1),
            "precisa_compactar": deve,
            "motivo": motivo,
            "total_decisoes": len(memoria.decisoes),
            "decisoes_irreversiveis": len(memoria.decisoes_irreversiveis()),
            "total_riscos": len(memoria.riscos),
            "riscos_abertos": len(memoria.riscos_ativos()),
            "total_hipoteses": len(memoria.hipoteses),
            "hipoteses_abertas": sum(1 for h in memoria.hipoteses if h.status == "aberta"),
            "pendencias_abertas": len(memoria.pendencias_abertas()),
            "total_handoffs": len(handoffs),
            "compactacoes_anteriores": len(reports),
        }


# ---------------------------------------------------------------------------
# Patch: adicionar campos extras à SharedMemory
# ---------------------------------------------------------------------------

def patch_shared_memory_compaction():
    """Adiciona handoffs e compaction_reports à SharedMemory."""
    from pydantic import fields
    import contracts

    for campo, default in [
        ("handoffs", list),
        ("compaction_reports", list),
    ]:
        if campo not in contracts.SharedMemory.model_fields:
            contracts.SharedMemory.model_fields[campo] = fields.FieldInfo(
                default_factory=default,
                description=f"Campo adicionado pelo patch de compactação.",
            )

    contracts.SharedMemory.model_rebuild(force=True)


# ---------------------------------------------------------------------------
# Exemplo de uso completo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")

    patch_shared_memory_compaction()

    from contracts import (
        SharedMemory, Fase, Agente, ECE,
        EntradaDecisao, EntradaRisco, EntradaHipotese, EntradaPendencia,
    )
    from onboarding import (
        OnboardingForm, OnboardingParser,
        ContextoNegocio, RestricoesCEO, CriteriosSuccesso, DecisaoPrevia, HipoteseInicial,
    )

    # 1. Criar SharedMemory via onboarding
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
    memoria.handoffs = []
    memoria.compaction_reports = []

    # 2. Simular acúmulo de dados ao longo do tempo
    print("=" * 60)
    print("SIMULANDO ACÚMULO DE DADOS AO LONGO DO PROJETO")
    print("=" * 60)

    # Adicionar decisões (mistura de reversíveis e irreversíveis)
    for i in range(8):
        irreversivel = i < 2
        memoria.decisoes.append(EntradaDecisao(
            id=f"D-{str(i+2).zfill(3)}",
            agente=Agente.COS,
            decisao=f"Decisão {'irreversível' if irreversivel else 'reversível'} #{i+1}: "
                    f"escolha de abordagem para feature {i+1}.",
            rationale=f"Rationale da decisão {i+1} tomada pelo CoS após análise.",
            irreversivel=irreversivel,
            ece=ECE.C1 if irreversivel else ECE.C2,
        ))

    # Adicionar riscos (variados status)
    riscos_dados = [
        ("R-003", "Dev senior saiu do projeto.", "alta", "aberto"),
        ("R-004", "Integração com Google Calendar instável.", "media", "mitigado"),
        ("R-005", "LGPD não analisada para cadastro de pacientes.", "critica", "aberto"),
        ("R-006", "Prazo apertado para QA completo.", "media", "aceito"),
        ("R-007", "Banco de dados sem backup configurado.", "alta", "encerrado"),
    ]
    for id_, desc, sev, status in riscos_dados:
        memoria.riscos.append(EntradaRisco(
            id=id_, agente_origem=Agente.COS,
            descricao=desc, severidade=sev, status=status,
        ))

    # Adicionar hipóteses (variados status)
    hipoteses_dados = [
        ("H-002", "Pacientes preferem agendar via link público.", "validada"),
        ("H-003", "Clínicas querem relatório semanal.", "refutada"),
        ("H-004", "Preço de R$300/mês é aceitável.", "aberta"),
        ("H-005", "Integração com WhatsApp aumenta conversão.", "arquivada"),
    ]
    for id_, hip, status in hipoteses_dados:
        memoria.hipoteses.append(EntradaHipotese(
            id=id_, agente_origem=Agente.DISCOVERY,
            hipotese=hip, status=status,
        ))

    # Adicionar pendências (mistas)
    memoria.pendencias.append(EntradaPendencia(
        id="P-002",
        descricao="Eng Lead revisar spec de autenticação.",
        dono=Agente.ENGINEERING,
        status="resolvida",
    ))
    memoria.pendencias.append(EntradaPendencia(
        id="P-003",
        descricao="QA executar test cases de edge cases de agenda.",
        dono=Agente.QA,
        prazo="próximo ciclo",
        status="aberta",
    ))

    manager = CompactionManager()

    # 3. Verificar status antes da compactação
    print("\n[Status ANTES da compactação]")
    status = manager.status(memoria)
    for k, v in status.items():
        print(f"  {k}: {v}")

    # 4. Executar compactação
    print(f"\n{'='*60}")
    print("EXECUTANDO COMPACTAÇÃO")
    print("=" * 60)
    memoria_nova, report = manager.verificar_e_compactar(
        memoria,
        executado_por=Agente.COS,
        observacao="Compactação de rotina ao fim do ciclo de engenharia.",
    )

    if report:
        print(f"\n{report.resumo()}")
        print(f"\nItens removidos ({len(report.itens_removidos)}):")
        for item in report.itens_removidos:
            print(f"  [{item.tipo.upper()}] {item.id} — {item.motivo[:80]}")
        print(f"\nPreservados por regra ({len(report.itens_preservados_forcados)}): "
              f"{report.itens_preservados_forcados}")
        if report.sumario_handoffs:
            print(f"\nSumário de handoffs:\n{report.sumario_handoffs}")

    # 5. Verificar status após compactação
    print(f"\n[Status APÓS compactação]")
    status_novo = manager.status(memoria_nova)
    for k, v in status_novo.items():
        print(f"  {k}: {v}")

    # 6. Verificar que decisões irreversíveis foram preservadas
    print(f"\n[Verificação de integridade]")
    irreversiveis_antes = [d for d in memoria.decisoes if d.irreversivel]
    irreversiveis_depois = [d for d in memoria_nova.decisoes if d.irreversivel]
    print(f"  Decisões irreversíveis antes: {len(irreversiveis_antes)}")
    print(f"  Decisões irreversíveis depois: {len(irreversiveis_depois)}")
    assert len(irreversiveis_antes) == len(irreversiveis_depois), \
        "ERRO: decisão irreversível foi removida!"
    print(f"  ✅ Todas as decisões irreversíveis preservadas.")

    riscos_criticos_abertos_antes = [
        r for r in memoria.riscos if r.severidade in ("critica", "alta") and r.status == "aberto"
    ]
    riscos_criticos_abertos_depois = [
        r for r in memoria_nova.riscos if r.severidade in ("critica", "alta") and r.status == "aberto"
    ]
    print(f"  Riscos críticos/altos abertos antes: {len(riscos_criticos_abertos_antes)}")
    print(f"  Riscos críticos/altos abertos depois: {len(riscos_criticos_abertos_depois)}")
    assert len(riscos_criticos_abertos_antes) == len(riscos_criticos_abertos_depois), \
        "ERRO: risco crítico/alto aberto foi removido!"
    print(f"  ✅ Todos os riscos críticos/altos abertos preservados.")

    # 7. Segunda compactação (deve reportar que não precisa)
    print(f"\n[Segunda verificação — memória já compactada]")
    _, report2 = manager.verificar_e_compactar(memoria_nova)
    if report2 is None:
        print("  ✅ Memória dentro dos limites. Nenhuma ação necessária.")
