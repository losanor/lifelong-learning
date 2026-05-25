# Integracao n8n

## Contrato de Entrada

A borda de automacao recebe novas demandas, sem executar agentes:

```http
POST http://127.0.0.1:8765/api/hooks/n8n/demands
Authorization: Bearer <SQUAD_WEBHOOK_TOKEN>
Content-Type: application/json
```

```json
{
  "event_id": "crm-ticket-1024",
  "project_id": "client-portal",
  "initiative_id": "onboarding",
  "user_goal": "Avaliar falha relatada no fluxo de cadastro.",
  "metadata": {
    "origin": "manual-triage",
    "priority": "normal"
  }
}
```

## Regras Operacionais

- `SQUAD_WEBHOOK_TOKEN` deve existir apenas no ambiente local/servidor.
- `event_id` e a chave idempotente; reenvios nao duplicam a demanda.
- O status inicial e `queued_for_review`.
- A console mostra a entrada para revisao humana.
- O endpoint nao chama o grafo, nao escreve no workspace e nao executa Git.

## Workflow Base

O arquivo `integrations/n8n/demand_webhook.example.json` inclui um trigger
manual, a construcao do payload e uma chamada HTTP autenticada. No n8n,
substitua o trigger conforme a necessidade e configure:

- `SQUAD_API_URL`, por exemplo `http://host.docker.internal:8765`.
- `SQUAD_WEBHOOK_TOKEN`, igual ao token do runtime.

Para producao, use HTTPS e armazenamento de credenciais do n8n; nao salve
tokens dentro do workflow exportado.
