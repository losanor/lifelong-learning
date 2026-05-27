# Piloto local sem mensalidade

## Stack ativa

O piloto utiliza:

- LangGraph executando localmente;
- a console `Squad Operations` para aprovacoes, patches e evidencias;
- SQLite para runs, checkpoints e fila operacional local;
- LangSmith Developer para traces e avaliacoes dentro da franquia gratuita;
- n8n somente para entrada autenticada de demandas, quando necessario.

LangSmith Deployment Cloud fica preparado, mas desativado nesta etapa porque
exige plano Plus.

## Controle de custo

No LangSmith, em `Settings > Billing > Usage limits`, configure `Spend limit`
como `US$ 0 / month on traces` e mantenha `Base - 14 day retention`. Esse
limite bloqueia gasto adicional com traces; o piloto utiliza somente a
franquia gratuita vigente do plano Developer. Na configuracao local:

```dotenv
USE_MOCK_MODEL=false
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=squad-v5-lite
LANGSMITH_TRACING_SAMPLING_RATE=0.2
```

Use `LANGSMITH_TRACING_SAMPLING_RATE=1.0` somente para uma baseline curta e
intencional. O custo das chamadas ao modelo continua sendo cobrado pelo
provedor do modelo, independentemente do plano LangSmith.

## Checagem antes de operar

O comando abaixo nao imprime tokens ou valores de chaves:

```powershell
.\.venv\Scripts\python.exe -m app.pilot_readiness
```

Ele verifica modelo real, tracing, sampling e controles de efeito. Para testar
fluxo sem custo de modelo, defina temporariamente `USE_MOCK_MODEL=true` e rode
`python -m app.evals`.

## Rotina diaria

1. Receber objetivo pela console ou pelo terminal.
2. Executar a squad apenas para demandas reais e delimitadas.
3. Revisar traces amostrados no LangSmith e a fila local na console.
4. Aprovar patches somente apos diff e validacoes.
5. Rever semanalmente volume de runs, escaladas, custo de modelo e qualidade.

Para backup, recuperacao, entrega Git supervisionada e tratamento de
incidentes, siga `docs/operations_runbook.md`.

## Criterio para migrar ao Cloud pago

Reavalie LangSmith Deployment quando os criterios mensuraveis descritos em
`docs/operations_runbook.md` forem atingidos.
