"""Dados estáticos do condomínio (somente leitura de dados/)."""

import json
from functools import cache

from .config import DADOS_DIR


def _load(nome: str):
    return json.loads((DADOS_DIR / nome).read_text(encoding="utf-8"))


@cache
def apartamentos() -> dict[str, str]:
    return {a["numero"]: a["morador"] for a in _load("apartamentos.json")}


@cache
def areas() -> dict[str, dict]:
    return {a["id"]: a for a in _load("areas.json")}


def reservas_iniciais() -> list[dict]:
    return _load("reservas.json")


def visitantes_iniciais() -> list[dict]:
    return _load("visitantes.json")
