"""Testes do roteamento por-quadro (Fase 2) — Compra vs Locação, dedup por-quadro.

Cada card vai para um de DOIS quadros Trello conforme o `transaction_type` do lead:
Compra → quadro de Compra, Aluguel → quadro de Locação, None/desconhecido → quadro
único (fallback, o atual via TRELLO_LIST_ID). As etiquetas (origem + temperatura) são
resolvidas SEMPRE no quadro de destino — um id de etiqueta do Trello pertence a um
quadro só; reusar id de outro quadro faz o `add_label` bater em id inexistente.

Tudo com mock de `requests` (mesmo padrão de `tests/test_trello.py`), sem tocar na
API real do Trello.
"""
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import src.trello as trello
from src.db import carded_contacts, insert_lead, set_transaction_type, set_trello_card_id
from src.models import Lead


def _fake(**over):
    """Settings falsas com TODOS os campos do Trello (quadro único + 2 quadros).

    Convenção dos ids nos testes: sufixo `-FB` = quadro único (fallback),
    `-C` = quadro de Compra, `-A` = quadro de Locação/Aluguel.
    """
    base = SimpleNamespace(
        trello_api_key="K", trello_api_token="T",
        # Quadro único (fallback — estado atual de produção)
        trello_list_id="LIST-FALLBACK",
        trello_label_wimoveis="W-FB", trello_label_dfimoveis="D-FB",
        trello_label_quente="Q-FB", trello_label_morno="M-FB", trello_label_frio="F-FB",
        # Quadro de Compra
        trello_list_id_compra="LIST-COMPRA",
        trello_label_wimoveis_compra="W-C", trello_label_dfimoveis_compra="D-C",
        trello_label_quente_compra="Q-C", trello_label_morno_compra="M-C", trello_label_frio_compra="F-C",
        # Quadro de Locação (Aluguel)
        trello_list_id_aluguel="LIST-ALUGUEL",
        trello_label_wimoveis_aluguel="W-A", trello_label_dfimoveis_aluguel="D-A",
        trello_label_quente_aluguel="Q-A", trello_label_morno_aluguel="M-A", trello_label_frio_aluguel="F-A",
        # Nomes de quadro/área (usados pelo setup)
        trello_workspace_name="WS",
        trello_board_name_compra="Leads — Compra",
        trello_board_name_aluguel="Leads — Locação",
    )
    for chave, valor in over.items():  # overrides pontuais por teste
        setattr(base, chave, valor)
    return base


class _Resp:
    """Resposta HTTP falsa com um id fixo."""

    def __init__(self, card_id="card1"):
        self._id = card_id

    def raise_for_status(self):
        pass

    def json(self):
        return {"id": self._id}


# ---------------------------------------------------------------------------
# _list_id_for: roteamento da lista de entrada por transaction_type
# ---------------------------------------------------------------------------
def test_list_id_for_roteia_por_tipo(monkeypatch):
    monkeypatch.setattr(trello, "settings", _fake())
    assert trello._list_id_for("Compra") == "LIST-COMPRA"
    assert trello._list_id_for("Aluguel") == "LIST-ALUGUEL"
    assert trello._list_id_for(None) == "LIST-FALLBACK"          # tipo indefinido → fallback
    assert trello._list_id_for("Outro") == "LIST-FALLBACK"       # desconhecido → fallback


def test_list_id_for_tipo_sem_quadro_configurado_cai_no_fallback(monkeypatch):
    """Rollout seguro: sem o list id do quadro de Compra, um lead de Compra ainda
    cai no quadro único — o roteamento só ativa quando o list id do quadro existe."""
    monkeypatch.setattr(trello, "settings", _fake(trello_list_id_compra=""))
    assert trello._list_id_for("Compra") == "LIST-FALLBACK"
    assert trello._list_id_for("Aluguel") == "LIST-ALUGUEL"      # o de Aluguel segue roteando


