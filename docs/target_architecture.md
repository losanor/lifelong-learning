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

Durante o piloto sem mensalidade, LangGraph, SQLite e console rodam localmente,
com LangSmith Developer apenas para observabilidade amostrada. O deployment
Cloud e PostgreSQL gerenciado entram quando a demanda operacional justificar o
plano pago.

## Principio de interface

A propria squad pode construir a interface operacional, depois que os contratos
de execucao estiverem estaveis. A interface nao deve decidir regras centrais;
ela deve operar o runtime.

Telas operacionais locais implementadas:

- Caixa de entrada de demandas com `project_id` e `initiative_id`.
- Board visual com trabalho a iniciar, em andamento, sob decisao humana, em aprovacao, concluido ou bloqueado.
- Orquestracao com mapa configurado da squad, distincao entre agentes fixos/sob demanda, rotas condicionais, gate humano e historico executado por atividade.
- Decisoes humanas pendentes com resposta registrada e retomada auditavel.
- Custos com orcamento estimado agregado por tier/projeto, subtotal de validacoes e sincronizacao manual do custo observado/link dos traces LangSmith.
- Parking lot de ideias com promocao supervisionada para a caixa de entrada.
- Approval inbox para editar, aprovar ou rejeitar efeitos antes de executar.

## Fases de implementacao

### M1 - Escopo e custo

- Namespace por projeto/iniciativa.
- Logs e memoria de novas runs particionados por iniciativa. (implementado)
- Tiers de execucao e orcamento observavel.
- Persistencia e metadata consumidas pela UI operacional. (implementado)

### M2 - Execution Engine

- Fila persistente de pedidos e decisoes humanas em dry-run. (implementado)
- Approval gate que impede efeitos antes da decisao. (implementado)
- Validacao de diff candidato contra alvos autorizados e `git apply --check`. (implementado)
- Segundo checkpoint humano antes de futura aplicacao do patch. (implementado)
- Escrita controlada no workspace com rollback. (implementado)
- Validacao automatica allowlisted, diff e evidencias. (implementado)
- Matriz executavel de autorizacoes. (implementado)

### M3 - Durable HITL

- Checkpointer SQLite append-only para workflow de efeitos. (implementado local)
- Interrupt/resume para aprovacoes e execucao controlada. (implementado local)
- Recuperacao apos falha e fila de decisoes pendentes. (implementado local)
- Empacotamento `langgraph.json` para carregar o grafo no Agent Server. (implementado)

No deployment distribuido, os mesmos estados devem migrar para Agent Server e
PostgreSQL, incluindo checkpoints internos do grafo LangGraph.

### M4 - Interface propria

- Console operacional local consumindo API do runtime. (implementado)
- Aprovacoes, custo/tier, diffs, validacoes, timeline e runs recentes. (implementado)
- Intake manual com previa deterministica de fluxo/custo e disparo supervisionado. (implementado local)
- Board, mapa de orquestracao, execucoes entre agentes, fila de decisoes humanas e parking lot supervisionado. (implementado local)
- Separacao de runs `eval-*`/`baseline-*` das filas diarias, preservando custo de validacao. (implementado local)
- Integrar custo observado e link direto do trace LangSmith por run. (implementado local, sincronizacao manual)
- Consolidar custo observado de runs historicas ou executadas sem trace. (pendente)
- Substituir API local pelo Agent Server no deployment distribuido.

### M5 - Automacoes externas

- Webhook n8n autenticado, idempotente e sem auto-execucao. (implementado)
- Interface apresenta demandas externas para revisao. (implementado)
- Ativar novos triggers somente quando houver demanda concreta.
