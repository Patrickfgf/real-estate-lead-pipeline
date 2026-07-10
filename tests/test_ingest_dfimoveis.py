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


def test_transaction_type_persistido_a_partir_do_payload():
    """Fase 1: o VrSync traz `transactionType` (SELL) — a ingestão persiste 'Compra'
    em leads_raw para a carga do Trello rotear o card (Locações vs Compra)."""
    lead = {**SAMPLE, "originLeadId": "df-tt-hook"}
    assert lead["transactionType"] == "SELL"  # o sample é de compra
    client.post("/webhook/dfimoveis", params={"token": SECRET}, json=lead)
    row = get_connection().execute(
        "SELECT transaction_type FROM leads_raw WHERE source='dfimoveis' AND external_id=?",
        ["df-tt-hook"],
    ).fetchone()
    assert row[0] == "Compra"


def test_transaction_type_ausente_nao_quebra_e_fica_nulo():
    """Caminho 'tipo ausente': um payload DFImóveis SEM `transactionType` não quebra a
    ingestão — o tipo resolve para None e é gravado NULL no PRÓPRIO INSERT; o webhook
    responde 200 (o lead nasce com a coluna nula, sem UPDATE posterior)."""
    lead = {**SAMPLE, "originLeadId": "df-sem-tt"}
    lead.pop("transactionType", None)  # o sample é de compra; aqui removemos o tipo
    resp = client.post("/webhook/dfimoveis", params={"token": SECRET}, json=lead)
    assert resp.status_code == 200

    row = get_connection().execute(
        "SELECT transaction_type FROM leads_raw WHERE source='dfimoveis' AND external_id=?",
        ["df-sem-tt"],
    ).fetchone()
    assert row[0] is None  # sem tipo derivado, a coluna nasce nula (não quebra)


def test_transaction_type_aluguel_pelo_client_listing_id_sem_transaction_type():
    """Empírico: os payloads reais do DFImóveis não trazem transactionType. O único
    sinal de aluguel é o clientListingId do CRM — 'al0001' resolve para 'Aluguel'."""
    lead = {**SAMPLE, "originLeadId": "df-al-crm", "clientListingId": "al0001"}
    lead.pop("transactionType", None)
    client.post("/webhook/dfimoveis", params={"token": SECRET}, json=lead)
    row = get_connection().execute(
        "SELECT transaction_type FROM leads_raw WHERE source='dfimoveis' AND external_id=?",
        ["df-al-crm"],
    ).fetchone()
    assert row[0] == "Aluguel"


def test_transaction_type_client_listing_id_de_casa_fica_nulo():
    """clientListingId 'CA0277' (CA = casa, tipo do imóvel) NÃO é aluguel: sem
    transactionType, o tipo fica indefinido (NULL) e o card cai no quadro fallback."""
    lead = {**SAMPLE, "originLeadId": "df-ca-crm", "clientListingId": "CA0277"}
    lead.pop("transactionType", None)
    client.post("/webhook/dfimoveis", params={"token": SECRET}, json=lead)
    row = get_connection().execute(
        "SELECT transaction_type FROM leads_raw WHERE source='dfimoveis' AND external_id=?",
        ["df-ca-crm"],
    ).fetchone()
    assert row[0] is None


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
