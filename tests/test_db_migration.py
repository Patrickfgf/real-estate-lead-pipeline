"""Teste da migração idempotente de `leads_raw` (banco pré-Fase-1 → transaction_type).

Bancos criados ANTES da Fase 1 (produção) não têm a coluna `transaction_type`. O
`get_connection()` (src/db.py) roda `ALTER TABLE leads_raw ADD COLUMN IF NOT EXISTS
transaction_type VARCHAR` para adicioná-la sem apagar dados. Os testes normais sempre
criam o schema novo (que JÁ inclui a coluna), então nunca exercitam esse caminho — este
teste cobre exatamente o banco de produção pré-Fase-1.

Como `get_connection()` é singleton amarrado a settings.duckdb_path, testamos a MESMA
instrução de migração diretamente contra um DuckDB temporário (tmp_path).
"""
import duckdb

# Schema de leads_raw ANTES da Fase 1: sem a coluna transaction_type.
_SCHEMA_PRE_FASE1 = """
CREATE TABLE leads_raw (
    source       VARCHAR NOT NULL,
    external_id  VARCHAR NOT NULL,
    name         VARCHAR,
    PRIMARY KEY (source, external_id)
);
"""

# As MESMAS instruções que get_connection() aplica em src/db.py (mantê-las em sincronia).
_MIGRATION = "ALTER TABLE leads_raw ADD COLUMN IF NOT EXISTS transaction_type VARCHAR"
_MIGRATION_LISTING_URL = "ALTER TABLE leads_raw ADD COLUMN IF NOT EXISTS listing_url VARCHAR"


def _columns(con) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'leads_raw'"
        ).fetchall()
    ]


def test_migracao_adiciona_coluna_preservando_dados(tmp_path):
    db_path = str(tmp_path / "pre_fase1.duckdb")

    # 1) Banco no formato pré-Fase-1, com um lead já gravado; fecha p/ liberar o arquivo.
    con = duckdb.connect(db_path)
    con.execute(_SCHEMA_PRE_FASE1)
    con.execute(
        "INSERT INTO leads_raw (source, external_id, name) VALUES (?, ?, ?)",
        ["wimoveis", "lead-antigo", "Cliente Antigo"],
    )
    assert "transaction_type" not in _columns(con)  # ponto de partida: coluna ausente
    con.close()

    # 2) Reabre e aplica a migração. IF NOT EXISTS → idempotente (rodar 2x é no-op).
    con = duckdb.connect(db_path)
    con.execute(_MIGRATION)
    con.execute(_MIGRATION)

    # 3) A coluna passou a existir e o dado antigo foi preservado (nova coluna nula).
    assert "transaction_type" in _columns(con)
    row = con.execute(
        "SELECT name, transaction_type FROM leads_raw WHERE external_id = 'lead-antigo'"
    ).fetchone()
    con.close()
    assert row == ("Cliente Antigo", None)


def test_migracao_adiciona_listing_url_preservando_dados(tmp_path):
    """Bancos anteriores a 2026-08 não têm `listing_url` (a URL do anúncio que a
    DFImóveis passou a enviar). Mesma garantia da migração de transaction_type."""
    db_path = str(tmp_path / "pre_listing_url.duckdb")

    con = duckdb.connect(db_path)
    con.execute(_SCHEMA_PRE_FASE1)
    con.execute(
        "INSERT INTO leads_raw (source, external_id, name) VALUES (?, ?, ?)",
        ["dfimoveis", "lead-sem-url", "Cliente Antigo"],
    )
    assert "listing_url" not in _columns(con)
    con.close()

    con = duckdb.connect(db_path)
    con.execute(_MIGRATION_LISTING_URL)
    con.execute(_MIGRATION_LISTING_URL)  # idempotente

    assert "listing_url" in _columns(con)
    row = con.execute(
        "SELECT name, listing_url FROM leads_raw WHERE external_id = 'lead-sem-url'"
    ).fetchone()
    con.close()
    assert row == ("Cliente Antigo", None)
