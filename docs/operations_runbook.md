# Runbook operacional da squad

## Escopo do piloto

O modo atual foi desenhado para um operador em uma maquina confiavel:
LangGraph e a console rodam localmente, SQLite guarda evidencias e LangSmith
recebe tracing amostrado. A console deve permanecer vinculada a `127.0.0.1`;
ela nao e um endpoint publico autenticado.

## Preflight

1. Mantenha credenciais apenas em `.env`.
2. Confirme prontidao sem imprimir segredos:

```powershell
.\.venv\Scripts\python.exe -m app.pilot_readiness
```

3. Para validar contratos sem custo de modelo:

```powershell
$env:USE_MOCK_MODEL="true"
.\.venv\Scripts\python.exe -m app.evals
```

4. Para operar com modelo real, restaure `USE_MOCK_MODEL=false` e inicie:

```powershell
.\.venv\Scripts\python.exe -m app.operational_api --port 8765
```

## Ciclo diario

1. Cadastre uma demanda delimitada na console e confirme o tier/custo estimado.
   Quando houver PRD, mantenha-a dentro do workspace, localize-a no intake e
   confirme no preview o documento vinculado e o limite de contexto.
2. Acompanhe a run em `Board` e o caminho percorrido em
   `Orquestracao > Execucoes`, incluindo os handoffs realmente registrados.
3. Resolva itens em `Decisoes` antes de autorizar continuidade.
4. Em `Inbox`, aprove preparacao, revise diff, aprove aplicacao e valide o
   resultado. Em falha de validacao, o patch e revertido automaticamente.
5. Para entregar codigo, acione `Criar branch e commit` somente depois de
   revisar evidencias. Depois, acione `Publicar e abrir PR draft`; o merge
   permanece fora da automacao.
6. Sincronize traces em `Custos` e acompanhe tendencia em `Indicadores`.

O vinculo da PRD e preservado por projeto/iniciativa. Em uma nova run do mesmo
escopo, a console reaproveita o documento salvo, desde que ele ainda exista
no mesmo workspace. Alteracoes no arquivo atualizam o hash na proxima run.

## Recuperacao e auditoria

Itens interrompidos permanecem no SQLite e podem ser inspecionados sem
reiniciar agentes:

```powershell
.\.venv\Scripts\python.exe -m app.workflow_recovery pending
.\.venv\Scripts\python.exe -m app.workflow_recovery resume <request_id>
```

Se uma aplicacao validada nao deve seguir para Git, use a acao de rollback na
console ou:

```powershell
.\.venv\Scripts\python.exe -m app.review_execution rollback <request_id> --reason "Cancelado pelo operador."
```

Depois de um commit supervisionado, reversao deve ser feita pelo fluxo Git
normal de revisao/revert; nao tente aplicar rollback de patch sobre um commit
ja publicado.

## Backup e restauracao

Com o servidor parado, copie o banco para uma pasta de backup controlada:

```powershell
Copy-Item .\data\squad_runtime.sqlite3 .\backups\squad_runtime-YYYYMMDD.sqlite3
```

Para restaurar, pare a console, preserve uma copia do banco corrente e copie
o backup escolhido para `data\squad_runtime.sqlite3`. Logs sob
`data\namespaces\` complementam auditoria humana, mas o SQLite e a fonte dos
estados executaveis.

## Incidentes usuais

- Falha do provedor: mantenha a run falha para auditoria, verifique credito e
  credenciais e inicie nova demanda somente depois da causa identificada.
- Trace ausente: a entrega segue operacionalmente; sincronize novamente em
  `Custos` e nao confunda orcamento estimado com custo observado.
- Validacao falha: confirme que o rollback automatico ocorreu e produza novo
  diff em vez de reaproveitar um patch rejeitado.
- Publicacao Git falha: o commit local continua auditavel; corrija
  autenticacao/remoto e repita somente a etapa de publicacao.
- Token n8n exposto: substitua `SQUAD_WEBHOOK_TOKEN`, reinicie o servidor e
  atualize a credencial no workflow antes de reativar entradas externas.

## Cadencia de governanca

Semanalmente, reveja `Indicadores`, traces amostrados, decisoes humanas e PRs
draft. Execute uma baseline real curta quando houver alteracao relevante em
prompts, roteamento ou modelo; registre a rubrica humana e mantenha a meta
minima de qualidade em `8.7`.

## Quando migrar do piloto local

Migrar para Agent Server/PostgreSQL deixa de ser opcional quando ocorrer um
destes sinais:

- mais de um operador precisa atuar simultaneamente;
- aprovacoes ou visualizacao precisam ser acessadas remotamente;
- a fila passa de 10 pendencias recorrentes ou existem runs concorrentes;
- a operacao exige disponibilidade continua ou recuperacao independente da
  maquina do operador;
- perda do SQLite local se torna risco inaceitavel de compliance ou auditoria.

Antes dessa migracao, a console local entrega uma operacao factivel com
governanca humana, evidencias persistidas e custos acompanhaveis.
