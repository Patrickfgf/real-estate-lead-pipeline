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


def test_ingest_reconstroi_leads_clean_com_score():
    """A camada curada é reconstruída na ingestão — fresca p/ analytics/dashboard."""
    lead = {**SAMPLE, "idEvento": "evt-clean-auto"}
    client.post("/webhook/wimoveis", params={"token": SECRET}, json=lead)

    row = get_connection().execute(
        "SELECT lead_score, lead_temperature FROM leads_clean "
        "WHERE source = 'wimoveis' AND external_id = ?",
        ["evt-clean-auto"],
    ).fetchone()
    assert row is not None  # leads_clean foi (re)construída automaticamente
    assert row[0] == 100  # anúncio (referencia) + telefone celular + e-mail
    assert row[1] == "Quente"


def test_payload_invalido_retorna_422():
    # falta idEvento e nome (campos obrigatórios)
    resp = client.post(
        "/webhook/wimoveis", params={"token": SECRET}, json={"foo": "bar"}
    )
    assert resp.status_code == 422


def test_payload_invalido_e_guardado_na_caixa_de_revisao():
    """Blindagem: payload recusado não some — vai para a dead-letter."""
    marcador = "marcador-dead-letter-xyz"
    resp = client.post(
        "/webhook/wimoveis",
        params={"token": SECRET},
        json={"sem_campos_obrigatorios": marcador},
    )
    assert resp.status_code == 422

    row = get_connection().execute(
        "SELECT source, error, raw_payload FROM leads_dead_letter WHERE raw_payload LIKE ?",
        [f"%{marcador}%"],
    ).fetchone()
    assert row is not None
    assert row[0] == "wimoveis"
    assert row[1]  # mensagem de erro preservada
    assert marcador in row[2]  # conteúdo cru preservado para reprocessar


def test_corpo_nao_json_nao_derruba_e_e_guardado():
    """Corpo que nem é JSON (scanner/lixo) não quebra o webhook — vai para revisão."""
    marcador = "isto-nao-e-json-zzz"
    resp = client.post(
        "/webhook/wimoveis",
        params={"token": SECRET},
        content=marcador.encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422
    row = get_connection().execute(
        "SELECT raw_payload FROM leads_dead_letter WHERE raw_payload LIKE ?",
        [f"%{marcador}%"],
    ).fetchone()
    assert row is not None and marcador in row[0]
