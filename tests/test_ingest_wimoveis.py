"""Testes da ingestão do Wimóveis (rota do webhook + dedup)."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

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
    assert body["external_id"] == SAMPLE["ExternalId"]
    assert body["duplicate"] is False


def test_aceita_segredo_via_header():
    lead = {**SAMPLE, "ExternalId": "WIM-HEADER-TEST"}
    resp = client.post(
        "/webhook/wimoveis", headers={"x-webhook-token": SECRET}, json=lead
    )
    assert resp.status_code == 200
    assert resp.json()["duplicate"] is False


def test_dedup_mesmo_external_id():
    lead = {**SAMPLE, "ExternalId": "WIM-DEDUP-TEST"}
    r1 = client.post("/webhook/wimoveis", params={"token": SECRET}, json=lead)
    r2 = client.post("/webhook/wimoveis", params={"token": SECRET}, json=lead)
    assert r1.json()["duplicate"] is False
    assert r2.json()["duplicate"] is True


def test_payload_invalido_retorna_422():
    resp = client.post(
        "/webhook/wimoveis", params={"token": SECRET}, json={"foo": "bar"}
    )
    assert resp.status_code == 422
