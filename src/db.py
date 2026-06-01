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
    PRIMARY KEY (source, external_id)
);
"""


def get_connection() -> duckdb.DuckDBPyConnection:
    """Retorna a conexão singleton, criando o arquivo e a tabela na 1ª chamada."""
    global _con
    if _con is None:
        Path(settings.duckdb_path).parent.mkdir(parents=True, exist_ok=True)
        _con = duckdb.connect(settings.duckdb_path)
        _con.execute(_SCHEMA)
    return _con


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
