# Squad v5 Lite

Squad multiagente enxuta para transformar um objetivo em discovery, escopo,
plano de QA, especificacao tecnica, pacote de implementacao, review de
engenharia, QA de execucao e decisao do Chief of Staff.

## Estrutura operacional

Fluxo padrao:

`Intake -> Product -> QA Planning -> Engineering -> Operator -> Engineering Review -> QA Execution -> CoS`

`Discovery` entra antes de Product apenas quando o intake detectar necessidade
de evidencia externa/mercado ou quando a execucao receber override explicito.

Fluxos adaptativos do intake:

- `delivery_core`: Product -> QA Planning -> Engineering -> Operator -> Engineering Review -> QA Execution -> CoS.
- `delivery_with_discovery`: Discovery + ciclo de entrega.
- `bugfix`: Engineering -> Operator -> Engineering Review -> QA Execution -> CoS.
- `docs`: Writing -> CoS.
- `review`: Engineering Review -> CoS.
- `decision_only`: Product -> CoS.
- `research_only`: Discovery -> CoS.

Gates sob demanda:

- `UX/UI`: entra entre Product e QA Planning quando a entrega envolve
  interface, tela, formulario ou jornada.
- `Privacy`: entra antes do Operator quando houver LGPD, dados pessoais,
  paciente ou compliance; em review tecnico, entra antes do CoS.
- `AppSec`: entra antes do Operator quando houver autenticacao, login,
  permissoes ou seguranca; em review tecnico, entra antes do CoS.

Camadas compartilhadas:

- `docs/target_architecture.md`: stack alvo e roadmap da interface propria.
- `app/project_scope.py`: namespace de projeto/iniciativa para memoria futura.
- `app/execution_policy.py`: tiers, budgets e aprovacoes previstas.
- `app/execution_engine.py`: fila de execucao e approval gate em modo dry-run.
- `app/workflow_recovery.py`: checkpoints duraveis e retomada da fila HITL.
- `contracts.py`: CMO, Resumo Estruturado, Shared Memory e ECE.
- `cmo_especializado.py`: factories de CMO para handoffs estruturados.
- `app/intake.py`: politica de agentes fixos e sinais de especialistas sob demanda.
- `app/workspace_context.py`: snapshot local de Git, docs e arquivos disponiveis
  para trabalho real sem depender de LLM.
- `app/operational_store.py`: persistencia SQLite de runs, outputs e metricas.
- `app/evals.py`: bateria deterministica de cenarios para medir aplicacao real.
- `app/orchestrator.py`: CMO textual e checks de Resumo Estruturado/ECE no grafo.
- `data/`: logs, memoria compacta, politicas, decisoes e escaladas.
- `app/prompts/`: prompts do nucleo fixo e dos especialistas sob demanda.

O fluxo padrao mantem especialistas sob demanda fora do caminho quente. UX/UI,
Privacy, AppSec, Writing e Data Scientist entram quando seus gatilhos existem.
O intake registra esses sinais em `on_demand_agents`; Discovery ja e roteado no
grafo por esse mecanismo. UX/UI, Privacy e AppSec agora tambem executam como
gates reais apenas quando seus gatilhos forem detectados.

Cada intake classifica a demanda em `quick`, `standard`, `full` ou
`controlled`. Nesta etapa, budgets sao `advisory`: ficam persistidos e seguem
para tracing/UI futura. Enforcement automatico entra com o Execution Engine e
o fluxo HITL duravel.

## Execucao local

1. Crie ou reutilize a `.venv`.
2. Instale `requirements.txt`.
3. Configure `.env`.
4. Use mock para validar a estrutura sem API:

```powershell
$env:USE_MOCK_MODEL="true"
.\.venv\Scripts\python.exe -m app.main
```

Para o modelo real, configure `ANTHROPIC_API_KEY` e deixe
`USE_MOCK_MODEL=false`.

Para executar a bateria operacional no modo mock:

```powershell
$env:USE_MOCK_MODEL="true"
.\.venv\Scripts\python.exe -m app.evals
```

