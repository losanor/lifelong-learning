from contracts import ECE
from app.structured_output import mock_agent_output


class MockResponse:
    def __init__(self, content: str):
        self.content = content


class MockModel:
    """
    Modelo fake para desenvolvimento local sem gastar API.

    Ele identifica o tipo de agente pelo conteúdo do prompt
    e retorna uma resposta curta, estruturada e previsível.
    """

    def invoke(self, prompt: str):
        prompt_lower = prompt.lower()

        if "[[agent:discovery]]" in prompt_lower:
            return MockResponse(mock_agent_output("discovery", mock_discovery(), ece=ECE.C2))

        if "[[agent:product]]" in prompt_lower:
            return MockResponse(mock_agent_output("product", mock_product(), ece=ECE.C2))

        if "[[agent:qa_planning]]" in prompt_lower:
            return MockResponse(mock_agent_output("qa_planning", mock_qa_planning(), ece=ECE.C2))

        if "[[agent:engineering]]" in prompt_lower:
            return MockResponse(mock_agent_output("engineering", mock_engineering(), ece=ECE.C2))

        if "[[agent:operator]]" in prompt_lower:
            return MockResponse(mock_agent_output("operator", mock_operator(), ece=ECE.C1))

        if "[[agent:engineering_review]]" in prompt_lower:
            return MockResponse(mock_agent_output("engineering_review", mock_engineering_review(), ece=ECE.C1))

        if "[[agent:qa_execution]]" in prompt_lower:
            return MockResponse(mock_agent_output("qa_execution", mock_qa_execution(), ece=ECE.C1))

        if "[[agent:writing]]" in prompt_lower:
            return MockResponse(mock_agent_output("writing", mock_writing(), ece=ECE.C1))

        if "[[agent:cos]]" in prompt_lower:
            return MockResponse(
                mock_agent_output(
                    "cos",
                    mock_cos(),
                    ece=ECE.C1,
                    decision="GO_WITH_RESTRICTIONS",
                    route_action="END_CYCLE",
                )
            )
        
        return MockResponse(mock_default())
    

def mock_discovery() -> str:
    return """
# Discovery / Market Research — MOCK

## 1. Achados verificados
Nenhum achado verificado nesta execução mock.

## 2. Achados não verificados
- Mercado de produtividade pessoal: C3
- Preferência por simplicidade: C2
- Dor com ferramentas complexas: C3

## 3. Lacunas
- L1: usuário-alvo não definido
- L2: problema central não validado
- L3: plataforma prioritária não definida

## 4. Resumo Estruturado
1. Decisão/artefato produzido: Discovery mock gerado.
2. Bloqueios: ausência de dados reais.
3. Próximo passo e dono: Product Lead avaliar escopo de MVP Sujo.
4. Necessidade de escalar para CoS: não.
5. ECE: C2.
"""


def mock_product() -> str:
    return """
# Product Brief — MOCK

## 1. Hipótese estratégica
Usuários podem valorizar uma ferramenta simples para capturar, listar e concluir tarefas pessoais.

ECE: C2 com dependências C3.

## 2. Escopo do MVP Sujo
Dentro do escopo:
- Criar tarefa
- Listar tarefas
- Marcar/desmarcar como concluída
- Persistir no localStorage
- Registrar analytics mínimo

Fora do escopo:
- Backend
- Login
- Banco remoto
- Categorias
- Datas
- Notificações

## 3. Bloqueios
- Engineering completa bloqueada até resolver L1, L2 e L3.
- Experimento permitido com MVP Sujo.

## 4. Resumo Estruturado
1. Decisão/artefato produzido: Product Brief mock.
2. Bloqueios: usuário-alvo e problema central indefinidos.
3. Próximo passo e dono: QA Planning avaliar testabilidade.
4. Necessidade de escalar para CoS: não.
5. ECE: C2.
"""


def mock_qa_planning() -> str:
    return """
# QA Planning — MOCK

## 1. Avaliação de Testabilidade
O MVP Sujo é testável. Engineering completa não é testável ainda.

## 2. Critérios de Aceite
- Criar tarefa via botão.
- Criar tarefa via Enter.
- Campo vazio não cria tarefa.
- Marcar/desmarcar concluída.
- Persistir após reload.
- Criar analytics no localStorage.

## 3. Recomendação Go / No-Go
Go apenas para MVP Sujo / experimento.
No-Go para Engineering completa.

## 4. Resumo Estruturado
1. Decisão/artefato produzido: QA Planning mock.
2. Bloqueios: critérios do experimento ainda precisam ser definidos.
3. Próximo passo e dono: Engineering Lead especificar MVP Sujo.
4. Necessidade de escalar para CoS: não.
5. ECE: C2.
"""


def mock_engineering() -> str:
    return """
# Engineering Specification — MOCK

## 1. Escopo Técnico
MVP Sujo / experimento.

## 2. Decisões Técnicas
- HTML + CSS + JavaScript vanilla.
- Sem backend.
- Sem autenticação.
- Sem banco remoto.
- Persistência via localStorage.
- Analytics local com first_visit_at, last_visit_at, visit_count e visit_dates.

## 3. Estrutura de Arquivos
- index.html
- style.css
- app.js

## 4. Recomendação Técnica
Go para Implementation Operator.

## 5. Resumo Estruturado
1. Decisão/artefato produzido: Engineering Spec mock.
2. Bloqueios: nenhum para MVP Sujo.
3. Próximo passo e dono: Implementation Operator gerar pacote.
4. Necessidade de escalar para CoS: não.
5. ECE: C2.
"""


