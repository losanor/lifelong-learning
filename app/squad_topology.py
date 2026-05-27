"""Topology exposed to the operations console for explaining squad orchestration."""

from __future__ import annotations

from typing import Any


AGENTS = [
    {
        "id": "intake",
        "label": "Entrada",
        "mode": "system",
        "stage": "input",
        "responsibility": "Classificar a demanda e escolher o percurso adequado sem consumir modelo.",
        "trigger": "Toda nova demanda.",
    },
    {
        "id": "product",
        "label": "Produto",
        "mode": "fixed",
        "stage": "core",
        "responsibility": "Transformar a necessidade em escopo, criterios e prioridade de produto.",
        "trigger": "Entregas e decisoes de produto.",
    },
    {
        "id": "qa_planning",
        "label": "QA Planejamento",
        "mode": "fixed",
        "stage": "core",
        "responsibility": "Definir criterios de aceite e plano de verificacao antes da implementacao.",
        "trigger": "Fluxo completo de entrega.",
    },
    {
        "id": "engineering",
        "label": "Engineering",
        "mode": "fixed",
        "stage": "core",
        "responsibility": "Projetar e implementar a mudanca solicitada dentro do escopo autorizado.",
        "trigger": "Entrega ou correcao tecnica.",
    },
    {
        "id": "operator",
        "label": "Operator",
        "mode": "fixed",
        "stage": "core",
        "responsibility": "Preparar efeitos operacionais e impedir alteracoes sem autorizacao.",
        "trigger": "Mudancas com efeito em arquivos ou operacao.",
    },
    {
        "id": "engineering_review",
        "label": "Engineering Review",
        "mode": "fixed",
        "stage": "core",
        "responsibility": "Revisar qualidade tecnica, riscos e aderencia antes da validacao final.",
        "trigger": "Entregas, correcoes e revisoes tecnicas.",
    },
    {
        "id": "qa_execution",
        "label": "QA Execucao",
        "mode": "fixed",
        "stage": "core",
        "responsibility": "Validar evidencias e resultado executavel antes do encerramento.",
        "trigger": "Fluxo completo de entrega.",
    },
    {
        "id": "cos",
        "label": "CoS",
        "mode": "fixed",
        "stage": "governance",
        "responsibility": "Orquestrar a conclusao, solicitar retorno ou elevar uma decisao ao humano.",
        "trigger": "Todo percurso concluido ou bloqueado.",
    },
    {
        "id": "discovery",
        "label": "Discovery",
        "mode": "on_demand",
        "stage": "specialist",
        "responsibility": "Investigar problema, mercado e evidencias antes de comprometer entrega.",
        "trigger": "Pesquisa, benchmark, hipotese ou necessidade de descoberta.",
    },
    {
        "id": "ux_ui",
        "label": "UX/UI",
        "mode": "on_demand",
        "stage": "specialist",
        "responsibility": "Revisar experiencia, jornada e interface quando existe superficie visual.",
        "trigger": "Telas, formularios, interface ou usabilidade.",
    },
    {
        "id": "privacy",
        "label": "Privacy",
        "mode": "on_demand",
        "stage": "specialist",
        "responsibility": "Verificar privacidade e conformidade em tratamento de dados sensiveis.",
        "trigger": "LGPD, dados pessoais, paciente ou compliance.",
    },
    {
        "id": "appsec",
        "label": "AppSec",
        "mode": "on_demand",
        "stage": "specialist",
        "responsibility": "Avaliar ameacas, autenticacao e autorizacao em mudancas sensiveis.",
        "trigger": "Seguranca, login, autenticacao ou permissoes.",
    },
    {
        "id": "writing",
        "label": "Writing",
        "mode": "on_demand",
        "stage": "specialist",
        "responsibility": "Produzir documentacao e comunicacao de entrega quando solicitada.",
        "trigger": "Documentacao, manual, memo ou release note.",
    },
    {
        "id": "human",
        "label": "Decisao Humana",
        "mode": "human",
        "stage": "governance",
        "responsibility": "Resolver decisoes de risco, prioridade ou autorizacao que a squad nao deve tomar sozinha.",
        "trigger": "Escalada do CoS ou aprovacao antes de aplicar efeitos.",
    },
]


