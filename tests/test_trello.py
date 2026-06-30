"""Testes da carga no Trello (Fase 4) — sem tocar na API real (mock)."""
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import src.trello as trello
from src.db import get_connection, insert_lead
from src.models import Lead


def test_create_card_envia_idlabels_na_query(monkeypatch):
    """A etiqueta de origem deve ir em `params` (query), não no corpo —
    senão o Trello ignora e o card sai sem etiqueta."""
    fake = SimpleNamespace(
        trello_list_id="LIST", trello_label_wimoveis="LBL", trello_label_dfimoveis="LBL2",
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


def test_create_card_adiciona_etiqueta_de_temperatura(monkeypatch):
    """Lead quente (intenção num anúncio + telefone): a etiqueta de ORIGEM vai na
    criação do card e a de TEMPERATURA é adicionada pelo endpoint dedicado (não
    juntas por vírgula em idLabels, que o Trello recusa)."""
    fake = SimpleNamespace(
        trello_list_id="LIST",
        trello_label_wimoveis="LBL-W", trello_label_dfimoveis="LBL-D",
        trello_label_quente="LBL-HOT", trello_label_morno="LBL-WARM", trello_label_frio="LBL-COLD",
        trello_api_key="K", trello_api_token="T",
    )
    monkeypatch.setattr(trello, "settings", fake)

    posts = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "card1"}

    def _fake_post(url, params=None, data=None, timeout=None):
        posts.append({"url": url, "params": params})
        return _Resp()

    monkeypatch.setattr(trello.requests, "post", _fake_post)

    lead = {
        "source": "wimoveis", "external_id": "x", "name": "N", "email": "n@email.com",
        "phone": "(61) 99999-8888", "message": None, "listing_ref": "AP-1",
        "advertiser_code": None, "agency_code": None, "lead_date": None, "received_at": None,
    }
    assert trello.create_card(lead) == "card1"

    # 1º POST: cria o card já com a etiqueta de ORIGEM
    assert posts[0]["url"].endswith("/cards")
    assert posts[0]["params"]["idLabels"] == "LBL-W"
    # 2º POST: adiciona a etiqueta de TEMPERATURA (lead quente) pelo endpoint dedicado
    assert posts[1]["url"].endswith("/cards/card1/idLabels")
    assert posts[1]["params"]["value"] == "LBL-HOT"


def _temp_fake_settings():
    return SimpleNamespace(
        trello_api_key="K", trello_api_token="T",
        trello_label_quente="HOT", trello_label_morno="WARM", trello_label_frio="COLD",
    )


def test_sync_temperatura_sobe_de_frio_para_quente(monkeypatch):
    """Pessoa entrou Frio (só e-mail) e reentra Quente (referencia um anúncio):
    o card sobe para Quente e a etiqueta Frio é removida."""
    monkeypatch.setattr(trello, "settings", _temp_fake_settings())
    monkeypatch.setattr(trello, "_card_label_ids", lambda cid: ["COLD", "LBL-ORIGEM"])
    add, rem = [], []
    monkeypatch.setattr(trello, "add_label", lambda cid, lid: add.append(lid))
    monkeypatch.setattr(trello, "remove_label", lambda cid, lid: rem.append(lid))

    lead = {"source": "wimoveis", "phone": "(61) 99999-8888",
            "email": "x@e.com", "listing_ref": "AP-1"}  # -> Quente
    trello._sync_temperature_label("card1", lead)
    assert "HOT" in add      # subiu para Quente
    assert "COLD" in rem     # removeu Frio
    assert "LBL-ORIGEM" not in rem  # não mexe na etiqueta de origem


def test_sync_temperatura_nao_rebaixa(monkeypatch):
    """Card já Quente; lead reentra Frio (só e-mail) — não rebaixa nem mexe nada."""
    monkeypatch.setattr(trello, "settings", _temp_fake_settings())
    monkeypatch.setattr(trello, "_card_label_ids", lambda cid: ["HOT"])
    add, rem = [], []
    monkeypatch.setattr(trello, "add_label", lambda cid, lid: add.append(lid))
    monkeypatch.setattr(trello, "remove_label", lambda cid, lid: rem.append(lid))

    lead = {"source": "wimoveis", "phone": None, "email": "x@e.com", "listing_ref": None}  # -> Frio
    trello._sync_temperature_label("card1", lead)
    assert add == [] and rem == []