def mock_operator() -> str:
    return """
# Implementation Operator Package — MOCK

## 1. Status de Implementação
Pronto para Claude Code.

## 2. Arquivos que Claude Code deve criar
- index.html
- style.css
- app.js

## 3. Restrições Obrigatórias
- Sem framework.
- Sem backend.
- Sem package.json.
- Sem dependências externas.
- Sem features além do escopo.

## 4. Prompt para Claude Code
Implementar MVP Sujo de tarefas pessoais usando HTML, CSS e JavaScript vanilla, com localStorage e analytics local.

## 5. Resumo Estruturado
1. Decisão/artefato produzido: pacote mock para Claude Code.
2. Bloqueios: nenhum para implementação.
3. Próximo passo e dono: executar código ou validar manualmente.
4. Necessidade de escalar para CoS: não.
5. ECE: C1.
"""


def mock_engineering_review() -> str:
    return """
# Engineering Review â€” MOCK

## 1. Parecer
Aprovado para QA Execution.

## 2. Aderencia a Especificacao
- Pacote do Operator preserva HTML, CSS e JavaScript vanilla.
- Sem backend, autenticacao, banco remoto ou analytics externo.
- Test cases do QA Planning foram carregados no pacote operacional.

## 3. Riscos Tecnicos
- ID local pode ser reforcado depois com sufixo aleatorio.

## 4. Proximo Passo Autorizado
QA Execution pode validar o resultado implementado.

## 5. Resumo Estruturado
1. Decisao/artefato produzido: review tecnico mock aprovado.
2. Bloqueios: nenhum bloqueio tecnico para QA Execution.
3. Proximo passo e dono: QA Execution validar build.
4. Necessidade de escalar para CoS: nao.
5. ECE: C1.
"""


def mock_qa_execution() -> str:
    return """
# QA Execution Report — MOCK

## 1. Parecer Final
Aprovado para experimento com usuários.

## 2. Escopo Validado
- Criação de tarefa
- Marcação/desmarcação
- Persistência local
- Analytics local
- Interface em português

## 3. Bugs Bloqueantes
Nenhum bug bloqueante identificado.

## 4. Ressalvas / Melhorias Opcionais
- Fechar B-002 antes da exposição a usuários reais.
- Executar testes de browser antes de ampliar o experimento.

## 5. Decisão de Liberação
Pode expor para usuários reais? Sim, após fechar B-002.
Pode avançar para Engineering completa? Não.
Precisa escalar para CoS? Não.

## 6. Resumo Estruturado
1. Decisão/artefato produzido: QA Execution mock.
2. Bloqueios: B-001 e B-002 permanecem abertos.
3. Próximo passo e dono: Product Lead fechar critérios do experimento.
4. Necessidade de escalar para CoS: não.
5. ECE: C1.
"""


def mock_writing() -> str:
    return """
# Writing Artifact â€” MOCK

## Objetivo
Organizar a documentacao solicitada sem alterar escopo.

## Conteudo Estruturado
- Contexto resumido
- Decisoes registradas
- Pendencias e proximos passos

## Resumo Estruturado
1. Decisao/artefato produzido: documento mock organizado.
2. Bloqueios: nenhum.
3. Proximo passo e dono: CoS revisar destino do documento.
4. Necessidade de escalar para CoS: nao.
5. ECE: C1.
"""


def mock_default() -> str:
    return """
# Mock Response

Resposta mock genérica.

## Resumo Estruturado
1. Decisão/artefato produzido: resposta mock.
2. Bloqueios: nenhum.
3. Próximo passo e dono: continuar fluxo.
4. Necessidade de escalar para CoS: não.
5. ECE: C2.
"""

def mock_cos() -> str:
    return """
# CoS / Orchestrator Report — MOCK

## 1. Decisão Executiva
GO_WITH_RESTRICTIONS

O ciclo está coerente e o MVP Sujo pode avançar apenas para experimento controlado. Engineering completa permanece bloqueada.

## 2. Justificativa da Decisão
QA Execution aprovou o MVP para experimento, mas B-001 e B-002 continuam ativos. Portanto, o ciclo pode encerrar com restrições.

## 3. Coerência Entre Agentes
- Product vs QA Planning: coerente.
- QA Planning vs Engineering: coerente.
- Engineering vs Operator: coerente.
- Operator vs QA Execution: coerente.
- QA Execution vs Decision Log: coerente.

## 4. Conflitos Detectados
Nenhum conflito crítico detectado.

## 5. Violações de Escopo
Nenhuma violação de escopo detectada.

## 6. Bloqueios Ativos
- B-001: Engineering completa não aprovada. Dono: Product Lead + Discovery.
- B-002: Critérios do experimento indefinidos. Dono: Product Lead.

## 7. Próximo Passo Autorizado
Product Lead pode fechar B-002 e preparar experimento controlado com usuários.

## 8. Próximo Passo Não Autorizado
Não está autorizado avançar para Engineering completa, backend, autenticação, produto escalável ou analytics remoto.

## 9. Ação de Roteamento
END_CYCLE


## 10. Decisão Humana Necessária
Precisa de decisão humana agora? Sim.
Qual decisão? Definir número de usuários, duração, critério de sucesso e forma de coleta.
Por quê? Sem isso, o experimento não gera decisão acionável.

## 11. Atualizações Recomendadas para Logs
- Shared Memory: registrar encerramento do ciclo com restrições.
- Decision Log: registrar nova decisão apenas se B-002 for fechado.
- Handoff Log: registrar passagem CoS → Product Lead / Humano.

## 12. Resumo Estruturado
1. Decisão/artefato produzido: CoS Report mock.
2. Bloqueios: B-001 e B-002 permanecem ativos.
3. Próximo passo e dono: Product Lead fechar B-002.
4. Necessidade de escalar para humano: sim, para definição de critérios do experimento.
5. ECE: C1.
"""
