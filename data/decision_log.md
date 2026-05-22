# Decision Log — Squad v5 Lite

Este arquivo registra decisões formais da squad.

Cada decisão deve conter:
- ID
- título
- data
- status
- contexto
- decisão
- impacto
- dono
- critérios de revisão

---

## D-001 — Tarefa concluída permanece visível

Data: 2026-05-19  
Status: Ativa  
Dono: Product Lead  

### Contexto
O MVP Sujo de tarefas pessoais precisa permitir que o usuário marque uma tarefa como concluída sem removê-la da lista.

### Decisão
Tarefa concluída permanece visível com texto riscado e pode ser desmarcada.

### Impacto
- Product: define comportamento funcional.
- QA: gera test cases de marcar/desmarcar.
- Engineering: implementa checkbox + estado `completed`.
- Implementation Operator: impede exclusão automática.

### Critérios de revisão
Revisar apenas se usuários pedirem explicitamente exclusão, arquivamento ou limpeza de concluídas.

---

## D-002 — Persistência local no MVP Sujo

Data: 2026-05-19  
Status: Ativa  
Dono: Engineering Lead  

### Contexto
O MVP Sujo deve validar uso com o menor custo e complexidade possível.

### Decisão
Usar `localStorage` como única camada de persistência. Não usar backend, autenticação ou banco remoto.

### Impacto
- Engineering: stack restrita a HTML, CSS e JavaScript vanilla.
- QA: validar persistência após reload.
- Product: aceitar limitação de uso no mesmo browser/dispositivo.

### Critérios de revisão
Revisar se o experimento mostrar uso recorrente e necessidade de sincronização entre dispositivos.

---

## D-003 — Tracking mínimo local

Data: 2026-05-19  
Status: Ativa  
Dono: Product Lead + Engineering Lead  

### Contexto
O experimento precisa medir retorno básico sem analytics externo.

### Decisão
Registrar `first_visit_at`, `last_visit_at`, `visit_count` e `visit_dates` em `localStorage`, na chave `analytics`.

### Impacto
- Engineering: implementar objeto `analytics`.
- QA: validar `visit_count` e `visit_dates`.
- Product: usar dados para interpretar retorno no mesmo browser.

### Critérios de revisão
Revisar se o experimento exigir coleta agregada de múltiplos usuários.

---

## D-004 — Interface em português

Data: 2026-05-19  
Status: Ativa  
Dono: Product Lead  

### Contexto
O MVP será testado em contexto brasileiro/português.

### Decisão
Interface em português com os textos:
- Placeholder: "Digite uma tarefa..."
- Botão: "Adicionar"
- Estado vazio: "Nenhuma tarefa ainda."
- Aviso storage: "Seus dados não serão salvos neste navegador."

### Impacto
- UX/UI: copy definida.
- QA: validação de textos.
- Engineering: textos hardcoded em português.

### Critérios de revisão
Revisar apenas se o público do experimento mudar de idioma.

---

## D-005 — MVP Sujo aprovado para experimento

Data: 2026-05-19  
Status: Ativa  
Dono: QA Execution Lead  

### Contexto
O MVP foi implementado via Claude Code e validado manualmente.

### Decisão
O MVP Sujo está aprovado para experimento com usuários, com Engineering completa ainda bloqueada.

### Impacto
- Product: pode iniciar experimento após fechar critérios B-002.
- QA: mantém ressalvas não bloqueantes.
- Engineering: não deve evoluir para produto completo sem novo ciclo.

### Critérios de revisão
Revisar após conclusão do experimento com usuários.