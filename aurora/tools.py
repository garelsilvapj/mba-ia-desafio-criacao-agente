"""Tools dos especialistas.

Garantia 2: nenhuma tool recebe o apartamento como parâmetro. O apartamento
vem de `_apartamento_da_sessao`, que lê o user_id da sessão (definido pelo
servidor na criação da sessão) e confere com o state gravado pelo servidor.

Garantia 1: `reservar_area` (quando a área tem taxa) e `autorizar_visitante`
são FunctionTools com `require_confirmation`; o ADK só executa a função depois
que a API entrega uma FunctionResponse de confirmação vinda da rota
/confirmacoes. Nada que o morador escreva substitui essa resposta.
"""

from typing import Literal

from google.adk.tools import FunctionTool, ToolContext

from . import catalogo, regulamento, repo

STATE_APARTAMENTO = "apartamento"

# Ids de dados/areas.json. Vira `enum` na declaração da tool, então o modelo não
# consegue pedir uma área inexistente; repo.validar_area continua como defesa.
Area = Literal["salao-de-festas", "churrasqueira", "quadra"]


class SessaoInvalida(Exception):
    pass


def _apartamento_da_sessao(tool_context: ToolContext) -> str:
    apartamento = tool_context.user_id
    if (
        apartamento not in catalogo.apartamentos()
        or tool_context.state.get(STATE_APARTAMENTO) != apartamento
    ):
        raise SessaoInvalida("Sessão sem apartamento válido.")
    return apartamento


def _erro(e: Exception) -> dict:
    return {"status": "erro", "mensagem": str(e)}


# ---------------------------------------------------------------- reservas


def listar_areas() -> dict:
    """Lista as áreas comuns que podem ser reservadas, com id, nome e se a
    reserva gera cobrança (taxa)."""
    return {
        "areas": [
            {"id": a["id"], "nome": a["nome"], "taxa": a["taxa"],
             "gera_cobranca": float(a["taxa"]) > 0}
            for a in catalogo.areas().values()
        ]
    }


def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento do morador desta conversa."""
    try:
        return {"reservas": repo.listar_reservas(_apartamento_da_sessao(tool_context))}
    except (repo.RegraViolada, SessaoInvalida) as e:
        return _erro(e)


def verificar_disponibilidade(area: Area, data: str) -> dict:
    """Verifica se uma área comum está livre em uma data.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    try:
        livre = repo.data_livre(area, data)
    except repo.RegraViolada as e:
        return _erro(e)
    # Garantia 2: só "livre" ou "ocupada", nunca de quem é a reserva.
    return {"area": area, "data": data, "situacao": "livre" if livre else "ocupada"}


def reservar_area(area: Area, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento do morador desta conversa.
    Áreas com taxa geram cobrança e exigem confirmação do morador pelo sistema.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    try:
        apartamento = _apartamento_da_sessao(tool_context)
        reserva = repo.criar_reserva(apartamento, area, data)
    except (repo.RegraViolada, SessaoInvalida) as e:
        return _erro(e)
    taxa = catalogo.areas()[area]["taxa"]
    return {"status": "reservada", **reserva,
            "cobranca": f"R$ {taxa:.2f}" if float(taxa) > 0 else "sem cobrança"}


def _reserva_gera_cobranca(area: Area, data: str, tool_context: ToolContext) -> bool:
    """Garantia 1: decide pela taxa da área (dado do sistema), não pelo modelo."""
    return repo.area_gera_cobranca(area)


def cancelar_minha_reserva(area: Area, data: str, tool_context: ToolContext) -> dict:
    """Cancela uma reserva do próprio apartamento do morador desta conversa.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data da reserva no formato AAAA-MM-DD.
    """
    try:
        apartamento = _apartamento_da_sessao(tool_context)
        cancelada = repo.cancelar_reserva(apartamento, area, data)
    except (repo.RegraViolada, SessaoInvalida) as e:
        return _erro(e)
    return {"status": "cancelada", **cancelada}


# -------------------------------------------------------------- visitantes


def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista as autorizações de visita do apartamento do morador desta conversa."""
    try:
        return {"visitantes": repo.listar_visitantes(_apartamento_da_sessao(tool_context))}
    except (repo.RegraViolada, SessaoInvalida) as e:
        return _erro(e)


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante no prédio para o apartamento do
    morador desta conversa. Sempre exige confirmação do morador pelo sistema.

    Args:
        nome: nome completo do visitante.
        data: data da visita no formato AAAA-MM-DD.
    """
    try:
        apartamento = _apartamento_da_sessao(tool_context)
        autorizacao = repo.autorizar_visitante(apartamento, nome, data)
    except (repo.RegraViolada, SessaoInvalida) as e:
        return _erro(e)
    return {"status": "autorizado", **autorizacao}


# ------------------------------------------------------------- regulamento


def ler_capitulo_regulamento(numero: int) -> dict:
    """Lê um único capítulo do regulamento interno.

    Args:
        numero: número do capítulo (1 a 14), conforme o índice.
    """
    capitulo = regulamento.capitulos().get(int(numero))
    if not capitulo:
        return {"status": "erro", "mensagem": "Capítulo inexistente."}
    return {"titulo": capitulo["titulo"], "texto": capitulo["texto"]}


# ------------------------------------------------------------ FunctionTools

RESERVAS_TOOLS = [
    FunctionTool(listar_areas),
    FunctionTool(listar_minhas_reservas),
    FunctionTool(verificar_disponibilidade),
    # Garantia 1: confirmação exigida quando a área tem taxa > 0.
    FunctionTool(reservar_area, require_confirmation=_reserva_gera_cobranca),
    FunctionTool(cancelar_minha_reserva),
]

VISITANTES_TOOLS = [
    FunctionTool(listar_meus_visitantes),
    # Garantia 1: liberar acesso sempre exige confirmação.
    FunctionTool(autorizar_visitante, require_confirmation=True),
]

REGULAMENTO_TOOLS = [FunctionTool(ler_capitulo_regulamento)]

# Tools que exigem confirmação e os campos mostrados em `detalhes`.
ACOES_CONFIRMAVEIS = {
    "reservar_area": ("area", "data"),
    "autorizar_visitante": ("nome", "data"),
}
