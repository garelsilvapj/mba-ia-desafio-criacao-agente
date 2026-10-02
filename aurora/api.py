"""API HTTP do assistente (FastAPI + Runner do ADK)."""

import asyncio
import logging
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from google.adk.events import Event
from google.adk.flows.llm_flows.functions import REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService, Session
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from . import catalogo, db, repo
from .agents import app as adk_app
from .config import APP_NAME, SESSIONS_DB_URL
from .restore import restore
from .tools import ACOES_CONFIRMAVEIS, STATE_APARTAMENTO

logger = logging.getLogger("aurora.api")

# Garantia 3: eventos e state das sessões persistidos em SQLite.
session_service = DatabaseSessionService(
    SESSIONS_DB_URL, connect_args={"timeout": 30}
)
runner = Runner(app=adk_app, session_service=session_service)

# Serializa as requisições de uma mesma sessão (mensagens e confirmações).
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_schema()
    conn = db.connect()
    try:
        vazio = conn.execute("SELECT COUNT(*) FROM codigos_emitidos").fetchone()[0] == 0
    finally:
        conn.close()
    if vazio:  # primeira execução: carrega o estado inicial de dados/
        restore()
    yield


api = FastAPI(title="Residencial Aurora", lifespan=lifespan)


@api.exception_handler(genai_errors.APIError)
async def _modelo_indisponivel(_, exc: genai_errors.APIError):
    # Falha do provedor do modelo (quota, créditos, indisponibilidade).
    return JSONResponse(
        status_code=503,
        content={"detail": f"Modelo indisponível no momento: {exc.status} ({exc.code})."},
    )


class NovaSessao(BaseModel):
    apartamento: str


class Mensagem(BaseModel):
    texto: str


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


# ------------------------------------------------------------------ helpers


def _apartamento_da_sessao_id(session_id: str) -> str | None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT apartamento FROM sessoes WHERE session_id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()
    return row["apartamento"] if row else None


async def _carregar_sessao(session_id: str) -> Session:
    apartamento = _apartamento_da_sessao_id(session_id)
    session = None
    if apartamento:
        session = await session_service.get_session(
            app_name=APP_NAME, user_id=apartamento, session_id=session_id
        )
    if session is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    return session


def _confirmacoes_pendentes(session: Session) -> list[dict]:
    """Garantia 1: pendências vêm dos eventos gravados pelo ADK, não do texto.

    Pendente = chamada `adk_request_confirmation` feita por um agente sem uma
    FunctionResponse correspondente enviada pela rota de confirmações.
    """
    respondidas = {
        fr.id
        for ev in session.events
        if ev.author == "user"
        for fr in ev.get_function_responses()
        if fr.name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
    }
    pendentes = []
    for ev in session.events:
        if ev.author == "user":
            continue
        for fc in ev.get_function_calls():
            if fc.name != REQUEST_CONFIRMATION_FUNCTION_CALL_NAME or fc.id in respondidas:
                continue
            original = (fc.args or {}).get("originalFunctionCall") or {}
            acao = original.get("name", "")
            args = original.get("args") or {}
            campos = ACOES_CONFIRMAVEIS.get(acao, tuple(args))
            detalhes = {k: args.get(k) for k in campos}
            if acao == "reservar_area" and args.get("area") in catalogo.areas():
                detalhes["taxa"] = catalogo.areas()[args["area"]]["taxa"]
            pendentes.append({"id": fc.id, "acao": acao, "detalhes": detalhes})
    return pendentes


def _resposta_confirmacao(conf_id: str, confirmado: bool) -> types.Part:
    return types.Part(
        function_response=types.FunctionResponse(
            id=conf_id,
            name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
            response={"confirmed": confirmado},
        )
    )


async def _executar(session: Session, content: types.Content) -> dict:
    textos: list[str] = []
    erros: list[str] = []
    async for event in runner.run_async(
        user_id=session.user_id, session_id=session.id, new_message=content
    ):
        if event.error_code:
            # O ADK pode transformar uma falha do modelo em evento de erro em vez
            # de levantar exceção; sem isso a resposta sairia vazia, em silêncio.
            logger.warning(
                "Sessão %s: evento de erro %s: %s",
                session.id, event.error_code, event.error_message,
            )
            erros.append(event.error_code)
        if event.author == "user" or not event.content or event.partial:
            continue
        for part in event.content.parts or []:
            if part.text and not part.thought:
                textos.append(part.text.strip())
    atualizada = await _carregar_sessao(session.id)
    resposta = "\n\n".join(t for t in textos if t)
    if not resposta and erros:
        resposta = f"Não consegui concluir agora ({erros[-1]}). Tente novamente."
    return {
        "resposta": resposta,
        "confirmacoes_pendentes": _confirmacoes_pendentes(atualizada),
    }


# ------------------------------------------------------------------- rotas


@api.post("/sessoes", status_code=201)
async def criar_sessao(body: NovaSessao):
    apartamento = body.apartamento.strip()
    if apartamento not in catalogo.apartamentos():
        raise HTTPException(status_code=422, detail="Apartamento inexistente.")
    session_id = str(uuid.uuid4())
    # Garantia 2: o apartamento é fixado aqui, uma única vez, pelo servidor:
    # vira o user_id da sessão ADK e o state "apartamento" lido pelas tools.
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=apartamento,
        session_id=session_id,
        state={STATE_APARTAMENTO: apartamento},
    )
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO sessoes (session_id, apartamento) VALUES (?, ?)",
            (session_id, apartamento),
        )
    return {"session_id": session_id}


@api.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(session_id: str, body: Mensagem):
    await _carregar_sessao(session_id)
    async with _locks[session_id]:
        session = await _carregar_sessao(session_id)
        # Mensagem nova com confirmação em aberto: as pendências são recusadas
        # antes (nada executa sem aprovação explícita pela rota de confirmações).
        # O ADK exige que respostas de confirmação e texto vão em mensagens
        # separadas.
        pendentes = _confirmacoes_pendentes(session)
        if pendentes:
            recusas = [_resposta_confirmacao(p["id"], False) for p in pendentes]
            await _executar(session, types.Content(role="user", parts=recusas))
        content = types.Content(role="user", parts=[types.Part(text=body.texto)])
        return await _executar(session, content)


@api.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(session_id: str, body: RespostaConfirmacao):
    await _carregar_sessao(session_id)
    async with _locks[session_id]:
        session = await _carregar_sessao(session_id)
        # Garantia 1: só aceita id pendente nesta sessão; já respondida,
        # inexistente ou de outra sessão -> 409 e nada executa.
        if body.id not in {p["id"] for p in _confirmacoes_pendentes(session)}:
            return JSONResponse(
                status_code=409,
                content={"detail": "Não existe confirmação pendente com esse id nesta sessão."},
            )
        content = types.Content(
            role="user", parts=[_resposta_confirmacao(body.id, body.confirmado)]
        )
        return await _executar(session, content)


@api.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str):
    session = await _carregar_sessao(session_id)
    return [_evento_json(e) for e in session.events]


def _evento_json(event: Event) -> dict:
    return event.model_dump(mode="json", by_alias=True, exclude_none=True)


@api.get("/apartamentos/{numero}/reservas")
async def reservas_do_apartamento(numero: str):
    return repo.listar_reservas(numero)


@api.get("/apartamentos/{numero}/visitantes")
async def visitantes_do_apartamento(numero: str):
    return repo.listar_visitantes(numero)
