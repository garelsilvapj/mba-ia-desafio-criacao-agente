# Residencial Aurora: assistente virtual (Google ADK)

API em Python (FastAPI) que expõe o assistente do Residencial Aurora, construído com **Google ADK 2.9.2**. O morador reserva e cancela áreas comuns, autoriza visitantes e tira dúvidas sobre o regulamento. As regras críticas estão no código, não no prompt: **o modelo decide o caminho, o código decide o que é permitido.**

```
aurora/
  config.py       caminhos, bancos e modelo (AURORA_MODEL)
  catalogo.py     leitura somente-leitura de dados/*.json
  db.py           schema SQLite do domínio (índice único de reservas ativas)
  repo.py         regras de negócio: listar, disponibilidade, reservar, cancelar, autorizar
  regulamento.py  parser do regulamento por capítulo
  tools.py        tools ADK (apartamento sempre vem da sessão)
  agents.py       agente principal + especialistas + App
  api.py          rotas HTTP, Runner, confirmações e sessões persistidas
  restore.py      comando de restauração dos dados
  main.py         comando de subida (uvicorn na porta 8000)
dados/            estado inicial do condomínio (não é alterado)
var/              bancos SQLite gerados em tempo de execução (fora do Git)
```

## Arquitetura

```
                    ┌─────────────────────────────┐
  morador ──HTTP──▶ │ FastAPI (aurora/api.py)     │
                    │ Runner + DatabaseSession    │
                    └──────────────┬──────────────┘
                                   ▼
                        concierge (principal)
               ┌───────────────┬───┴──────────────────┐
     transfer  ▼      transfer ▼                      ▼  AgentTool
      reservas_agent     visitantes_agent       regulamento_agent
      (tools de reserva) (tools de visitante)   (lê 1 capítulo por vez)
               └────────┬────────┘
                        ▼
              SQLite de domínio (var/aurora.db)
```

| Agente | Responsabilidade | Como é acionado | Por quê |
|---|---|---|---|
| `concierge` (principal) | Conversa com o morador e encaminha cada pedido. Não tem tools de dados e **não recebe o regulamento nas instruções**. | Raiz do `App`/`Runner`. | Mantém o contexto principal enxuto e separa a conversa das ações. |
| `reservas_agent` | Lista áreas e reservas do próprio apartamento, verifica disponibilidade, reserva e cancela. | **Sub-agente por transferência** (`sub_agents`). | Uma reserva com taxa pausa a execução para confirmação. A retomada precisa chegar ao agente que fez a chamada, e o ADK só a processa no agente autor (`request_confirmation.py`). Como sub-agente, ele fica registrado como autor nos eventos persistidos e o Runner o escolhe de novo na retomada, inclusive depois de reiniciar a API. |
| `visitantes_agent` | Lista e autoriza visitantes do próprio apartamento. | **Sub-agente por transferência**. | Mesmo motivo: autorizar visitante sempre pede confirmação e precisa ser retomado pelo agente autor. |
| `regulamento_agent` | Responde dúvidas lendo **um capítulo por vez** com a tool `ler_capitulo_regulamento`. | **`AgentTool`** do `concierge`. | O `AgentTool` roda em uma sessão interna, isolada e em memória. O texto do capítulo nunca entra nos eventos da sessão do morador, onde só ficam a pergunta e a resposta curta. Isso economiza tokens em todas as mensagens seguintes. |

Reservas e visitantes são sempre lidos e gravados pelas tools de `aurora/tools.py`, que chamam `aurora/repo.py`. O estado do condomínio fica no SQLite `var/aurora.db`, e as conversas ficam no SQLite `var/sessions.db`, pelo `DatabaseSessionService` do ADK.

**Decisões adicionais**

- O código da reserva é `RSV-` seguido de 8 caracteres hexadecimais aleatórios, registrado em `codigos_emitidos`.
- O comando de restauração volta reservas e visitantes ao estado de `dados/`. Ele **preserva as sessões** e o registro de códigos emitidos, então nenhum código se repete, nem depois de um restore.
- Se chegar uma nova mensagem enquanto há confirmação pendente, a pendência é **recusada** antes de processar a mensagem. Nada executa sem aprovação.

## Garantias

### Garantia 1: cobrança ou acesso só com confirmação

