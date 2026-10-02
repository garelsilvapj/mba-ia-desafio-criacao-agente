"""Restaura reservas e visitantes ao estado de dados/*.json.

As sessões (conversas) são preservadas. A tabela codigos_emitidos também é
preservada, para que códigos gerados antes do restore nunca voltem a ser usados.
"""

from . import catalogo, db


def restore() -> None:
    db.init_schema()
    with db.transaction() as conn:
        conn.execute("DELETE FROM reservas")
        conn.execute("DELETE FROM visitantes")
        for r in catalogo.reservas_iniciais():
            conn.execute(
                "INSERT INTO reservas (codigo, apartamento, area, data, status)"
                " VALUES (?, ?, ?, ?, 'ativa')",
                (r["codigo"], r["apartamento"], r["area"], r["data"]),
            )
            conn.execute(
                "INSERT OR IGNORE INTO codigos_emitidos (codigo) VALUES (?)",
                (r["codigo"],),
            )
        for v in catalogo.visitantes_iniciais():
            conn.execute(
                "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
                (v["apartamento"], v["nome"], v["data"]),
            )


def main() -> None:
    restore()
    print("Reservas e visitantes restaurados a partir de dados/.")


if __name__ == "__main__":
    main()
