"""Camada de dados: conexão DuckDB, criação da tabela e inserção com dedup.

Usamos uma única conexão protegida por lock — o volume de leads é baixo
(alguns por dia), então serializar a escrita é simples e seguro. DuckDB é
single-writer, e o lock evita corrida entre requisições do FastAPI.
"""
import threading
from pathlib import Path

import duckdb

from src.config import settings
from src.models import Lead

_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads_raw (
    source          VARCHAR   NOT NULL,
    external_id     VARCHAR   NOT NULL,
    name            VARCHAR,
    email           VARCHAR,
    phone           VARCHAR,
    message         VARCHAR,
    listing_ref     VARCHAR,
    advertiser_code VARCHAR,
    agency_code     VARCHAR,
    cpf             VARCHAR,
    lead_date       TIMESTAMPTZ,
    raw_payload     VARCHAR,
    received_at     TIMESTAMPTZ,
    trello_card_id  VARCHAR,
    PRIMARY KEY (source, external_id)
);
"""

# "Caixa de revisão" (dead-letter): qualquer POST que NÃO passe na validação é
# guardado aqui em vez de se perder. Garante que nenhum lead suma só porque veio
# num formato inesperado — a gente inspeciona, conserta o parsing e reprocessa.
_SCHEMA_DEAD_LETTER = """
CREATE TABLE IF NOT EXISTS leads_dead_letter (
    received_at  TIMESTAMPTZ,
    source       VARCHAR,
    error        VARCHAR,
    raw_payload  VARCHAR
);
"""

# Colunas devolvidas para montar o card do Trello (Fase 4).
_LEAD_COLS = [
    "source",
    "external_id",
    "name",
    "email",
    "phone",
    "message",
    "listing_ref",
    "advertiser_code",
    "agency_code",
    "lead_date",
    "received_at",
]


def get_connection() -> duckdb.DuckDBPyConnection:
    """Retorna a conexão singleton, criando o arquivo e a tabela na 1ª chamada."""
    global _con
    if _con is None:
        Path(settings.duckdb_path).parent.mkdir(parents=True, exist_ok=True)
        _con = duckdb.connect(settings.duckdb_path)
        _con.execute(_SCHEMA)
        _con.execute(_SCHEMA_DEAD_LETTER)
    return _con


def ping() -> None:
    """Liveness do caminho de escrita: confirma que o DuckDB responde a uma query.

    Usado pelo /health para um deep check — um SELECT estático no app não prova que
    o banco (onde TODA ingestão grava) está acessível. Levanta se a conexão falhar.
    """
    con = get_connection()
    with _lock:
        con.execute("SELECT 1").fetchone()


def insert_lead(lead: Lead) -> bool:
    """Insere o lead. Retorna True se gravou, False se já existia (dedup).

    O dedup é por (source, external_id) via PRIMARY KEY + ON CONFLICT DO NOTHING.
    O RETURNING só devolve linha quando a inserção de fato aconteceu.
    """
    con = get_connection()
    with _lock:
        row = con.execute(
            """
            INSERT INTO leads_raw
                (source, external_id, name, email, phone, message, listing_ref,
                 advertiser_code, agency_code, cpf, lead_date, raw_payload, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source, external_id) DO NOTHING
            RETURNING external_id;
            """,
            [
                lead.source,
                lead.external_id,
                lead.name,
                lead.email,
                lead.phone,
                lead.message,
                lead.listing_ref,
                lead.advertiser_code,
                lead.agency_code,
                lead.cpf,
                lead.lead_date,
                lead.raw_payload,
                lead.received_at,
            ],
        ).fetchone()
    return row is not None


def fetch_pending_leads(limit: int = 50) -> list[dict]:
    """Leads ainda não enviados ao Trello (trello_card_id IS NULL), mais antigos primeiro."""
    con = get_connection()
    rows = con.execute(
        f"""
        SELECT {", ".join(_LEAD_COLS)}
        FROM leads_raw
        WHERE trello_card_id IS NULL
        ORDER BY received_at
        LIMIT ?;
        """,
        [limit],
    ).fetchall()
    return [dict(zip(_LEAD_COLS, row, strict=True)) for row in rows]


def carded_contacts() -> list[dict]:
    """Contatos (telefone/e-mail) dos leads que já têm card no Trello.

    Base para a dedup de identidade na carga: permite reconhecer que um lead novo
    é a mesma pessoa de alguém já carregado (inclusive de outro portal).
    """
    con = get_connection()
    rows = con.execute(
        "SELECT source, external_id, phone, email, trello_card_id "
        "FROM leads_raw WHERE trello_card_id IS NOT NULL;"
    ).fetchall()
    cols = ["source", "external_id", "phone", "email", "trello_card_id"]
    return [dict(zip(cols, row, strict=True)) for row in rows]


def set_trello_card_id(source: str, external_id: str, card_id: str) -> None:
    """Marca o lead como já carregado no Trello (idempotência da Fase 4)."""
    con = get_connection()
    with _lock:
        con.execute(
            "UPDATE leads_raw SET trello_card_id = ? WHERE source = ? AND external_id = ?;",
            [card_id, source, external_id],
        )


def leads_raw_dataframe():
    """Lê toda a camada crua `leads_raw` como DataFrame pandas (camada curada, Fase 2)."""
    con = get_connection()
    with _lock:
        return con.execute("SELECT * FROM leads_raw").df()


def rebuild_clean_table(df) -> None:
    """(Re)cria `leads_clean` a partir de um DataFrame já curado. Idempotente.

    CREATE OR REPLACE substitui a tabela inteira — o transform (`src/transform.py`)
    sempre reconstrói a curada do zero a partir da crua, então não há estado parcial.
    """
    con = get_connection()
    with _lock:
        con.register("_clean_df", df)
        try:
            con.execute("CREATE OR REPLACE TABLE leads_clean AS SELECT * FROM _clean_df")
        finally:
            con.unregister("_clean_df")


def insert_dead_letter(source: str, error: str, raw_payload: str, received_at) -> None:
    """Guarda na caixa de revisão um POST que não pôde ser processado.

    Rede de segurança: chamado quando o payload não parseia/valida. Preserva o
    conteúdo cru para inspeção e reprocessamento — nenhum lead se perde.
    """
    con = get_connection()
    with _lock:
        con.execute(
            "INSERT INTO leads_dead_letter (received_at, source, error, raw_payload) VALUES (?, ?, ?, ?);",
            [received_at, source, error, raw_payload],
        )


def fetch_dead_letter(limit: int = 20) -> list[dict]:
    """Últimas entradas da caixa de revisão (mais recentes primeiro)."""
    con = get_connection()
    rows = con.execute(
        "SELECT received_at, source, error, raw_payload FROM leads_dead_letter "
        "ORDER BY received_at DESC LIMIT ?;",
        [limit],
    ).fetchall()
    cols = ["received_at", "source", "error", "raw_payload"]
    return [dict(zip(cols, row, strict=True)) for row in rows]


# ----------------------------------------------------------------------------
# CLI de inspeção (operação): python -m src.db dead-letter
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    for _stream in (sys.stdout, sys.stderr):  # console Windows (cp1252) tolerante
        try:
            _stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    if (sys.argv[1] if len(sys.argv) > 1 else "") != "dead-letter":
        print("uso: python -m src.db dead-letter", file=sys.stderr)
        sys.exit(1)

    entradas = fetch_dead_letter()
    if not entradas:
        print("Caixa de revisão vazia — nenhum lead pendente de conserto. ✅")
    else:
        print(f"{len(entradas)} entrada(s) na caixa de revisão (mais recentes primeiro):\n")
        for e in entradas:
            print(f"- {e['received_at']} [{e['source']}] erro: {e['error']}")
            print(f"    payload: {e['raw_payload'][:200]}")