CONNECTIONS = [
    {"from": "intake", "to": "product", "kind": "core", "label": "Entrega"},
    {"from": "product", "to": "qa_planning", "kind": "core", "label": "Escopo aprovado"},
    {"from": "qa_planning", "to": "engineering", "kind": "core", "label": "Plano de teste"},
    {"from": "engineering", "to": "operator", "kind": "core", "label": "Mudanca proposta"},
    {"from": "operator", "to": "engineering_review", "kind": "core", "label": "Evidencias"},
    {"from": "engineering_review", "to": "qa_execution", "kind": "core", "label": "Revisao"},
    {"from": "qa_execution", "to": "cos", "kind": "core", "label": "Resultado"},
    {"from": "intake", "to": "discovery", "kind": "conditional", "label": "Pesquisa necessaria"},
    {"from": "intake", "to": "engineering", "kind": "conditional", "label": "Correcao direta"},
    {"from": "intake", "to": "engineering_review", "kind": "conditional", "label": "Revisao tecnica"},
    {"from": "discovery", "to": "product", "kind": "conditional", "label": "Hipotese e evidencia"},
    {"from": "discovery", "to": "cos", "kind": "conditional", "label": "Pesquisa concluida"},
    {"from": "product", "to": "ux_ui", "kind": "conditional", "label": "Experiencia visual"},
    {"from": "product", "to": "cos", "kind": "conditional", "label": "Decisao ou bloqueio"},
    {"from": "engineering", "to": "ux_ui", "kind": "conditional", "label": "Interface"},
    {"from": "ux_ui", "to": "qa_planning", "kind": "conditional", "label": "Experiencia validada"},
    {"from": "ux_ui", "to": "privacy", "kind": "conditional", "label": "Dados pessoais"},
    {"from": "ux_ui", "to": "appsec", "kind": "conditional", "label": "Superficie sensivel"},
    {"from": "ux_ui", "to": "operator", "kind": "conditional", "label": "Ajuste liberado"},
    {"from": "ux_ui", "to": "cos", "kind": "conditional", "label": "Bloqueio"},
    {"from": "qa_planning", "to": "cos", "kind": "conditional", "label": "Bloqueio"},
    {"from": "engineering", "to": "privacy", "kind": "conditional", "label": "Privacidade"},
    {"from": "privacy", "to": "appsec", "kind": "conditional", "label": "Controle sensivel"},
    {"from": "engineering", "to": "appsec", "kind": "conditional", "label": "Seguranca"},
    {"from": "engineering", "to": "cos", "kind": "conditional", "label": "Bloqueio"},
    {"from": "appsec", "to": "operator", "kind": "conditional", "label": "Liberacao tecnica"},
    {"from": "appsec", "to": "cos", "kind": "conditional", "label": "Risco nao aceito"},
    {"from": "privacy", "to": "operator", "kind": "conditional", "label": "Liberacao de dados"},
    {"from": "privacy", "to": "cos", "kind": "conditional", "label": "Risco de privacidade"},
    {"from": "operator", "to": "cos", "kind": "conditional", "label": "Efeito bloqueado"},
    {"from": "engineering_review", "to": "privacy", "kind": "conditional", "label": "Risco identificado"},
    {"from": "engineering_review", "to": "appsec", "kind": "conditional", "label": "Seguranca identificada"},
    {"from": "engineering_review", "to": "cos", "kind": "conditional", "label": "Revisao bloqueada"},
    {"from": "intake", "to": "writing", "kind": "conditional", "label": "Documentacao"},
    {"from": "writing", "to": "cos", "kind": "conditional", "label": "Entrega documental"},
    {"from": "cos", "to": "human", "kind": "decision", "label": "Escalada ou aprovacao"},
    {"from": "cos", "to": "product", "kind": "return", "label": "Refinar escopo"},
    {"from": "cos", "to": "qa_planning", "kind": "return", "label": "Replanejar teste"},
    {"from": "cos", "to": "engineering", "kind": "return", "label": "Revisar implementacao"},
    {"from": "cos", "to": "operator", "kind": "return", "label": "Rever efeito"},
]


INTEGRATIONS = [
    {"id": "github", "label": "Git / GitHub", "purpose": "Entrega, diff, commit e revisao."},
    {"id": "langsmith", "label": "LangSmith", "purpose": "Trace, custo e avaliacao."},
    {"id": "n8n", "label": "n8n", "purpose": "Entrada autenticada de demandas externas."},
]


def squad_topology_snapshot() -> dict[str, Any]:
    """Return the configured interaction model used by the visual console."""
    return {
        "basis": "configured_runtime",
        "core_path": [
            "intake",
            "product",
            "qa_planning",
            "engineering",
            "operator",
            "engineering_review",
            "qa_execution",
            "cos",
        ],
        "agents": AGENTS,
        "connections": CONNECTIONS,
        "integrations": INTEGRATIONS,
    }