# ---------------------------------------------------------------------------
# Etiquetas por-quadro: sempre o id do quadro de destino
# ---------------------------------------------------------------------------
def test_source_label_id_usa_etiqueta_do_quadro_de_destino(monkeypatch):
    monkeypatch.setattr(trello, "settings", _fake())
    assert trello._source_label_id("wimoveis", "Compra") == "W-C"
    assert trello._source_label_id("wimoveis", "Aluguel") == "W-A"
    assert trello._source_label_id("dfimoveis", "Compra") == "D-C"
    assert trello._source_label_id("wimoveis", None) == "W-FB"   # fallback = quadro único


def test_temp_label_id_usa_etiqueta_do_quadro_de_destino(monkeypatch):
    monkeypatch.setattr(trello, "settings", _fake())
    assert trello._temp_label_id("Quente", "Compra") == "Q-C"
    assert trello._temp_label_id("Quente", "Aluguel") == "Q-A"
    assert trello._temp_label_id("Frio", None) == "F-FB"         # fallback = quadro único


def test_source_label_id_quadro_sem_list_usa_fallback(monkeypatch):
    """Se o quadro do tipo não está configurado (sem list id), a etiqueta também
    vem do quadro único — casada com a lista, para o add_label não bater em outro quadro."""
    monkeypatch.setattr(trello, "settings", _fake(trello_list_id_compra=""))
    assert trello._source_label_id("wimoveis", "Compra") == "W-FB"
    assert trello._temp_label_id("Quente", "Compra") == "Q-FB"


# ---------------------------------------------------------------------------
# create_card: roteia idList + etiquetas para o quadro de destino
# ---------------------------------------------------------------------------
def _post_recorder(posts, card_id="card1"):
    def _fake_post(url, params=None, data=None, timeout=None):
        posts.append({"url": url, "params": params, "data": data})
        return _Resp(card_id)
    return _fake_post


def _lead(**over):
    base = {
        "source": "wimoveis", "external_id": "x", "name": "N", "email": "n@e.com",
        "phone": "(61) 99999-8888", "message": None, "listing_ref": "AP-1",
        "advertiser_code": None, "agency_code": None, "lead_date": None,
        "received_at": None, "transaction_type": "Compra",
    }
    base.update(over)
    return base


def test_create_card_roteia_compra_para_quadro_compra(monkeypatch):
    monkeypatch.setattr(trello, "settings", _fake())
    posts = []
    monkeypatch.setattr(trello.requests, "post", _post_recorder(posts, "cardC"))

    assert trello.create_card(_lead(transaction_type="Compra")) == "cardC"
    assert posts[0]["url"].endswith("/cards")
    assert posts[0]["params"]["idList"] == "LIST-COMPRA"
    assert posts[0]["params"]["idLabels"] == "W-C"       # origem do quadro Compra
    assert posts[1]["url"].endswith("/cards/cardC/idLabels")
    assert posts[1]["params"]["value"] == "Q-C"          # temperatura (lead quente) do quadro Compra


def test_create_card_roteia_aluguel_para_quadro_locacao(monkeypatch):
    monkeypatch.setattr(trello, "settings", _fake())
    posts = []
    monkeypatch.setattr(trello.requests, "post", _post_recorder(posts, "cardA"))

    assert trello.create_card(_lead(transaction_type="Aluguel")) == "cardA"
    assert posts[0]["params"]["idList"] == "LIST-ALUGUEL"
    assert posts[0]["params"]["idLabels"] == "W-A"       # origem do quadro Locação
    assert posts[1]["params"]["value"] == "Q-A"          # temperatura do quadro Locação


def test_create_card_tipo_none_cai_no_quadro_unico(monkeypatch):
    monkeypatch.setattr(trello, "settings", _fake())
    posts = []
    monkeypatch.setattr(trello.requests, "post", _post_recorder(posts, "cardN"))

    assert trello.create_card(_lead(transaction_type=None)) == "cardN"
    assert posts[0]["params"]["idList"] == "LIST-FALLBACK"
    assert posts[0]["params"]["idLabels"] == "W-FB"