def _make_lead(ext: str) -> Lead:
    # Contato ÚNICO por ext (e-mail derivado) — senão a dedup de identidade veria
    # os dois leads como a mesma pessoa e criaria só um card.
    return Lead(
        external_id=ext,
        source="wimoveis",
        name="Fulano de Tal",
        email=f"{ext}@email.com",
        phone=None,
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


def test_card_desc_mostra_link_do_anuncio_wimoveis():
    """Quando o listing_ref é o idnavplat (numérico), o card traz o link clicável do
    anúncio no Wimóveis em vez do número cru."""
    lead = {
        "source": "wimoveis", "external_id": "1", "name": "F", "email": None,
        "phone": None, "message": None, "listing_ref": "3026198578",
        "advertiser_code": None, "agency_code": None, "lead_date": None, "received_at": None,
    }
    desc = trello._card_desc(lead)
    assert "https://www.wimoveis.com.br/propriedades/imovel-3026198578.html" in desc


def test_card_desc_mostra_portal_de_origem():
    """O card deve indicar o portal certo (Wimóveis vs DFImóveis)."""
    base = {
        "external_id": "1", "name": "F", "email": None, "phone": None,
        "message": None, "listing_ref": None, "advertiser_code": None,
        "agency_code": None, "lead_date": None, "received_at": None,
    }
    assert "Lead Wimóveis" in trello._card_desc({**base, "source": "wimoveis"})
    assert "Lead DFImóveis" in trello._card_desc({**base, "source": "dfimoveis"})


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


def test_dup_comment_menciona_portal_mensagem_e_marcador():
    lead = {"source": "dfimoveis", "external_id": "x9", "received_at": None,
            "message": "quero visitar"}
    txt = trello._dup_comment(lead)
    assert "DFImóveis" in txt
    assert "quero visitar" in txt
    assert "jare-ext:dfimoveis:x9" in txt


def test_push_pending_serializa_sob_concorrencia(monkeypatch):
    """Dois push_pending_leads concorrentes (rajada de leads da mesma pessoa) NÃO
    criam card duplicado: o _push_lock serializa ler→criar→marcar, então o segundo
    lead é vinculado ao card do primeiro em vez de abrir um 2º — mesmo sob threads."""
    base = datetime(2026, 6, 2, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    # mesma pessoa nos dois portais (o telefone normaliza para o mesmo E.164)
    insert_lead(Lead(external_id="conc-w", source="wimoveis", name="Bia Sá",
                     email=None, phone="(61) 96666-4321", message="w",
                     raw_payload="{}", received_at=base))
    insert_lead(Lead(external_id="conc-d", source="dfimoveis", name="Bia Sá",
                     email=None, phone="61966664321", message="d",
                     raw_payload="{}", received_at=base + timedelta(minutes=1)))

    criados = []

    def slow_create(lead):
        criados.append(lead["external_id"])
        time.sleep(0.02)  # alarga a janela: sem o lock, os dois threads colidiriam
        return f"card-{lead['external_id']}"

    monkeypatch.setattr(trello, "create_card", slow_create)
    monkeypatch.setattr(trello, "_link_to_existing_card", lambda cid, lead: None)

    threads = [threading.Thread(target=trello.push_pending_leads) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # uma pessoa, um card: conc-w criado exatamente 1x; conc-d nunca cria card (vincula)
    assert criados.count("conc-w") == 1
    assert "conc-d" not in criados


def test_push_nao_duplica_card_para_mesma_pessoa_entre_portais(monkeypatch):
    """Mesma pessoa (mesmo telefone) entrando nos dois portais → UM card só.

    O lead mais antigo cria o card; o duplicado é vinculado ao mesmo card e
    recebe um comentário, sem gerar um 2º card.
    """
    base = datetime(2026, 6, 2, 8, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    # Telefone distinto de qualquer outro usado na suíte; normaliza igual nos dois.
    insert_lead(Lead(external_id="dup-w", source="wimoveis", name="Ana Lima",
                     email=None, phone="(61) 95555-1234", message="msg wimoveis",
                     raw_payload="{}", received_at=base))
    insert_lead(Lead(external_id="dup-d", source="dfimoveis", name="Ana Lima",
                     email=None, phone="61955551234", message="msg dfimoveis",
                     raw_payload="{}", received_at=base + timedelta(hours=1)))

    criados, vinculos = [], []

    def fake_create(lead):
        criados.append(lead["external_id"])
        return f"card-{lead['external_id']}"

    def fake_link(card_id, lead):
        vinculos.append((card_id, lead["source"], lead["external_id"]))

    monkeypatch.setattr(trello, "create_card", fake_create)
    monkeypatch.setattr(trello, "_link_to_existing_card", fake_link)

    trello.push_pending_leads()

    # dup-w (mais antigo) criou o card; dup-d foi vinculado, NÃO criou 2º card
    assert "dup-w" in criados
    assert "dup-d" not in criados
    assert ("card-dup-w", "dfimoveis", "dup-d") in vinculos

    # ambos apontam para o MESMO card no banco
    con = get_connection()
    cw = con.execute("SELECT trello_card_id FROM leads_raw WHERE external_id = 'dup-w'").fetchone()[0]
    cd = con.execute("SELECT trello_card_id FROM leads_raw WHERE external_id = 'dup-d'").fetchone()[0]
    assert cw == "card-dup-w" and cd == "card-dup-w"
