# Retry Policy — Squad v5 Lite

Este arquivo define a política de tentativas, retornos e controle de loops da squad.

Objetivo:
- evitar loops infinitos;
- evitar retrabalho caro;
- limitar retornos automáticos;
- escalar para humano quando o sistema não conseguir resolver sozinho;
- preservar previsibilidade operacional.

---

## 1. Princípios

1. Nenhum agente pode entrar em loop infinito.
2. Todo retorno para agente deve ter motivo explícito.
3. Retornos devem ser limitados por agente e por ciclo.
4. Se o mesmo problema persistir após retries, escalar para humano.
5. CoS é o único agente autorizado a solicitar retorno formal.
6. QA Execution pode reprovar, mas quem decide roteamento final é o CoS.

---

## 2. Limites

### Limite por agente
Máximo de 2 retornos para o mesmo agente no mesmo ciclo.

### Limite total por ciclo
Máximo de 3 retornos totais no mesmo ciclo.

### Após limite excedido
Escalar para humano.

---

## 3. Retornos permitidos

- RETURN_TO_PRODUCT
- RETURN_TO_QA
- RETURN_TO_ENGINEERING
- RETURN_TO_OPERATOR

---

## 4. Quando retornar para Product

Retornar para Product quando:
- hipótese C3 virou decisão firme;
- escopo está ambíguo;
- Product ignorou bloqueio ativo;
- MVP Sujo virou produto completo sem autorização;
- critérios de sucesso não foram definidos quando necessários.

---

## 5. Quando retornar para QA

Retornar para QA quando:
- test cases não cobrem o escopo aprovado;
- QA aprovou algo não testável;
- QA ignorou bloqueios críticos;
- critérios de aceite estão genéricos demais.

---

## 6. Quando retornar para Engineering

Retornar para Engineering quando:
- houver overengineering;
- stack violar decisão ativa;
- Engineering aprovar produto completo com B-001 aberto;
- especificação técnica estiver incompleta;
- houver backend, autenticação, banco ou framework sem autorização.

---

## 7. Quando retornar para Operator

Retornar para Operator quando:
- pacote para Claude Code violar Engineering Spec;
- adicionar arquivos, features ou dependências fora do escopo;
- remover restrições críticas;
- prompt estiver insuficiente para implementação.

---

## 8. Quando escalar humano

Escalar humano quando:
- limite de retry por agente for excedido;
- limite total de retries for excedido;
- houver conflito entre agentes sem resolução;
- CoS identificar decisão estratégica;
- Decision Log estiver inconsistente;
- houver ambiguidade de escopo que o sistema não consegue resolver.

---

## 9. Política atual

Modo atual:
- Retry Policy ativa como estado e validação.
- Loop automático ainda não ativado.
- RETURN_TO_* registra intenção de retorno.
- Execução automática de retorno será implementada em fase posterior com edges condicionais.