def test_create_card_sem_env_novas_usa_quadro_unico(monkeypatch):
    """Estado atual de produção: só TRELLO_LIST_ID setado. Mesmo um lead TIPADO
    (Compra) cai no quadro único e usa a etiqueta de origem do quadro único — o
    roteamento fica inativo até o list id do quadro ser configurado (rollout seguro)."""
    fb = _fake(
        trello_list_id_compra="", trello_list_id_aluguel="",
        trello_label_wimoveis_compra="", trello_label_wimoveis_aluguel="",
        trello_label_quente_compra="", trello_label_quente_aluguel="",
    )
    monkeypatch.setattr(trello, "settings", fb)
    posts = []
    monkeypatch.setattr(trello.requests, "post", _post_recorder(posts, "cardFB"))

    assert trello.create_card(_lead(transaction_type="Compra")) == "cardFB"
    assert posts[0]["params"]["idList"] == "LIST-FALLBACK"
    assert posts[0]["params"]["idLabels"] == "W-FB"


# ---------------------------------------------------------------------------
# Dedup POR-QUADRO: chave composta (transaction_type, person_key)
# ---------------------------------------------------------------------------
def test_dedup_por_quadro_gera_card_em_cada_quadro(monkeypatch):
    """Mesma pessoa com um lead de Compra E um de Aluguel → 1 card em CADA quadro.

    Com OS DOIS quadros configurados (roteamento ativo), a dedup chaveia pelo quadro
    resolvido: Compra→LIST-COMPRA e Aluguel→LIST-ALUGUEL são list_ids distintos, então
    os dois leads criam cards distintos (um em cada quadro), sem vínculo entre eles.
    """
    monkeypatch.setattr(trello, "settings", _fake())  # 2 quadros configurados → roteamento ativo
    base = datetime(2026, 7, 1, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    # Telefone único na suíte (normaliza igual nos dois leads) — evita colidir com
    # fixtures de outros arquivos que já cardam a mesma pessoa noutro quadro.
    insert_lead(Lead(external_id="q2-compra", source="wimoveis", name="Duo Interesse",
                     email=None, phone="(61) 93000-9999", message="quero comprar",
                     raw_payload="{}", received_at=base))
    insert_lead(Lead(external_id="q2-aluguel", source="wimoveis", name="Duo Interesse",
                     email=None, phone="61930009999", message="quero alugar",
                     raw_payload="{}", received_at=base + timedelta(minutes=5)))
    set_transaction_type("wimoveis", "q2-compra", "Compra")
    set_transaction_type("wimoveis", "q2-aluguel", "Aluguel")

    criados, vinculos = [], []
    monkeypatch.setattr(
        trello, "create_card",
        lambda lead: criados.append((lead["external_id"], lead["transaction_type"])) or f"card-{lead['external_id']}",
    )
    monkeypatch.setattr(trello, "_link_to_existing_card",
                        lambda cid, lead: vinculos.append((cid, lead["external_id"])))

    trello.push_pending_leads()
    # os dois criaram card (quadros distintos); nenhum foi vinculado ao outro
    assert ("q2-compra", "Compra") in criados
    assert ("q2-aluguel", "Aluguel") in criados
    assert "q2-aluguel" not in [ext for _cid, ext in vinculos]


def test_dedup_quadro_unico_mesma_pessoa_tipos_diferentes_um_card(monkeypatch):
    """Regressão (config atual de produção): só TRELLO_LIST_ID, quadros dedicados
    vazios → roteamento inativo. A mesma pessoa com um lead de Compra E um de Aluguel
    cai no MESMO quadro fallback — logo deve virar UM card só, não dois.

    A chave de dedup passa a ser o QUADRO RESOLVIDO (`_list_id_for`), não o
    `transaction_type` cru: com roteamento inativo os dois tipos resolvem para o
    mesmo list_id → dedup. (Antes do fix a chave crua separava os tipos → 2 cards.)
    """
    # Só o quadro único configurado; os dedicados vazios (estado atual de produção).
    monkeypatch.setattr(
        trello, "settings", _fake(trello_list_id_compra="", trello_list_id_aluguel="")
    )

    base = datetime(2026, 7, 5, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    # Telefone único na suíte; normaliza igual nos dois leads (mesma pessoa).
    insert_lead(Lead(external_id="fb-compra", source="wimoveis", name="Um Card",
                     email=None, phone="(61) 92222-1111", message="quero comprar",
                     raw_payload="{}", received_at=base))
    insert_lead(Lead(external_id="fb-aluguel", source="wimoveis", name="Um Card",
                     email=None, phone="61922221111", message="quero alugar",
                     raw_payload="{}", received_at=base + timedelta(minutes=5)))
    set_transaction_type("wimoveis", "fb-compra", "Compra")
    set_transaction_type("wimoveis", "fb-aluguel", "Aluguel")

    criados, vinculos = [], []
    monkeypatch.setattr(trello, "create_card",
                        lambda lead: criados.append(lead["external_id"]) or f"card-{lead['external_id']}")
    monkeypatch.setattr(trello, "_link_to_existing_card",
                        lambda cid, lead: vinculos.append((cid, lead["external_id"])))

    trello.push_pending_leads()
    # Um card só: o 1º (Compra) cria, o 2º (Aluguel) vincula ao MESMO card.
    assert "fb-compra" in criados
    assert "fb-aluguel" not in criados            # não abriu 2º card (antes do fix abre → RED)
    assert ("card-fb-compra", "fb-aluguel") in vinculos


def test_dedup_mesma_operacao_dois_portais_um_card(monkeypatch):
    """Mesma pessoa + MESMA operação (Aluguel) nos 2 portais → 1 card + comentário.

    Comportamento atual preservado, agora por-quadro: como o tipo é o mesmo, a chave
    composta bate e o 2º lead é vinculado ao card do 1º (não abre um 2º card).
    """
    base = datetime(2026, 7, 2, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    insert_lead(Lead(external_id="op-w", source="wimoveis", name="Mesma Op",
                     email=None, phone="(61) 97777-6666", message="w",
                     raw_payload="{}", received_at=base))
    insert_lead(Lead(external_id="op-d", source="dfimoveis", name="Mesma Op",
                     email=None, phone="61977776666", message="d",
                     raw_payload="{}", received_at=base + timedelta(hours=1)))
    set_transaction_type("wimoveis", "op-w", "Aluguel")
    set_transaction_type("dfimoveis", "op-d", "Aluguel")

    criados, vinculos = [], []
    monkeypatch.setattr(trello, "create_card",
                        lambda lead: criados.append(lead["external_id"]) or f"card-{lead['external_id']}")
    monkeypatch.setattr(trello, "_link_to_existing_card",
                        lambda cid, lead: vinculos.append((cid, lead["external_id"])))

    trello.push_pending_leads()
    assert "op-w" in criados
    assert "op-d" not in criados
    assert ("card-op-w", "op-d") in vinculos


def test_none_nao_deduplica_contra_operacao_real(monkeypatch):
    """Edge: com roteamento ativo, um lead sem tipo (None) NÃO deve deduplicar contra
    uma operação real da mesma pessoa. None resolve para o quadro único (LIST-FALLBACK)
    e Aluguel para LIST-ALUGUEL — list_ids distintos → cria card próprio no quadro único
    em vez de se colar ao card de Aluguel."""
    monkeypatch.setattr(trello, "settings", _fake())  # roteamento ativo: None→fallback ≠ Aluguel
    base = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    insert_lead(Lead(external_id="real-al", source="wimoveis", name="Sem Tipo",
                     email=None, phone="(61) 96565-4343", message="alugar",
                     raw_payload="{}", received_at=base))
    insert_lead(Lead(external_id="none-op", source="wimoveis", name="Sem Tipo",
                     email=None, phone="61965654343", message="sem tipo",
                     raw_payload="{}", received_at=base + timedelta(minutes=5)))
    set_transaction_type("wimoveis", "real-al", "Aluguel")
    # none-op fica com transaction_type NULL (não seta) → chave (None, k)

    criados, vinculos = [], []
    monkeypatch.setattr(
        trello, "create_card",
        lambda lead: criados.append((lead["external_id"], lead["transaction_type"])) or f"card-{lead['external_id']}",
    )
    monkeypatch.setattr(trello, "_link_to_existing_card",
                        lambda cid, lead: vinculos.append((cid, lead["external_id"])))

    trello.push_pending_leads()
    assert ("real-al", "Aluguel") in criados
    assert ("none-op", None) in criados                 # criou o próprio card, não vinculou
    assert "none-op" not in [ext for _cid, ext in vinculos]


def test_dedup_concorrente_por_quadro_nao_duplica(monkeypatch):
    """Dois push concorrentes com a mesma pessoa+MESMA operação → 1 card só (o
    _push_lock + a chave composta serializam ler→criar→marcar por-quadro)."""
    base = datetime(2026, 7, 4, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    insert_lead(Lead(external_id="cc-w", source="wimoveis", name="Conc Quadro",
                     email=None, phone="(61) 94444-3232", message="w",
                     raw_payload="{}", received_at=base))
    insert_lead(Lead(external_id="cc-d", source="dfimoveis", name="Conc Quadro",
                     email=None, phone="61944443232", message="d",
                     raw_payload="{}", received_at=base + timedelta(minutes=1)))
    set_transaction_type("wimoveis", "cc-w", "Compra")
    set_transaction_type("dfimoveis", "cc-d", "Compra")

    criados = []
    monkeypatch.setattr(trello, "create_card",
                        lambda lead: criados.append(lead["external_id"]) or f"card-{lead['external_id']}")
    monkeypatch.setattr(trello, "_link_to_existing_card", lambda cid, lead: None)

    threads = [threading.Thread(target=trello.push_pending_leads) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert criados.count("cc-w") == 1
    assert "cc-d" not in criados


# ---------------------------------------------------------------------------
# carded_contacts inclui transaction_type (base da dedup por-quadro)
# ---------------------------------------------------------------------------
def test_carded_contacts_inclui_transaction_type():
    insert_lead(Lead(external_id="cc-tt", source="dfimoveis", name="CC",
                     email="cc-tt@e.com", phone=None, message=None,
                     raw_payload="{}", received_at=datetime.now(ZoneInfo("America/Sao_Paulo"))))
    set_transaction_type("dfimoveis", "cc-tt", "Compra")
    set_trello_card_id("dfimoveis", "cc-tt", "card-cc-tt")

    linha = {c["external_id"]: c for c in carded_contacts()}["cc-tt"]
    assert linha["transaction_type"] == "Compra"
    assert linha["trello_card_id"] == "card-cc-tt"


# ---------------------------------------------------------------------------
# setup_board: garante OS DOIS quadros (Compra e Locação) + listas + labels
# ---------------------------------------------------------------------------
def test_setup_board_cria_dois_quadros(monkeypatch):
    monkeypatch.setattr(trello, "settings", _fake())

    posts, counter = [], {"n": 0}

    class _R:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def _get(url, params=None, timeout=None):
        if url.endswith("/members/me/organizations"):
            return _R([{"id": "WS-ID", "name": "ws", "displayName": "WS"}])
        # quadros/listas/labels: vazio → tudo é criado do zero
        return _R([])

    def _post(url, params=None, data=None, timeout=None):
        counter["n"] += 1
        new_id = f"id-{counter['n']}"
        posts.append({"url": url, "params": params, "id": new_id})
        return _R({"id": new_id})

    monkeypatch.setattr(trello.requests, "get", _get)
    monkeypatch.setattr(trello.requests, "post", _post)

    result = trello.setup_board()

    # dois quadros distintos no resultado, com listas e labels garantidas em cada
    assert set(result.keys()) == {"Compra", "Aluguel"}
    board_posts = [p for p in posts if p["url"].endswith("/boards")]
    nomes = {p["params"]["name"] for p in board_posts}
    assert nomes == {"Leads — Compra", "Leads — Locação"}
    for chave in ("Compra", "Aluguel"):
        assert result[chave]["listas"], f"quadro {chave} deveria ter listas"
        assert result[chave]["labels"], f"quadro {chave} deveria ter labels"
