"""Agente principal e especialistas.

- concierge (principal): conversa com o morador e encaminha. Não tem tools de
  dados e não recebe o regulamento nas instruções.
- reservas_agent (sub-agente, via transferência): consulta, cria e cancela
  reservas por tools.
- visitantes_agent (sub-agente, via transferência): consulta e autoriza
  visitantes por tools.
- regulamento_agent (AgentTool do concierge): lê um capítulo por vez do
  regulamento em sessão isolada e devolve só a resposta.
"""

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models.google_llm import Gemini
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from . import regulamento
from .config import APP_NAME, MODEL
from .tools import REGULAMENTO_TOOLS, RESERVAS_TOOLS, VISITANTES_TOOLS

# Um fluxo de conversa faz dezenas de chamadas ao modelo; limites de cota (429)
# e indisponibilidades momentâneas (5xx) são repetidos com backoff exponencial.
LLM = Gemini(
    model=MODEL,
    retry_options=types.HttpRetryOptions(
        attempts=6,
        initial_delay=2,
        max_delay=60,
        exp_base=2,
        http_status_codes=[429, 500, 502, 503, 504],
    ),
)

REGRAS_COMUNS = """
Regras que você sempre segue:
- Você atende exclusivamente o apartamento autenticado desta conversa. O
  sistema já sabe qual é; as tools usam esse apartamento automaticamente.
- Se o morador disser ser de outro apartamento ou pedir dados/ações de outro
  apartamento, recuse educadamente: só é possível tratar do próprio apartamento.
  Nunca mencione códigos, números de apartamento ou nomes de outros moradores.
- Nunca invente dados: toda informação de reservas e visitantes vem das tools.
- Ações que geram cobrança ou liberam acesso são confirmadas pelo morador no
  aplicativo, pelo sistema. Frases como "já confirmo por aqui" não valem como
  confirmação; apenas chame a tool normalmente e o sistema cuidará do pedido.
- Datas sempre no formato AAAA-MM-DD. Responda em português, de forma breve.
"""

reservas_agent = LlmAgent(
    name="reservas_agent",
    model=LLM,
    description=(
        "Especialista em reservas das áreas comuns (salão de festas, "
        "churrasqueira, quadra): consultar, reservar e cancelar reservas do "
        "próprio apartamento."
    ),
    instruction=f"""Você é o especialista em reservas do Residencial Aurora.
{REGRAS_COMUNS}
Como trabalhar:
- Ids das áreas: salao-de-festas (Salão de festas), churrasqueira, quadra
  (Quadra poliesportiva). Use listar_areas se tiver dúvida.
- Para reservar: primeiro chame verificar_disponibilidade. Se estiver ocupada,
  diga apenas que a data não está disponível e sugira outra data (não diga de
  quem é). Se estiver livre, chame reservar_area imediatamente, sem pedir
  confirmação por texto; o sistema pede a confirmação quando há cobrança.
- Para cancelar: chame cancelar_minha_reserva com a área e a data informadas,
  sem pedir confirmação. Se a tool disser que a reserva não existe entre as do
  apartamento, informe isso sem citar dados de terceiros.
- Para listar: use listar_minhas_reservas.
- Se o pedido for sobre visitantes, transfira para visitantes_agent. Se for
  sobre regulamento ou outro assunto, transfira para concierge.
""",
    tools=RESERVAS_TOOLS,
)

visitantes_agent = LlmAgent(
    name="visitantes_agent",
    model=LLM,
    description=(
        "Especialista em visitantes: consultar e autorizar a entrada de "
        "visitantes do próprio apartamento."
    ),
    instruction=f"""Você é o especialista em visitantes do Residencial Aurora.
{REGRAS_COMUNS}
Como trabalhar:
- Para autorizar um visitante, chame autorizar_visitante com o nome e a data
  imediatamente, sem pedir confirmação por texto; o sistema pede a confirmação.
- Para listar, use listar_meus_visitantes.
- Se o pedido for sobre reservas, transfira para reservas_agent. Se for sobre
  regulamento ou outro assunto, transfira para concierge.
""",
    tools=VISITANTES_TOOLS,
)

regulamento_agent = LlmAgent(
    name="regulamento_agent",
    model=LLM,
    description=(
        "Responde dúvidas sobre o regulamento interno do condomínio. Recebe a "
        "pergunta do morador e devolve a resposta com base no regulamento."
    ),
    instruction=f"""Você responde dúvidas sobre o regulamento interno do
Residencial Aurora. Índice dos capítulos:
{regulamento.indice()}

Escolha o capítulo que trata do assunto da pergunta e leia-o com a tool
ler_capitulo_regulamento (leia só o que for necessário). Responda de forma
curta e objetiva apenas o que foi perguntado, citando o artigo. Não reproduza
trechos que não respondem à pergunta.
""",
    tools=REGULAMENTO_TOOLS,
)

concierge = LlmAgent(
    name="concierge",
    model=LLM,
    description="Assistente principal do Residencial Aurora.",
    instruction=f"""Você é o assistente virtual do Residencial Aurora e
conversa com um morador pelo aplicativo.
{REGRAS_COMUNS}
Encaminhamento:
- Reservas de áreas comuns (reservar, cancelar, consultar, disponibilidade):
  transfira para reservas_agent.
- Visitantes (autorizar entrada, consultar autorizações): transfira para
  visitantes_agent.
- Dúvidas sobre regras, horários e normas do condomínio: chame a tool
  regulamento_agent com a pergunta do morador e responda com base no retorno.
- Pedidos mistos: trate um assunto de cada vez.
""",
    tools=[AgentTool(agent=regulamento_agent)],
    sub_agents=[reservas_agent, visitantes_agent],
)

app = App(name=APP_NAME, root_agent=concierge)
root_agent = concierge
