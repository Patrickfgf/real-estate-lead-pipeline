"""Testes da persistência + resolução do tipo de operação (Aluguel/Compra).

Fase 1 da separação de leads em 2 quadros Trello: o `transaction_type` passa a
ser gravado em `leads_raw` para que a carga do Trello possa rotear o card pro
quadro certo (Locações vs Compra).
"""
from datetime import UTC, datetime

from src.db import get_connection, insert_lead, set_transaction_type
from src.models import Lead


def _make_lead(external_id: str, source: str = "dfimoveis", raw_payload: str = "{}") -> Lead:
    return Lead(
        external_id=external_id,
        source=source,
        name="Fulano de Tal",
        raw_payload=raw_payload,
        received_at=datetime.now(UTC),
    )


def test_set_transaction_type_persiste_na_coluna():
    insert_lead(_make_lead("tt-set-1"))
    set_transaction_type("dfimoveis", "tt-set-1", "Aluguel")
    row = get_connection().execute(
        "SELECT transaction_type FROM leads_raw "
        "WHERE source = 'dfimoveis' AND external_id = 'tt-set-1'"
    ).fetchone()
    assert row[0] == "Aluguel"


def test_insert_lead_grava_transaction_type_no_proprio_insert():
    """FIX: o tipo já nasce gravado NO PRÓPRIO INSERT (não por um UPDATE posterior).

    Antes, `insert_lead` gravava o tipo NULL e só um `set_transaction_type` posterior
    o resolvia — janela em que um push concorrente cardava o lead no quadro fallback
    errado. Passar o tipo já no `Lead` fecha essa janela. Aqui provamos que o valor
    persistido veio do INSERT (o lead nunca teve tipo NULL)."""
    lead = Lead(
        external_id="tt-insert-1",
        source="dfimoveis",
        name="Fulano de Tal",
        transaction_type="Compra",
        raw_payload="{}",
        received_at=datetime.now(UTC),
    )
    assert insert_lead(lead) is True
    row = get_connection().execute(
        "SELECT transaction_type FROM leads_raw "
        "WHERE source = 'dfimoveis' AND external_id = 'tt-insert-1'"
    ).fetchone()
    assert row[0] == "Compra"