O runtime grava `data/squad_runtime.sqlite3` localmente. O banco nao entra no
Git; ele serve para consultar volume, acionabilidade, C3, escaladas, duracao e
resultados dos evals. Runs novos tambem registram `project_id`, `initiative_id`,
namespace de memoria e tier/orcamento de execucao.

Quando `app.main` conclui uma run, o Execution Engine registra uma solicitacao.
Pacotes `execution_ready: true` ficam em `pending_approval`; pacotes bloqueados
ficam em `not_actionable`. A primeira aprovacao permite preparar um diff
candidato, que precisa afetar somente `target_refs` declarados e passar em
`git apply --check`. Um patch valido fica em `awaiting_apply_approval`; a
segunda aprovacao o move para `approved_for_apply`, ainda sem escrever no
workspace. O comando `apply` aplica somente esse patch aprovado, executa
validacoes allowlisted e reverte automaticamente a alteracao se alguma falhar.
Commit e push permanecem manuais.

Para consultar e decidir a fila local:

```powershell
.\.venv\Scripts\python.exe -m app.review_execution list
.\.venv\Scripts\python.exe -m app.review_execution approve <request_id> --by owner --notes "Preparar diff."
.\.venv\Scripts\python.exe -m app.review_execution prepare <request_id> --patch-file .\candidate.patch
.\.venv\Scripts\python.exe -m app.review_execution approve-apply <request_id> --by owner --notes "Patch validado."
.\.venv\Scripts\python.exe -m app.review_execution apply <request_id> --validate git_diff_check --validate python_compile
.\.venv\Scripts\python.exe -m app.review_execution rollback <request_id> --reason "Cancelar entrega."
.\.venv\Scripts\python.exe -m app.review_execution reject <request_id> --by owner --notes "Revisar escopo."
.\.venv\Scripts\python.exe -m app.workflow_recovery pending
.\.venv\Scripts\python.exe -m app.workflow_recovery resume <request_id>
```

Para iniciar uma baseline real rastreada, primeiro configure chaves somente em
`.env` (nunca em `.env.example`) e rode um piloto de baixo custo:

```powershell
.\.venv\Scripts\python.exe -m app.baseline --limit 3
```

Depois de avaliar os tres traces e custos no LangSmith, rode a bateria completa:

```powershell
.\.venv\Scripts\python.exe -m app.baseline --full
```

Cada caso gera um foco de revisao humana. Registre as cinco notas de `0` a `10`
com:

```powershell
.\.venv\Scripts\python.exe -m app.review_baseline <baseline_id> <scenario_id> --correctness 9 --practical-utility 9 --scope-control 9 --next-step-clarity 9 --execution-confidence 9 --notes "Aprovado."
```

## Observabilidade

LangSmith e o caminho recomendado para tracing e avaliacao do grafo. Copie os
campos de `.env.example` para `.env`, defina `LANGSMITH_TRACING=true`,
`LANGSMITH_API_KEY` e `LANGSMITH_PROJECT`. O runtime injeta `run_id`, fluxo
ativo, estado Git e tags `gate:<agente>` em cada execucao via
`app/observability.py`.
Na baseline real, o runtime tambem publica `baseline_auto_score` como feedback
automatico do trace; a rubrica humana permanece no SQLite local.
Se o provedor falhar antes de produzir artefato, a baseline registra
`provider_failed`, retorna `automatic_score: null` e exclui essa execucao da
avaliacao de qualidade da squad.
Se um agente retornar JSON invalido, o runtime tenta uma unica correcao guiada
pelo erro de schema e persiste o diagnostico e a ocorrencia de reparo.
A baseline inclui um guardrail de governanca: diante de bloqueios ativos,
encerrar em `C3` com pacote `execution_ready: false` e sem acionar
implementacao e comportamento correto, nao falha.

