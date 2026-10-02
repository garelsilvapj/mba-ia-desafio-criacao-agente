"""Banco de domínio em SQLite.

A exclusividade de reserva (Garantia 5) mora no schema: o índice único parcial
`ux_reserva_ativa` impede duas reservas *ativas* para a mesma área e data. A
verificação acontece dentro do próprio INSERT, no instante da gravação, então
nenhuma corrida entre "conferir agenda" e "gravar" consegue furá-la.
"""

import sqlite3
from contextlib import contextmanager

from .config import DOMAIN_DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS reservas (
    codigo      TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL,
    area        TEXT NOT NULL,
    data        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ativa' CHECK (status IN ('ativa', 'cancelada'))
);

-- Garantia 5: no máximo uma reserva ativa por área e data, validado na gravação.
CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_ativa
    ON reservas (area, data) WHERE status = 'ativa';

-- Todo código já emitido (inclusive de reservas canceladas ou apagadas pelo
-- restore). Nunca é limpo, então um código novo jamais repete um antigo.
CREATE TABLE IF NOT EXISTS codigos_emitidos (
    codigo TEXT PRIMARY KEY
);

-- Sessões de conversa -> apartamento autenticado (fixado na criação).
CREATE TABLE IF NOT EXISTS sessoes (
    session_id  TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS visitantes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento TEXT NOT NULL,
    nome        TEXT NOT NULL,
    data        TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DOMAIN_DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def transaction():
    """Transação IMMEDIATE: pega o lock de escrita logo no início."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


def init_schema() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
