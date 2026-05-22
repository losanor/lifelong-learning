# Context Policy — Squad v5 Lite

Este arquivo define quais fontes de contexto devem ser usadas em cada tipo de execução da squad.

Objetivo:
- reduzir consumo de tokens;
- reduzir ruído nos prompts;
- preservar rastreabilidade;
- evitar que agentes usem contexto irrelevante;
- permitir auditoria quando necessário.

---

## 1. Fontes de Contexto

### Compact Memory
Arquivo: `data/compact_memory.md`

Uso:
- contexto operacional prioritário;
- deve ser usado em execuções normais;
- contém decisões ativas, bloqueios ativos e resumo do último ciclo.

### Shared Memory
Arquivo: `data/shared_memory.md`

Uso:
- memória viva completa;
- deve ser usada quando o agente precisar entender histórico mais amplo;
- não deve ser enviada inteira em ciclos normais.

### Decision Log
Arquivo: `data/decision_log.md`

Uso:
- decisões formais;
- deve ser usado em decisões críticas;
- deve ser usado por Product, Engineering e QA Execution quando houver risco de contrariar decisão ativa.

### Handoff Log
Arquivo: `data/handoff_log.md`

Uso:
- auditoria de passagens entre agentes;
- deve ser usado em debugging, investigação de falhas e análise de retrabalho;
- não deve ser enviado inteiro em ciclos normais.

---

## 2. Modos de Contexto

### normal

Usar:
- Compact Memory

Não usar:
- Shared Memory completa
- Decision Log completo
- Handoff Log completo

Quando usar:
- execução padrão da squad;
- geração de Product Brief;
- QA Planning comum;
- Engineering Spec comum;
- Implementation Operator comum.

---

### decision_critical

Usar:
- Compact Memory
- Decision Log

Não usar:
- Shared Memory completa
- Handoff Log completo, salvo se houver divergência entre agentes.

Quando usar:
- mudança de escopo;
- desbloqueio de Engineering completa;
- decisão de avançar/reprovar release;
- alteração de decisão ativa;
- conflito entre Product, Engineering e QA.

---

### audit_debug

Usar:
- Compact Memory
- Handoff Log
- Decision Log, se necessário

Quando usar:
- agente gerou output contraditório;
- fluxo quebrou;
- handoff ficou inconsistente;
- QA reprovou;
- houve retrabalho excessivo;
- suspeita de alucinação ou perda de contexto.

---

### full_context

Usar:
- Compact Memory
- Shared Memory
- Decision Log
- Handoff Log

Quando usar:
- revisão geral da squad;
- preparação de relatório executivo;
- migração de arquitetura;
- compactação;
- investigação profunda;
- auditoria completa.

Regra:
Este modo deve ser exceção, não padrão.

---

## 3. Política por Agente

### Discovery
Modo padrão: normal  
Contexto: Compact Memory

Discovery normalmente não precisa de logs completos.

### Product Lead
Modo padrão: normal  
Modo alternativo: decision_critical

Usar Decision Log quando houver mudança de escopo, desbloqueio de Engineering completa ou decisão de produto relevante.

### QA Planning
Modo padrão: normal  
Modo alternativo: decision_critical

Usar Decision Log se o QA precisar avaliar se um escopo viola decisões ativas.

### Engineering Lead
Modo padrão: normal  
Modo alternativo: decision_critical

Usar Decision Log quando houver risco de overengineering, mudança de stack ou alteração de decisão técnica.

### Implementation Operator
Modo padrão: normal

Deve seguir Engineering Spec e Compact Memory. Não deve consultar logs completos salvo em caso de ambiguidade.

### QA Execution
Modo padrão: decision_critical

QA Execution decide liberação. Deve receber Compact Memory e Decision Log.

### CoS
Modo padrão: full_context quando acionado

CoS entra para resolver ambiguidades, conflitos, escaladas e decisões estratégicas.

---

## 4. Regras Gerais

1. Compact Memory é a fonte prioritária em execuções normais.
2. Logs completos são fontes de auditoria, não contexto padrão.
3. Decision Log prevalece sobre Shared Memory em caso de conflito.
4. Handoff Log não deve ser usado para tomar decisão de produto, apenas para rastrear fluxo e diagnosticar problemas.
5. Se um agente identificar conflito entre Compact Memory e Decision Log, deve escalar para CoS.
6. Se a decisão não estiver no Decision Log, ela não deve ser tratada como decisão formal.
7. Se um bloqueio estiver aberto no Compact Memory, o agente não deve ignorá-lo.
8. Engineering completa só pode ser autorizada se o Decision Log indicar desbloqueio explícito.

---

## 5. Política Atual

Modo padrão atual da squad: `normal`

Exceção:
- QA Execution usa `decision_critical`
- CoS, quando existir, usará `full_context`