Use Markdown/logs locais como trilha operacional simples e LangSmith para
traces, comparacao de execucoes, monitoramento e evals. Depois de medir volume,
ajuste `LANGSMITH_TRACING_SAMPLING_RATE` para controlar custo de tracing.

LangSmith nao substitui as metricas de processo da squad, como output
aproveitado, C3 por agente, retrabalho, bloqueio do Operator e aprovacao sem
refacao. Essas metricas continuam sendo dominio do runtime e podem ser
publicadas no tracing como metadata/feedback quando houver baseline real.

## O que esta validado

- O grafo registra handoffs, rotas, retries e escaladas humanas.
- O Orchestrator injeta CMO textual e bloqueia passagem direta quando resumo/ECE
  falham ou quando o output final do agente e C3.
- Outputs criticos usam envelope JSON validado por Pydantic; Markdown segue no
  campo `artifact_markdown` como artefato humano.
- Cada envelope tambem carrega `operational_artifact` com refs afetadas, acoes
  recomendadas, verificacoes e proximos passos Git/docs quando cabiveis.
- O CoS consolida o envelope final em `operational_packet`, uma saida curta com
  decisao, refs, acoes, verificacoes, checkpoint humano e acoes Git/docs
  compativeis com o workspace detectado.
- O intake captura um `Workspace Context` deterministico. Agentes recebem
  branch/status quando houver repositorio e sao instruidos a nao presumir PR,
  branch ou commit quando o workspace for apenas local/documental.
- O CoS recebe os checks operacionais antes de decidir a rota.
- O CoS usa contexto decisorio compacto no caminho normal; `full_context` fica
  para auditoria e debug.
- O SQLite registra runs e artefatos estruturados sem depender de parsing de
  logs Markdown.
- O Execution Engine registra pedidos, diffs candidatos, validacao de
  aplicabilidade, aplicacao controlada, validacoes allowlisted e rollback.
- Cada transicao humana/executora produz checkpoint SQLite append-only; apos
  reinicio, `workflow_recovery` recupera itens pendentes e sua timeline.
- O intake cria namespace deterministico por projeto/iniciativa e persiste uma
  politica de custo/aprovacao para a futura interface operacional.
- Os evals cobrem feature, bugfix, documentacao, review, decisao, research,
  entrega com Discovery, entrega sensivel e review de seguranca; a meta minima
  de aceite e nota `8.7`.
- A camada contratual Pydantic cobre CMO estruturado, Shared Memory,
  observabilidade de handoffs e metricas de saude.

## Limites atuais

- O contrato estruturado depende do provedor obedecer JSON puro; saidas que nao
  validarem viram falha C3 e sao roteadas ao CoS.
- Discovery real precisa de fontes fornecidas ao agente ou de uma integracao de
  pesquisa; sem fonte factual o prompt deve marcar C3.
- O `Workspace Context` informa refs e estado Git, mas nao executa alteracoes
  sozinho; implementation, docs e commits continuam dependendo do operador
  que aplicar o pacote operacional.
- A aplicacao controlada ainda nao cria commits ou publica branches; esses
  efeitos Git seguem como operacao deliberada apos revisar evidencias.
- O checkpoint duravel local cobre o workflow de efeitos e aprovacoes. Em
  producao distribuida, o backend recomendado continua sendo Agent Server com
  PostgreSQL para retomar tambem a execucao interna do grafo.
- O namespace ja e registrado, mas os arquivos de memoria atuais ainda
  precisam ser migrados para armazenamento particionado por iniciativa.
- Validacao automatica de performance do modelo depende do provedor real,
  latencia de rede, tamanho de contexto e volume dos logs compactados.
- A bateria inicial usa o mock para medir arquitetura e contratos; a proxima
  baseline deve rodar com modelo real e custos/latencias observados no LangSmith.

Referencias oficiais para operar LangSmith:

- Metadata e tags: https://docs.langchain.com/langsmith/add-metadata-tags
- Avaliacao de grafos: https://docs.langchain.com/langsmith/evaluate-graph
- Sampling de traces: https://docs.langchain.com/langsmith/sample-traces
