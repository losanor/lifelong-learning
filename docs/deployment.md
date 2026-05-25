# Deployment da squad

## LangSmith Deployment

O repositorio esta empacotado para Agent Server por `langgraph.json`. O arquivo
nao referencia `.env`, porque credenciais nao sao publicadas no Git. Em um
deployment gerenciado, o graph ID e `squad` e a infraestrutura de threads,
runs, fila e persistencia e provisionada pelo LangSmith Deployment.

Pre-requisitos:

- conectar este repositorio GitHub a um workspace LangSmith com Deployment;
- cadastrar `ANTHROPIC_API_KEY`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` e
  `LANGSMITH_TRACING=true` no ambiente seguro do deployment;
- manter `USE_MOCK_MODEL=false` fora de testes;
- configurar autenticacao antes de expor qualquer endpoint customizado.

O input minimo do grafo e:

```json
{
  "user_goal": "Objetivo operacional da iniciativa",
  "project_id": "cliente-ou-produto",
  "initiative_id": "entrega-atual",
  "workspace_root": "."
}
```

## Operacao local e producao

`app.operational_api` e a console local de aprovacao e evidencias, apoiada em
SQLite. Ela e apropriada para desenvolvimento, piloto individual e validacao
do processo, vinculada a `127.0.0.1`.

Em producao distribuida, use Agent Server para threads/runs e um backend
PostgreSQL provisionado pelo modo de deployment escolhido. A migracao da fila
local de aprovacoes para endpoints autenticados do Agent Server depende da
definicao do ambiente e da politica de identidade; nao deve ser improvisada
antes disso.

## n8n

O webhook local implementado aceita apenas entrada autenticada e idempotente.
Ao publicar integracoes, encaminhe eventos ao endpoint autenticado do ambiente
escolhido e preserve a regra: trigger externo enfileira demanda, mas nao
autoriza efeitos no workspace.

Referencias oficiais:

- https://docs.langchain.com/langsmith/deployment
- https://docs.langchain.com/langsmith/local-server
- https://docs.langchain.com/langsmith/agent-server
