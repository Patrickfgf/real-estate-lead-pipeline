"""Testes da ingestão da DFImóveis (webhook VrSync + dedup + caixa de revisão)."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.db import get_connection
from src.main import app

client = TestClient(app)
SECRET = "segredo-de-teste"  # mesmo valor do conftest.py (DFIMOVEIS_WEBHOOK_SECRET)

_SAMPLE_PATH = Path(__file__).resolve().parents[1] / "samples" / "dfimoveis_lead.json"
SAMPLE = json.loads(_SAMPLE_PATH.read_text(encoding="utf-8"))


def test_rejeita_sem_segredo():
    resp = client.post("/webhook/dfimoveis", json=SAMPLE)
    assert resp.status_code == 401


def test_recebe_lead_valido():
    resp = client.post("/webhook/dfimoveis", params={"token": SECRET}, json=SAMPLE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "received"
    assert body["external_id"] == SAMPLE["originLeadId"]
    assert body["duplicate"] is False


def test_dedup_mesmo_origin_lead_id():
    lead = {**SAMPLE, "originLeadId": "df-dedup-test"}
    r1 = client.post("/webhook/dfimoveis", params={"token": SECRET}, json=lead)
    r2 = client.post("/webhook/dfimoveis", params={"token": SECRET}, json=lead)
    assert r1.json()["duplicate"] is False
    assert r2.json()["duplicate"] is True


def test_mapeamento_dos_campos_no_banco():
    """Garante que o payload VrSync foi normalizado e gravado corretamente."""
    lead = {**SAMPLE, "originLeadId": "df-map-test"}
    client.post("/webhook/dfimoveis", params={"token": SECRET}, json=lead)

    row = get_connection().execute(
        """
        SELECT source, name, email, phone, message, listing_ref, lead_date
        FROM leads_raw WHERE source = 'dfimoveis' AND external_id = ?
        """,
        ["df-map-test"],
    ).fetchone()

    assert row is not None
    source, name, email, phone, message, listing_ref, lead_date = row
    assert source == "dfimoveis"
    assert name == SAMPLE["name"]
    assert email == SAMPLE["email"]
    assert phone == SAMPLE["phoneNumber"]  # prioriza phoneNumber
    assert listing_ref == SAMPLE["clientListingId"]
    assert lead_date is not None and lead_date.tzinfo is not None  # timestamp parseado com fuso


def test_telefone_monta_de_ddd_e_phone_sem_phoneNumber():
    lead = {**SAMPLE, "originLeadId": "df-phone-test"}
    lead.pop("phoneNumber", None)
    client.post("/webhook/dfimoveis", params={"token": SECRET}, json=lead)
    row = get_connection().execute(
        "SELECT phone FROM leads_raw WHERE external_id = ?", ["df-phone-test"]
    ).fetchone()
    assert row[0] == f"{SAMPLE['ddd']}{SAMPLE['phone']}"


def test_payload_invalido_vai_para_caixa_de_revisao():
    """Blindagem: payload recusado não some — vai para a dead-letter."""
    marcador = "df-dead-letter-abc"
    resp = client.post(
        "/webhook/dfimoveis",
        params={"token": SECRET},
        json={"semCamposObrigatorios": marcador},
    )
    assert resp.status_code == 422
    row = get_connection().execute(
        "SELECT source, raw_payload FROM leads_dead_letter WHERE raw_payload LIKE ?",
        [f"%{marcador}%"],
    ).fetchone()
    assert row is not None
    assert row[0] == "dfimoveis"
    assert marcador in row[1]