- `aurora/tools.py`, na lista `RESERVAS_TOOLS`: `FunctionTool(reservar_area, require_confirmation=_reserva_gera_cobranca)`. A função `_reserva_gera_cobranca` decide pela **taxa da área** em `dados/areas.json`. A quadra (taxa 0) não pede confirmação.
- `aurora/tools.py`, na lista `VISITANTES_TOOLS`: `FunctionTool(autorizar_visitante, require_confirmation=True)`. Autorizar visitante sempre pede confirmação.
- `aurora/tools.py`, tipo `Area`: o parâmetro `area` das tools de reserva é um `Literal` com os ids de `dados/areas.json` e vira `enum` na declaração enviada ao modelo. Assim o modelo não consegue gerar uma confirmação para uma área inexistente, e `repo.validar_area` continua validando no código.
- `aurora/api.py`, função `_confirmacoes_pendentes`: as pendências são **derivadas dos eventos gravados pelo ADK**. Cada chamada `adk_request_confirmation` sem resposta do usuário vira um item de `confirmacoes_pendentes`. Os `detalhes` vêm dos argumentos originais da tool (`area`/`data` ou `nome`/`data`).
- `aurora/api.py`, função `responder_confirmacao`: sob um lock da sessão, verifica se o `id` está entre as pendências **daquela sessão**. Se não estiver (inexistente, de outra sessão ou já respondida), devolve `409` e nada é executado. Se estiver, envia ao Runner a `FunctionResponse` `adk_request_confirmation` com `{"confirmed": ...}`, e o ADK executa a tool uma única vez ou devolve rejeição, conforme a resposta.

**Por que não depende do modelo:** a execução da tool fica bloqueada dentro do `FunctionTool` do ADK até existir uma `FunctionResponse` de confirmação, e essa resposta só é criada pela rota `/confirmacoes`. Um texto como "já estou confirmando" é só texto e não substitui a resposta. Quem decide se a ação precisa de confirmação é a taxa cadastrada, não o modelo.

### Garantia 2: cada sessão pertence a um apartamento

- `aurora/api.py`, função `criar_sessao`: o apartamento é validado contra `dados/apartamentos.json` e fixado **uma única vez**. Ele vira o `user_id` da sessão ADK e o state `apartamento`, e também é gravado na tabela `sessoes`.
- `aurora/tools.py`, função `_apartamento_da_sessao`: toda tool que lê ou altera dados de um morador obtém o apartamento de `tool_context.user_id` e confere com o state gravado pelo servidor. **Nenhuma tool tem parâmetro `apartamento`**, então o modelo não tem como escolher outro.
- `aurora/repo.py`, função `cancelar_reserva`: o `WHERE apartamento = ? AND area = ? AND data = ?` só encontra reservas do próprio apartamento. Uma reserva de outro morador é tratada como "não existe entre as suas" e nenhum dado dela é devolvido.
- `aurora/tools.py`, função `verificar_disponibilidade`, e `aurora/repo.py`, funções `data_livre` e `criar_reserva`: devolvem apenas `livre`/`ocupada` ou "data já ocupada", **nunca o código ou o apartamento** do dono da reserva.

**Por que não depende do modelo:** o apartamento chega às tools pelo contexto da sessão, criado pelo servidor. Mesmo que o morador diga ser do 302, as consultas SQL continuam filtradas pelo apartamento da sessão.

### Garantia 3: nada se perde no reinício

- `aurora/api.py`: `session_service = DatabaseSessionService(SESSIONS_DB_URL, ...)`, com a URL definida em `aurora/config.py` (`SESSIONS_DB_URL`, SQLite em `var/sessions.db`). Eventos e state das sessões ficam em disco.
- `aurora/db.py`: reservas, visitantes, códigos emitidos e o mapa sessão→apartamento ficam em `var/aurora.db`.
- A subida da API não restaura dados. A carga inicial só acontece automaticamente se o banco estiver vazio (`lifespan` em `aurora/api.py`).
- Confirmações pendentes sobrevivem ao reinício: são lidas dos eventos persistidos, e a aprovação depois do reinício é retomada pelo agente autor.

### Garantia 4: o regulamento é consultado, não carregado

