"""Operações de domínio. Toda função que lê ou altera dados de um morador
recebe o apartamento explicitamente; quem chama (as tools) obtém esse valor da
sessão, nunca do modelo."""

import secrets
import sqlite3
from datetime import date

from . import catalogo, db


class RegraViolada(Exception):
    """Erro de negócio com mensagem segura para ir à conversa."""


def validar_area(area: str) -> dict:
    info = catalogo.areas().get(area)
    if not info:
        validas = ", ".join(catalogo.areas())
        raise RegraViolada(f"Área '{area}' não existe. Áreas válidas: {validas}.")
    return info


def validar_data(data: str) -> str:
    try:
        return date.fromisoformat(data).isoformat()
    except (TypeError, ValueError):
        raise RegraViolada("Data inválida. Use o formato AAAA-MM-DD.") from None


def area_gera_cobranca(area: str) -> bool:
    info = catalogo.areas().get(area)
    # Área desconhecida: por segurança, trata como se gerasse cobrança.
    return info is None or float(info["taxa"]) > 0


def listar_reservas(apartamento: str) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT codigo, area, data FROM reservas"
            " WHERE apartamento = ? AND status = 'ativa' ORDER BY data, codigo",
            (apartamento,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def listar_visitantes(apartamento: str) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, id",
            (apartamento,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def data_livre(area: str, data: str) -> bool:
    """Só diz se a data está livre; nunca expõe de quem é a reserva."""
    validar_area(area)
    data = validar_data(data)
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND status = 'ativa'",
            (area, data),
        ).fetchone()
    finally:
        conn.close()
    return row is None


def _novo_codigo(conn: sqlite3.Connection) -> str:
    while True:
        codigo = f"RSV-{secrets.token_hex(4).upper()}"
        try:
            conn.execute("INSERT INTO codigos_emitidos (codigo) VALUES (?)", (codigo,))
            return codigo
        except sqlite3.IntegrityError:
            continue  # colisão improvável: tenta outro


def criar_reserva(apartamento: str, area: str, data: str) -> dict:
    """Grava a reserva. A exclusividade é garantida pelo índice único parcial
    ux_reserva_ativa no momento do INSERT (Garantia 5)."""
    validar_area(area)
    data = validar_data(data)
    try:
        with db.transaction() as conn:
            codigo = _novo_codigo(conn)
            conn.execute(
                "INSERT INTO reservas (codigo, apartamento, area, data, status)"
                " VALUES (?, ?, ?, ?, 'ativa')",
                (codigo, apartamento, area, data),
            )
    except sqlite3.IntegrityError:
        raise RegraViolada(
            f"A data {data} já está ocupada para essa área. Escolha outra data."
        ) from None
    return {"codigo": codigo, "area": area, "data": data}


def cancelar_reserva(apartamento: str, area: str, data: str) -> dict:
    """Cancela apenas reservas do próprio apartamento (filtro no WHERE)."""
    validar_area(area)
    data = validar_data(data)
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT codigo FROM reservas WHERE apartamento = ? AND area = ?"
            " AND data = ? AND status = 'ativa'",
            (apartamento, area, data),
        ).fetchone()
        if row is None:
            raise RegraViolada(
                "Não existe reserva dessa área nessa data entre as reservas do seu"
                " apartamento. Só é possível cancelar reservas do próprio apartamento."
            )
        conn.execute(
            "UPDATE reservas SET status = 'cancelada' WHERE codigo = ?", (row["codigo"],)
        )
    return {"codigo": row["codigo"], "area": area, "data": data}


def autorizar_visitante(apartamento: str, nome: str, data: str) -> dict:
    nome = " ".join((nome or "").split())
    if not nome:
        raise RegraViolada("Informe o nome do visitante.")
    data = validar_data(data)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
            (apartamento, nome, data),
        )
    return {"nome": nome, "data": data}
