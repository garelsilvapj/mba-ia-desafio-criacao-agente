"""Acesso ao regulamento por capítulo (Garantia 4).

O texto nunca é colocado inteiro em instruções nem em eventos: a tool
`ler_capitulo_regulamento` devolve só o capítulo pedido, e ela só existe no
especialista `regulamento_agent`, que roda como AgentTool em sessão isolada.
"""

import re
from functools import cache

from .config import DADOS_DIR

_CAPITULO_RE = re.compile(r"^## Capítulo ([IVXLC]+): (.+)$", re.MULTILINE)
_ROMANOS = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def _romano_para_int(r: str) -> int:
    total = 0
    for a, b in zip(r, r[1:] + " "):
        v = _ROMANOS[a]
        total += -v if b != " " and _ROMANOS[b] > v else v
    return total


@cache
def capitulos() -> dict[int, dict]:
    texto = (DADOS_DIR / "regulamento.md").read_text(encoding="utf-8")
    matches = list(_CAPITULO_RE.finditer(texto))
    caps: dict[int, dict] = {}
    for i, m in enumerate(matches):
        fim = matches[i + 1].start() if i + 1 < len(matches) else len(texto)
        numero = _romano_para_int(m.group(1))
        caps[numero] = {
            "numero": numero,
            "titulo": f"Capítulo {m.group(1)}: {m.group(2).strip()}",
            "texto": texto[m.start():fim].strip(),
        }
    return caps


def indice() -> str:
    """Só os títulos dos capítulos, usado nas instruções do especialista."""
    return "\n".join(f"{n}. {c['titulo']}" for n, c in capitulos().items())
