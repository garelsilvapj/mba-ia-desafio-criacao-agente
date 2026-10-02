"""Configuração central: caminhos e modelo."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

DADOS_DIR = ROOT_DIR / "dados"
VAR_DIR = Path(os.getenv("AURORA_VAR_DIR", ROOT_DIR / "var"))
VAR_DIR.mkdir(parents=True, exist_ok=True)

# Banco de domínio (reservas, visitantes, códigos emitidos).
DOMAIN_DB_PATH = VAR_DIR / "aurora.db"
# Banco das sessões do ADK (eventos e state), persistido entre reinícios.
SESSIONS_DB_URL = f"sqlite+aiosqlite:///{VAR_DIR / 'sessions.db'}"

APP_NAME = "residencial_aurora"
MODEL = os.getenv("AURORA_MODEL") or "gemini-3.6-flash"
