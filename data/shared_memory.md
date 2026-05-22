# Shared Memory — Squad v5 Lite

## 1. Contexto Geral

Esta memória registra decisões, aprendizados, bloqueios e status dos ciclos executados pela Squad v5 Lite.

A squad opera com foco em:
- redução de alucinação;
- separação entre hipótese, evidência e decisão;
- controle de escopo;
- validação humana antes de avanço;
- execução incremental;
- baixo custo operacional.

---

## 2. Estado Atual da Squad

Fluxo atual implementado:

Discovery → Product → QA Planning → Engineering Lead → Implementation Operator → Claude Code → QA Execution

Status:
- Fluxo multiagente local funcionando em LangGraph.
- MVP Sujo de tarefas pessoais implementado em HTML, CSS e JavaScript vanilla.
- QA manual realizado.
- QA Execution aprovado para experimento com usuários.

---

## 3. Decisões Registradas

### D-001 — Tarefa concluída permanece visível
Tarefa concluída deve permanecer visível com texto riscado e pode ser desmarcada.

Status: Ativa  
Impacto: Product, QA, Engineering, Implementation Operator

### D-002 — Persistência local no MVP Sujo
O MVP Sujo deve usar `localStorage`, sem backend, autenticação ou banco remoto.

Status: Ativa  
Impacto: Engineering, Implementation Operator, QA

### D-003 — Tracking mínimo local
O MVP Sujo deve registrar `first_visit_at`, `last_visit_at`, `visit_count` e `visit_dates` em `localStorage`, na chave `analytics`.

Status: Ativa  
Impacto: Engineering, QA, Product

### D-004 — Interface em português
A interface deve estar em português:
- Placeholder: "Digite uma tarefa..."
- Botão: "Adicionar"
- Estado vazio: "Nenhuma tarefa ainda."
- Aviso storage: "Seus dados não serão salvos neste navegador."

Status: Ativa  
Impacto: UX/UI, Engineering, QA

### D-005 — MVP Sujo aprovado para experimento
Após implementação via Claude Code e validação manual, o QA Execution aprovou o MVP Sujo para experimento com usuários.

Status: Ativa  
Impacto: Product, QA, CoS

---

## 4. Bloqueios Atuais

### B-001 — Engineering completa não aprovada
Engineering completa permanece bloqueada até resolver:
- usuário-alvo;
- problema central;
- diferencial frente a concorrentes;
- plataforma prioritária.

Status: Aberto  
Dono: Product Lead + Discovery

### B-002 — Critérios do experimento com usuários
Antes de expor o MVP Sujo a usuários reais, Product Lead deve definir:
- número de usuários;
- duração do teste;
- critério mínimo de sucesso;
- forma de coleta dos dados.

Status: Aberto  
Dono: Product Lead

---

## 5. Artefatos Gerados

- Product Brief preliminar
- QA Planning
- Engineering Specification
- Implementation Operator Package
- Código do MVP Sujo:
  - index.html
  - style.css
  - app.js
- QA Execution Report
- Release Decision pendente de registro formal

---

## 6. Próximo Passo

Criar Decision Log e mecanismos simples para:
- registrar novas decisões;
- consultar estado atual da squad;
- alimentar agentes com memória compartilhada;
- evitar que decisões fiquem hardcoded no `graph.py`.

---

## 7. Observações

Esta memória ainda é manual e baseada em Markdown.  
Não há RAG, embeddings ou compactação nesta fase.  
A prioridade é rastreabilidade simples e baixo atrito.
---

## Registro de Ciclo — MVP Sujo de Tarefas

Data/hora: 2026-05-19

### Status do ciclo

Concluído.

### Fluxo executado

Discovery → Product → QA Planning → Engineering Lead → Implementation Operator → Claude Code → QA Execution

### Decisão final

O MVP Sujo de organização de tarefas pessoais foi aprovado para experimento com usuários.

### Evidências

- Código implementado em HTML, CSS e JavaScript vanilla.
- Persistência via `localStorage`.
- Tracking local via chave `analytics`.
- QA manual executado.
- QA Execution emitiu parecer: **Aprovado para experimento com usuários**.
- Nenhum bug bloqueante identificado.
- Test cases críticos P0 aprovados.

### Restrições mantidas

Engineering completa permanece bloqueada.

### Bloqueios abertos

#### B-001 — Engineering completa não aprovada
Permanece aberto. Depende de aprendizado do experimento e resolução de:
- usuário-alvo;
- problema central;
- diferencial frente a concorrentes;
- plataforma prioritária.

#### B-002 — Critérios do experimento indefinidos
Permanece aberto. Product Lead precisa definir antes de expor o MVP:
- número de usuários;
- duração do experimento;
- critério mínimo de sucesso;
- forma de coleta dos dados.

### Ressalvas não bloqueantes

- Executar TC-011: localStorage indisponível.
- Executar TC-014 e TC-015: texto longo e caracteres especiais.
- Executar P2: compatibilidade básica de browsers.
- Reforçar geração de ID com sufixo aleatório em ciclo futuro, se necessário.

### Próximo passo

Fechar B-002 antes de iniciar o experimento com usuários reais.