- `aurora/agents.py`: a instrução do `concierge` não contém o regulamento. Ele só tem `tools=[AgentTool(agent=regulamento_agent)]`.
- `aurora/agents.py`, `regulamento_agent`: recebe apenas o **índice de títulos** (`regulamento.indice()`) e lê o texto com a tool `ler_capitulo_regulamento`.
- `aurora/tools.py`, função `ler_capitulo_regulamento`, e `aurora/regulamento.py`, função `capitulos`: devolvem **um único capítulo** por chamada.
- Como o especialista é um `AgentTool`, o ADK o executa em um Runner próprio com `InMemorySessionService`. A leitura do capítulo acontece nessa sessão interna, e na sessão do morador aparece só a chamada do `AgentTool` e a resposta final.

### Garantia 5: dois moradores, uma reserva

- `aurora/db.py`, `SCHEMA`: `CREATE UNIQUE INDEX ux_reserva_ativa ON reservas (area, data) WHERE status = 'ativa'`. A exclusividade é conferida **pelo próprio banco, no instante do INSERT**.
- `aurora/repo.py`, função `criar_reserva`: grava dentro de uma transação `BEGIN IMMEDIATE` (`db.transaction`). Se o índice rejeitar a gravação (`sqlite3.IntegrityError`), a função lança `RegraViolada` e a tool devolve uma resposta normal ("data já ocupada"), sem erro de servidor.
- Cancelar é um *soft delete* (`status = 'cancelada'`), então a data volta a ficar livre e o código nunca é reaproveitado. `codigos_emitidos` guarda todos os códigos já usados, e `_novo_codigo` sorteia um código com chave primária única.

**Por que não depende do modelo:** a conferência de agenda (`verificar_disponibilidade`) é só uma cortesia para a conversa. Mesmo que duas aprovações simultâneas passem por ela, o índice único parcial deixa apenas uma gravação acontecer.

## Como rodar

### Pré-requisitos

- [uv](https://docs.astral.sh/uv/getting-started/installation/), que instala o Python 3.12 automaticamente, se necessário.
- Uma chave da Gemini API do [Google AI Studio](https://aistudio.google.com/apikey).
- Nenhum serviço externo: o armazenamento é SQLite local, em `var/`.

### Variáveis do `.env`

```bash
cp .env.example .env
```

| Variável | Obrigatória | Descrição |
|---|---|---|
| `GOOGLE_API_KEY` | sim | Chave do Google AI Studio. |
| `GOOGLE_GENAI_USE_VERTEXAI` | não | Use `FALSE` (ou deixe vazio) para usar a Gemini API do AI Studio. |
| `AURORA_MODEL` | não | Modelo Gemini dos agentes. Padrão: `gemini-3.6-flash`. |

O fluxo completo de uma avaliação faz algumas dezenas de chamadas ao modelo. As chamadas que recebem `429` (limite de cota por minuto) ou `5xx` são repetidas com backoff exponencial (`LLM` em `aurora/agents.py`, até 6 tentativas). Um erro `402 RESOURCE_EXHAUSTED` significa créditos esgotados no projeto do AI Studio e não é repetido: recarregue os créditos ou use outra chave.

### Comandos

```bash
uv sync                 # instala as dependências (google-adk==2.9.2 fixado)
uv run aurora-restore   # restaura reservas e visitantes ao estado de dados/
uv run aurora-api       # sobe a API em http://localhost:8000
```

A restauração pode ser executada com a API parada ou no ar. Ela não apaga as sessões. Para começar do zero, inclusive as conversas, apague a pasta `var/` e rode `uv run aurora-restore`.

### Exemplo

```bash
curl -s -X POST localhost:8000/sessoes -H 'content-type: application/json' -d '{"apartamento":"101"}'
curl -s -X POST localhost:8000/sessoes/<id>/mensagens -H 'content-type: application/json' \
     -d '{"texto":"Reserve o salão de festas para 2030-04-20"}'
curl -s -X POST localhost:8000/sessoes/<id>/confirmacoes -H 'content-type: application/json' \
     -d '{"id":"<id da confirmação>","confirmado":true}'
curl -s localhost:8000/sessoes/<id>/eventos
curl -s localhost:8000/apartamentos/101/reservas
curl -s localhost:8000/apartamentos/101/visitantes
```
