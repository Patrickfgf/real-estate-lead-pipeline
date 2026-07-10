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
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"  # deep check: o /health tocou o DuckDB de verdade


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
    """Garante que o payload CONTACTO_MENSAJE foi normalizado e gravado corretamente."""
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
    # idnavplat (ID do aviso na Navent) é o vínculo com o imóvel — vira listing_ref.
    assert listing_ref == str(SAMPLE["idnavplat"])
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
    assert row[0] == 100  # idnavplat (60) + telefone celular (30) + e-mail (10)
    assert row[1] == "Quente"


def test_idnavplat_vira_listing_ref_quando_nao_ha_referencia():
    """Sem 'referencia' (corretora não associou avisos), o idnavplat — ID do aviso
    na Navent, sempre presente — é o vínculo com o imóvel e vira o listing_ref."""
    lead = {**SAMPLE, "idEvento": "evt-navplat"}
    assert "referencia" not in lead  # o payload real não traz referencia
    client.post("/webhook/wimoveis", params={"token": SECRET}, json=lead)
    row = get_connection().execute(
        "SELECT listing_ref FROM leads_raw WHERE source='wimoveis' AND external_id=?",
        ["evt-navplat"],
    ).fetchone()
    assert row[0] == str(SAMPLE["idnavplat"])


def test_referencia_associada_tem_precedencia_sobre_idnavplat():
    """Quando a corretora associou o aviso, vem 'referencia' (código legível do CRM):
    ela tem precedência sobre o idnavplat numérico como listing_ref."""
    lead = {**SAMPLE, "idEvento": "evt-ref", "referencia": "AP-ASA-SUL-2Q-123"}
    client.post("/webhook/wimoveis", params={"token": SECRET}, json=lead)
    row = get_connection().execute(
        "SELECT listing_ref FROM leads_raw WHERE source='wimoveis' AND external_id=?",
        ["evt-ref"],
    ).fetchone()
    assert row[0] == "AP-ASA-SUL-2Q-123"


def test_mensagem_e_limpa_do_spam_na_ingestao():
    """O boilerplate promocional do imovelweb não vai pro card (message limpa); o
    texto original continua preservado no raw_payload (auditoria)."""
    lead = {**SAMPLE, "idEvento": "evt-msg-limpa"}
    client.post("/webhook/wimoveis", params={"token": SECRET}, json=lead)
    row = get_connection().execute(
        "SELECT message, raw_payload FROM leads_raw "
        "WHERE source='wimoveis' AND external_id=?",
        ["evt-msg-limpa"],
    ).fetchone()
    message, raw_payload = row
    assert "¡" not in message and "panel/feedback" not in message
    assert message == "Olá! Quero ser contatado sobre este imóvel em venda que vi em Wimoveis."
    assert "panel/feedback" in raw_payload  # original preservado no cru


def test_transaction_type_resolvido_via_navent(monkeypatch):
    """Fase 1: o callback do Wimóveis NÃO traz o tipo — resolvemos via API Navent
    (fetch_operation) e persistimos em leads_raw. Aqui mockamos a resposta 'Aluguel'.

    O mock CAPTURA os argumentos recebidos (em vez de ignorá-los com `*a, **k`) e o
    teste confere que a ingestão passa (idnavplat, codigo_imobiliaria) NA ORDEM certa
    — uma troca de ordem no `fetch_operation(...)` do hook resolveria o tipo com os
    identificadores trocados e quebraria a resolução em silêncio."""
    import src.ingest_wimoveis as iw

    captura = {}

    def _fake_fetch(*args, **kwargs):
        captura["args"] = args
        captura["kwargs"] = kwargs
        return "Aluguel"

    monkeypatch.setattr(iw, "fetch_operation", _fake_fetch)
    lead = {**SAMPLE, "idEvento": "evt-tt-hook"}
    client.post("/webhook/wimoveis", params={"token": SECRET}, json=lead)
    row = get_connection().execute(
        "SELECT transaction_type FROM leads_raw WHERE source='wimoveis' AND external_id=?",
        ["evt-tt-hook"],
    ).fetchone()
    assert row[0] == "Aluguel"
    # Contrato do hook: idnavplat (ID do aviso, int) primeiro, codigo_imobiliaria
    # (str) depois — a ordem que `fetch_operation` espera. Assert posicional pega a troca.
    assert captura["kwargs"] == {}
    idnavplat, codigo_imobiliaria = captura["args"]
    assert idnavplat == SAMPLE["idnavplat"]
    assert codigo_imobiliaria == SAMPLE["codigoImobiliaria"]


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
