"""Testes da carga no Trello (Fase 4) — sem tocar na API real (mock)."""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import src.trello as trello
from src.db import get_connection, insert_lead
from src.models import Lead


def test_create_card_envia_idlabels_na_query(monkeypatch):
    """A etiqueta de origem deve ir em `params` (query), não no corpo —
    senão o Trello ignora e o card sai sem etiqueta."""
    fake = SimpleNamespace(
        trello_list_id="LIST", trello_label_wimoveis="LBL",
        trello_api_key="K", trello_api_token="T",
    )
    monkeypatch.setattr(trello, "settings", fake)

    capturado = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "card1"}

    def _fake_post(url, params=None, data=None, timeout=None):
        capturado["params"] = params
        capturado["data"] = data
        return _Resp()

    monkeypatch.setattr(trello.requests, "post", _fake_post)

    lead = {
        "source": "wimoveis", "external_id": "x", "name": "N", "email": None,
        "phone": None, "message": None, "listing_ref": None, "advertiser_code": None,
        "agency_code": None, "lead_date": None, "received_at": None,
    }
    assert trello.create_card(lead) == "card1"
    assert capturado["params"]["idLabels"] == "LBL"
    assert capturado["params"]["idList"] == "LIST"
    assert "name" in capturado["data"]


def _make_lead(ext: str) -> Lead:
    return Lead(
        external_id=ext,
        source="wimoveis",
        name="Fulano de Tal",
        email="fulano@email.com",
        phone="(61) 90000-0000",
        message="Tenho interesse no imóvel.",
        listing_ref="REF-123",
        advertiser_code="ANUN-1",
        agency_code="IMOB-1",
        cpf=None,
        lead_date=None,
        raw_payload="{}",
        received_at=datetime.now(ZoneInfo("America/Sao_Paulo")),
    )


def test_card_name_inclui_referencia():
    lead = {"name": "Fulano", "listing_ref": "AP-9"}
    assert trello._card_name(lead) == "🏠 Fulano — AP-9"


def test_card_desc_tem_marcador_de_rastreio():
    lead = {
        "source": "wimoveis",
        "external_id": "abc-123",
        "name": "Fulano",
        "email": None,
        "phone": None,
        "message": None,
        "listing_ref": None,
        "advertiser_code": None,
        "agency_code": None,
        "lead_date": None,
        "received_at": None,
    }
    desc = trello._card_desc(lead)
    assert "jare-ext:wimoveis:abc-123" in desc


def test_push_pending_cria_e_marca_idempotente(monkeypatch):
    insert_lead(_make_lead("trello-1"))
    insert_lead(_make_lead("trello-2"))

    chamadas = []

    def fake_create(lead):
        chamadas.append(lead["external_id"])
        return f"card-{lead['external_id']}"

    monkeypatch.setattr(trello, "create_card", fake_create)

    result = trello.push_pending_leads()
    assert result["criados"] >= 2
    assert result["falhas"] == 0
    assert {"trello-1", "trello-2"}.issubset(set(chamadas))

    # idempotência: tudo que estava pendente já foi marcado → 2ª rodada não recria
    again = trello.push_pending_leads()
    assert again["criados"] == 0

    # o vínculo foi gravado no banco
    card_id = get_connection().execute(
        "SELECT trello_card_id FROM leads_raw WHERE external_id = 'trello-1'"
    ).fetchone()[0]
    assert card_id == "card-trello-1"
