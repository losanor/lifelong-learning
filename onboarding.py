"""
Squad v5 Lite — Onboarding do CoS
===================================
Problema resolvido: Shared Memory começa vazia.

Solução: formulário estruturado que o CEO preenche antes da squad rodar.
O CoS valida as respostas e popula a Shared Memory com contexto real,
garantindo que o primeiro CMO já tenha substância.

Fluxo:
    1. CEO preenche OnboardingForm (interativo ou via dict)
    2. CoS valida com OnboardingValidator
    3. OnboardingParser converte em SharedMemory populada
    4. Squad começa a rodar com contexto real
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, model_validator

from contracts import (
    SharedMemory, ECE, Agente, Fase,
    EntradaDecisao, EntradaRisco, EntradaHipotese,
    EntradaPendencia, MetricasSaude,
)


# ---------------------------------------------------------------------------
# Formulário de Onboarding
# ---------------------------------------------------------------------------

class ContextoNegocio(BaseModel):
    """Bloco 1 — O CEO descreve o problema e o contexto."""

    problema_central: str = Field(
        ...,
        max_length=500,
        description=(
            "Qual problema de negócio você está tentando resolver? "
            "Descreva o sintoma, não a solução."
        )
    )
    por_que_agora: str = Field(
        ...,
        max_length=300,
        description="O que mudou que torna esse problema prioritário agora?"
    )
    custo_de_nao_resolver: str = Field(
        ...,
        max_length=300,
        description="Qual o custo de não resolver isso nos próximos 3 meses?"
    )
    contexto_adicional: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Histórico, tentativas anteriores, ou qualquer contexto relevante."
    )


class RestricoesCEO(BaseModel):
    """Bloco 2 — Restrições inegociáveis que a squad não pode ignorar."""

    prazo: str = Field(
        ...,
        max_length=100,
        description="Prazo máximo. Ex: '6 semanas', 'até 30/06', 'sem data definida'."
    )
    orcamento: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Limite de custo, se houver. Ex: 'R$ 50k', 'sem budget definido'."
    )
    stack_ou_tecnologia: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Restrições de stack ou tecnologia. Ex: 'Python + AWS', 'sem restrição'."
    )
    equipe: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Quem está disponível para executar. Ex: '1 dev senior, 1 junior'."
    )
    restricoes_adicionais: list[str] = Field(
        default_factory=list,
        description="Outras restrições inegociáveis não cobertas acima."
    )

    def para_lista(self) -> list[str]:
        """Serializa todas as restrições como lista simples para o CMO."""
        itens = []
        if self.prazo:
            itens.append(f"Prazo: {self.prazo}")
        if self.orcamento:
            itens.append(f"Orçamento: {self.orcamento}")
        if self.stack_ou_tecnologia:
            itens.append(f"Stack: {self.stack_ou_tecnologia}")
        if self.equipe:
            itens.append(f"Equipe: {self.equipe}")
        itens.extend(self.restricoes_adicionais)
        return itens[:10]  # máximo 10 restrições na Shared Memory


class DecisaoPrevia(BaseModel):
    """Uma decisão já tomada antes da squad começar."""

    descricao: str = Field(..., max_length=300)
    rationale: str = Field(..., max_length=300)
    irreversivel: bool = Field(
        default=False,
        description="True se esta decisão não pode ser reaberta pela squad."
    )


class HipoteseInicial(BaseModel):
    """Uma hipótese que o CEO traz para a squad validar."""

    hipotese: str = Field(..., max_length=300)
    base: str = Field(
        ...,
        max_length=300,
        description="Em que se baseia essa hipótese? Dado, intuição, experiência?"
    )
    critica: bool = Field(
        default=False,
        description="True se o projeto depende que essa hipótese seja verdadeira."
    )


class RiscoPercebido(BaseModel):
    """Um risco que o CEO já percebe antes de começar."""

    descricao: str = Field(..., max_length=300)
    severidade: str = Field(
        ...,
        description="'baixa' | 'media' | 'alta' | 'critica'."
    )


class CriteriosSuccesso(BaseModel):
    """Bloco 5 — Como o CEO vai saber que deu certo."""

    definicao_de_sucesso: str = Field(
        ...,
        max_length=400,
        description=(
            "Como você vai saber, em 30-60 dias, que isso valeu a pena? "
            "Seja específico: métrica, comportamento observável, evento."
        )
    )
    mvp_minimo: str = Field(
        ...,
        max_length=400,
        description=(
            "Qual é a versão mais simples que ainda entrega valor real? "
            "O que você removeria se tivesse apenas 30% do prazo?"
        )
    )
    nao_e_sucesso: Optional[str] = Field(
        default=None,
        max_length=300,
        description="O que explicitamente NÃO é critério de sucesso nesta fase?"
    )


class OnboardingForm(BaseModel):
    """
    Formulário completo de onboarding preenchido pelo CEO.
    O CoS valida e converte em SharedMemory populada.
    """

    # Metadados
    projeto: str = Field(..., description="Nome ou código do projeto.")
    data_preenchimento: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Blocos do formulário
    contexto: ContextoNegocio
    restricoes: RestricoesCEO
    decisoes_previas: list[DecisaoPrevia] = Field(
        default_factory=list,
        description="Decisões já tomadas antes da squad começar. Pode ser vazio."
    )
    hipoteses: list[HipoteseInicial] = Field(
        default_factory=list,
        description="Hipóteses que a squad deve validar. Pode ser vazio."
    )
    riscos_percebidos: list[RiscoPercebido] = Field(
        default_factory=list,
        description="Riscos que o CEO já percebe. Pode ser vazio."
    )
    criterios: CriteriosSuccesso

    # Aprovação do CoS
    aprovado_pelo_cos: bool = Field(
        default=False,
        description="O CoS confirmou que o formulário está completo o suficiente para começar."
    )
    observacoes_cos: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Observações do CoS ao revisar o formulário."
    )

    @model_validator(mode="after")
    def validar_completude(self) -> "OnboardingForm":
        """Garante campos críticos não vazios."""
        if len(self.contexto.problema_central.strip()) < 20:
            raise ValueError(
                "problema_central muito curto. Descreva o sintoma com mais detalhe."
            )
        if len(self.criterios.definicao_de_sucesso.strip()) < 20:
            raise ValueError(
                "definicao_de_sucesso muito curta. Seja específico sobre como medir sucesso."
            )
        return self


# ---------------------------------------------------------------------------
# Validador do CoS
# ---------------------------------------------------------------------------

class ResultadoValidacao(BaseModel):
    """Resultado da validação do CoS sobre o formulário de onboarding."""
    aprovado: bool
    lacunas: list[str] = Field(default_factory=list)
    perguntas_adicionais: list[str] = Field(default_factory=list)
    observacao: str = ""


class OnboardingValidator:
    """
    O CoS usa este validador para verificar se o formulário está
    completo o suficiente para a squad começar a rodar.

    Regra: se houver lacunas críticas, o CoS devolve ao CEO antes
    de popular a Shared Memory.
    """

    CAMPOS_CRITICOS = [
        "contexto.problema_central",
        "contexto.por_que_agora",
        "restricoes.prazo",
        "criterios.definicao_de_sucesso",
        "criterios.mvp_minimo",
    ]

    def validar(self, form: OnboardingForm) -> ResultadoValidacao:
        lacunas = []
        perguntas = []

        # Verificar hipóteses críticas sem base
        for i, h in enumerate(form.hipoteses):
            if h.critica and len(h.base.strip()) < 15:
                lacunas.append(
                    f"Hipótese crítica #{i+1} sem base suficiente: '{h.hipotese[:60]}...'"
                )
                perguntas.append(
                    f"Em que dado ou experiência se baseia a hipótese '{h.hipotese[:40]}'?"
                )

        # Verificar restrições de equipe para prazo curto
        prazo_lower = form.restricoes.prazo.lower()
        prazo_curto = any(p in prazo_lower for p in ["semana", "dias", "semanas"])
        if prazo_curto and not form.restricoes.equipe:
            lacunas.append("Prazo curto definido mas equipe disponível não foi especificada.")
            perguntas.append("Quem está disponível para executar dentro deste prazo?")

        # Verificar se MVP mínimo é realmente mínimo
        if len(form.criterios.mvp_minimo.split()) > 80:
            lacunas.append(
                "mvp_minimo parece complexo demais. MVP deve ser a versão mais simples possível."
            )
            perguntas.append(
                "Se você tivesse apenas 30% do prazo, o que removeria do MVP descrito?"
            )

        # Verificar riscos críticos sem dono
        riscos_criticos = [r for r in form.riscos_percebidos if r.severidade == "critica"]
        if riscos_criticos and not form.decisoes_previas:
            perguntas.append(
                f"Há {len(riscos_criticos)} risco(s) crítico(s) identificado(s). "
                "Existe alguma decisão já tomada para mitigá-los?"
            )

        aprovado = len(lacunas) == 0

        obs_partes = []
        if aprovado:
            obs_partes.append("Formulário completo. Squad pode iniciar.")
        else:
            obs_partes.append(f"{len(lacunas)} lacuna(s) identificada(s). Devolver ao CEO.")
        if perguntas:
            obs_partes.append(f"{len(perguntas)} pergunta(s) adicional(is) recomendada(s).")

        return ResultadoValidacao(
            aprovado=aprovado,
            lacunas=lacunas,
            perguntas_adicionais=perguntas,
            observacao=" ".join(obs_partes),
        )


# ---------------------------------------------------------------------------
# Parser — converte OnboardingForm em SharedMemory populada
# ---------------------------------------------------------------------------

class OnboardingParser:
    """
    Converte um OnboardingForm aprovado em uma SharedMemory
    populada com contexto real, pronta para o primeiro CMO.
    """

    def converter(self, form: OnboardingForm) -> SharedMemory:
        if not form.aprovado_pelo_cos:
            raise ValueError(
                "OnboardingForm não foi aprovado pelo CoS. "
                "Valide com OnboardingValidator antes de converter."
            )

        # Montar objetivo consolidado
        objetivo = (
            f"{form.contexto.problema_central} "
            f"[Sucesso: {form.criterios.definicao_de_sucesso}]"
        )[:300]

        # Converter decisões prévias
        decisoes = []
        for i, d in enumerate(form.decisoes_previas):
            decisoes.append(EntradaDecisao(
                id=f"D-{str(i+1).zfill(3)}",
                agente=Agente.CEO,
                decisao=d.descricao,
                rationale=d.rationale,
                irreversivel=d.irreversivel,
                ece=ECE.C1,  # decisões do CEO são C1 por definição
            ))

        # Converter riscos percebidos
        riscos = []
        for i, r in enumerate(form.riscos_percebidos):
            riscos.append(EntradaRisco(
                id=f"R-{str(i+1).zfill(3)}",
                agente_origem=Agente.CEO,
                descricao=r.descricao,
                severidade=r.severidade,
                status="aberto",
            ))

        # Converter hipóteses
        hipoteses = []
        for i, h in enumerate(form.hipoteses):
            hipoteses.append(EntradaHipotese(
                id=f"H-{str(i+1).zfill(3)}",
                agente_origem=Agente.CEO,
                hipotese=h.hipotese,
                status="aberta",
                proxima_acao=(
                    "Discovery deve validar esta hipótese." if h.critica
                    else "Validar quando houver oportunidade."
                ),
            ))

        # Montar aprendizados iniciais a partir do contexto adicional
        aprendizados = []
        if form.contexto.contexto_adicional:
            aprendizados.append(f"[CEO] {form.contexto.contexto_adicional}"[:200])
        if form.criterios.nao_e_sucesso:
            aprendizados.append(f"[Fora de escopo nesta fase] {form.criterios.nao_e_sucesso}"[:200])

        # Pendência automática: MVP mínimo deve ser validado pelo Product Lead
        pendencias = [
            EntradaPendencia(
                id="P-001",
                descricao=f"Product Lead deve validar MVP: {form.criterios.mvp_minimo[:150]}",
                dono=Agente.PRODUCT,
                prazo="fase de produto",
            )
        ]

        return SharedMemory(
            projeto=form.projeto,
            objetivo_atual=objetivo,
            fase_atual=Fase.IDEACAO,
            restricoes_ativas=form.restricoes.para_lista(),
            decisoes=decisoes,
            riscos=riscos,
            hipoteses=hipoteses,
            pendencias=pendencias,
            aprendizados=aprendizados,
            atualizado_por=Agente.COS,
            metricas=MetricasSaude(),
        )


# ---------------------------------------------------------------------------
# Interface interativa (CLI simples)
# ---------------------------------------------------------------------------

def onboarding_interativo() -> OnboardingForm:
    """
    Conduz o CEO pelo preenchimento do formulário via terminal.
    Útil para testes locais antes de integrar com LangGraph.
    """
    print("\n" + "=" * 60)
    print("SQUAD V5 LITE — ONBOARDING DO CEO")
    print("Chief of Staff conduzindo a sessão")
    print("=" * 60)

    print("\n[BLOCO 1 — Contexto do Negócio]")
    problema = input("Qual problema de negócio você está tentando resolver?\n> ").strip()
    por_que = input("\nPor que agora? O que mudou?\n> ").strip()
    custo = input("\nQual o custo de não resolver nos próximos 3 meses?\n> ").strip()
    contexto_adicional = input("\nContexto adicional (Enter para pular):\n> ").strip() or None

    print("\n[BLOCO 2 — Restrições]")
    prazo = input("Prazo máximo:\n> ").strip()
    orcamento = input("Orçamento (Enter para pular):\n> ").strip() or None
    stack = input("Restrições de stack/tecnologia (Enter para pular):\n> ").strip() or None
    equipe = input("Equipe disponível (Enter para pular):\n> ").strip() or None

    print("\n[BLOCO 3 — Critérios de Sucesso]")
    sucesso = input("Como você vai saber que deu certo em 30-60 dias?\n> ").strip()
    mvp = input("\nQual a versão mais simples que ainda entrega valor real?\n> ").strip()
    nao_sucesso = input("\nO que NÃO é critério de sucesso nesta fase? (Enter para pular):\n> ").strip() or None

    projeto = input("\nNome do projeto:\n> ").strip()

    form = OnboardingForm(
        projeto=projeto,
        contexto=ContextoNegocio(
            problema_central=problema,
            por_que_agora=por_que,
            custo_de_nao_resolver=custo,
            contexto_adicional=contexto_adicional,
        ),
        restricoes=RestricoesCEO(
            prazo=prazo,
            orcamento=orcamento,
            stack_ou_tecnologia=stack,
            equipe=equipe,
        ),
        criterios=CriteriosSuccesso(
            definicao_de_sucesso=sucesso,
            mvp_minimo=mvp,
            nao_e_sucesso=nao_sucesso,
        ),
    )
    return form


# ---------------------------------------------------------------------------
# Exemplo de uso completo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("SQUAD V5 LITE — ONBOARDING: EXEMPLO COMPLETO")
    print("=" * 60)

    # 1. CEO preenche o formulário (simulado)
    form = OnboardingForm(
        projeto="agendamento-clinicas-v1",
        contexto=ContextoNegocio(
            problema_central=(
                "Clínicas pequenas perdem consultas por falta de sistema de agendamento. "
                "Hoje dependem de WhatsApp e planilha, gerando conflitos de horário e "
                "cancelamentos sem aviso."
            ),
            por_que_agora=(
                "Três clínicas parceiras pediram solução nos últimos 30 dias. "
                "Concorrente direto lançou produto similar com preço alto."
            ),
            custo_de_nao_resolver=(
                "Perder janela de entrada no mercado. Clínicas parceiras podem "
                "contratar concorrente antes de termos algo pronto."
            ),
            contexto_adicional=(
                "Tentamos integrar com sistema legado há 6 meses e abandonamos. "
                "Desta vez, MVP independente sem integração."
            ),
        ),
        restricoes=RestricoesCEO(
            prazo="6 semanas",
            orcamento="R$ 30k",
            stack_ou_tecnologia="Python + FastAPI + React. Sem mobile nesta fase.",
            equipe="1 dev senior (40h/sem), 1 dev junior (20h/sem).",
            restricoes_adicionais=[
                "Sem dados pessoais de pacientes no MVP (LGPD).",
                "Deploy obrigatório em cloud (não on-premise).",
            ],
        ),
        decisoes_previas=[
            DecisaoPrevia(
                descricao="MVP sem integração com sistemas legados de prontuário.",
                rationale="Integração travou projeto anterior por 6 meses. Escopo fora do MVP.",
                irreversivel=True,
            ),
            DecisaoPrevia(
                descricao="Modelo de negócio: SaaS com cobrança mensal por clínica.",
                rationale="Receita recorrente previsível. Validado com 2 clínicas parceiras.",
                irreversivel=False,
            ),
        ],
        hipoteses=[
            HipoteseInicial(
                hipotese="Clínicas pagarão R$ 200-400/mês por sistema de agendamento simples.",
                base="Conversas informais com 3 proprietários de clínicas.",
                critica=True,
            ),
            HipoteseInicial(
                hipotese="Pacientes preferem agendar online a ligar para a clínica.",
                base="Tendência de mercado observada em outros setores.",
                critica=False,
            ),
        ],
        riscos_percebidos=[
            RiscoPercebido(
                descricao="Dev senior pode sair durante o projeto (está em processo seletivo).",
                severidade="alta",
            ),
            RiscoPercebido(
                descricao="Clínicas parceiras podem não adotar o produto após MVP.",
                severidade="media",
            ),
        ],
        criterios=CriteriosSuccesso(
            definicao_de_sucesso=(
                "3 clínicas usando o sistema ativamente em 60 dias, "
                "com pelo menos 20 agendamentos/mês cada."
            ),
            mvp_minimo=(
                "Cadastro de clínica, agenda semanal configurável, "
                "link de agendamento público para paciente, confirmação por email."
            ),
            nao_e_sucesso=(
                "Número de features. Integração com prontuário. "
                "App mobile. Relatórios avançados."
            ),
        ),
    )

    print("\n✅ Formulário preenchido pelo CEO.")

    # 2. CoS valida
    validator = OnboardingValidator()
    resultado = validator.validar(form)
    print(f"\n[CoS — Validação]")
    print(f"Aprovado: {resultado.aprovado}")
    print(f"Observação: {resultado.observacao}")
    if resultado.lacunas:
        print(f"Lacunas: {resultado.lacunas}")
    if resultado.perguntas_adicionais:
        print(f"Perguntas adicionais: {resultado.perguntas_adicionais}")

    # 3. CoS aprova e converte
    if resultado.aprovado:
        form.aprovado_pelo_cos = True
        form.observacoes_cos = resultado.observacao

        parser = OnboardingParser()
        memoria = parser.converter(form)

        print(f"\n✅ Shared Memory populada pelo CoS:")
        print(f"  Projeto: {memoria.projeto}")
        print(f"  Fase: {memoria.fase_atual}")
        print(f"  Restrições: {len(memoria.restricoes_ativas)} item(ns)")
        print(f"  Decisões prévias: {len(memoria.decisoes)}")
        print(f"  Riscos: {len(memoria.riscos)}")
        print(f"  Hipóteses: {len(memoria.hipoteses)}")
        print(f"  Pendências: {len(memoria.pendencias)}")
        print(f"  Aprendizados: {len(memoria.aprendizados)}")

        print(f"\n[Snapshot para primeiro CMO]")
        print(memoria.snapshot_para_cmo())
    else:
        print("\n⛔ Formulário devolvido ao CEO para revisão.")
