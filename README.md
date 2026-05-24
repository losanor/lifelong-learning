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

Camadas compartilhadas:

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
grafo por esse mecanismo.

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
resultados dos evals.

## Observabilidade

LangSmith e o caminho recomendado para tracing e avaliacao do grafo. Copie os
campos de `.env.example` para `.env`, defina `LANGSMITH_TRACING=true`,
`LANGSMITH_API_KEY` e `LANGSMITH_PROJECT`. O runtime injeta `run_id`, fluxo
ativo e tags em cada execucao via `app/observability.py`.

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
- Os evals cobrem feature, bugfix, documentacao, review, decisao, research e
  entrega com Discovery; a meta minima de aceite e nota `8.7`.
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
- Validacao automatica de performance do modelo depende do provedor real,
  latencia de rede, tamanho de contexto e volume dos logs compactados.
- A bateria inicial usa o mock para medir arquitetura e contratos; a proxima
  baseline deve rodar com modelo real e custos/latencias observados no LangSmith.
