# Arquitetura Alvo da Squad

## Decisao de stack

A arquitetura alvo usa:

- LangGraph como runtime unico de orquestracao, estado, gates e aprovacao.
- LangSmith para tracing, custo, datasets, feedback e avaliacao.
- LangGraph Agent Server / Deployment para threads, runs e retomada em producao.
- PostgreSQL para checkpoints, projetos, iniciativas, decisoes e aprovacoes.
- Git/GitHub como trilha de entrega, diff, commit e revisao.
- n8n apenas na borda, quando existirem triggers, agendas ou integracoes externas.

Open Agent Platform nao faz parte da arquitetura alvo porque foi descontinuado.

## Principio de interface

A propria squad pode construir a interface operacional, depois que os contratos
de execucao estiverem estaveis. A interface nao deve decidir regras centrais;
ela deve operar o runtime.

Primeiras telas previstas:

- Caixa de entrada de demandas com `project_id` e `initiative_id`.
- Run detail com fluxo, agentes acionados, tier, orcamento, custo e traces.
- Approval inbox para editar, aprovar ou rejeitar efeitos antes de executar.
- Delivery view com diff Git, testes, evidencias QA e artefatos.
- Memory view separando decisoes, bloqueios e historico por iniciativa.

## Fases de implementacao

### M1 - Escopo e custo

- Namespace por projeto/iniciativa.
- Tiers de execucao e orcamento observavel.
- Persistencia e metadata para futura UI.

### M2 - Execution Engine

- Fila persistente de pedidos e decisoes humanas em dry-run. (implementado)
- Approval gate que impede efeitos antes da decisao. (implementado)
- Escrita controlada no workspace.
- Validacao automatica, diff e evidencias.
- Matriz executavel de autorizacoes.

### M3 - Durable HITL

- Checkpointer persistente.
- Interrupt/resume para aprovacoes.
- Recuperacao apos falha e fila de decisoes pendentes.

### M4 - Interface propria

- UI consumindo API do Agent Server.
- Aprovacoes, traces, custo, diffs, testes e memoria por projeto.

### M5 - Automacoes externas

- n8n para triggers e integracoes somente quando houver demanda concreta.
