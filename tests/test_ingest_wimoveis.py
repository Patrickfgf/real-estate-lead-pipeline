"""Testes da ingestão do Wimóveis (callback CONTACTO da Navent + dedup)."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.db import get_connection
from src.main import app

client = TestClient(app)
SECRET = "segredo-de-teste"  # mesmo valor definido no conftest.py

_SAMPLE_PATH = Path(__file__).resolve().parents[1] / "samples" / "wimoveis_lead.json"
SAMPLE = json.loads(_SAMPLE_PATH.read_text(encoding="utf-8"))


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_rejeita_sem_segredo():
    resp = client.post("/webhook/wimoveis", json=SAMPLE)
    assert resp.status_code == 401


def test_recebe_lead_valido():
    resp = client.post("/webhook/wimoveis", params={"token": SECRET}, json=SAMPLE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "received"
    assert body["external_id"] == SAMPLE["idEvento"]
    assert body["duplicate"] is False


def test_aceita_segredo_via_header():
    lead = {**SAMPLE, "idEvento": "evt-header-test"}
    resp = client.post(
        "/webhook/wimoveis", headers={"x-webhook-token": SECRET}, json=lead
    )
    assert resp.status_code == 200
    assert resp.json()["duplicate"] is False


def test_dedup_mesmo_id_evento():
    lead = {**SAMPLE, "idEvento": "evt-dedup-test"}
    r1 = client.post("/webhook/wimoveis", params={"token": SECRET}, json=lead)
    r2 = client.post("/webhook/wimoveis", params={"token": SECRET}, json=lead)
    assert r1.json()["duplicate"] is False
    assert r2.json()["duplicate"] is True


def test_mapeamento_dos_campos_no_banco():
    """Garante que o payload CONTACTO foi normalizado e gravado corretamente."""
    lead = {**SAMPLE, "idEvento": "evt-map-test"}
    client.post("/webhook/wimoveis", params={"token": SECRET}, json=lead)

    row = get_connection().execute(
        """
        SELECT name, email, phone, message, listing_ref, advertiser_code,
               agency_code, lead_date
        FROM leads_raw WHERE source = 'wimoveis' AND external_id = ?
        """,
        ["evt-map-test"],
    ).fetchone()

    assert row is not None
    name, email, phone, message, listing_ref, advertiser, agency, lead_date = row
    assert name == SAMPLE["nome"]
    assert email == SAMPLE["email"]
    assert phone == SAMPLE["telefone"]
    assert listing_ref == SAMPLE["referencia"]
    assert advertiser == SAMPLE["codigoDoAnunciante"]
    assert agency == SAMPLE["codigoImobiliaria"]
    assert lead_date is not None and lead_date.tzinfo is not None  # dataRegistro parseado com fuso


def test_payload_invalido_retorna_422():
    # falta idEvento e nome (campos obrigatórios)
    resp = client.post(
        "/webhook/wimoveis", params={"token": SECRET}, json={"foo": "bar"}
    )
    assert resp.status_code == 422
