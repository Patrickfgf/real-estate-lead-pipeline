"""Testes do resolver do tipo de operação (Aluguel/Compra) via API Navent.

O Wimóveis (callback CONTACTO) NÃO manda o tipo de transação. A via do detalhe do
anúncio está fechada (o token de produção não tem permissão: o GET do anúncio
responde HTTP 500 "Access is denied"), mas o próprio `/online/resumo` já traz o
campo `titulo`, que indica a operação de forma inequívoca ("... à venda ...",
"... para aluguel ..."). Resolvemos com UM GET só, parseando o título.

É best-effort: qualquer falha/formato inesperado/sem credencial vira None (nunca
levanta), pra não derrubar a ingestão.

Padrão de mock igual ao test_navent.py: classe `_Resp` + monkeypatch de
`navent_listings.requests.get` (sem tocar a rede).
"""
import json
import logging
import types
from urllib.parse import parse_qs, urlparse

import pytest

from src import navent_listings
from src.navent_listings import _operacao_do_titulo, fetch_operation


class _Resp:
    """Resposta HTTP falsa o suficiente pra `fetch_operation` (sem rede)."""

    def __init__(self, status_code, json_data=None, text=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json = json_data
        self.text = text if text is not None else (json.dumps(json_data) if json_data is not None else "")

    def json(self):
        if self._json is None:
            raise ValueError("sem corpo JSON")
        return self._json


def _fake_settings(token="tok"):
    return types.SimpleNamespace(navent_token=token, navent_base_url="https://api.test")


def _resumo(titulo):
    """Resposta do /online/resumo: LISTA com 1 item contendo `titulo`."""
    return _Resp(200, [{"titulo": titulo}])


# --- função pura _operacao_do_titulo ---------------------------------------
@pytest.mark.parametrize(
    "titulo, esperado",
    [
        # Os 4 títulos REAIS retornados pela API em produção (4 anúncios).
        ("Casa com 3 quartos à venda, 250 m² por R$ 990.000 - ...", "Compra"),
        ("Casa para aluguel - no Setor Habitacional Jardim Botânico", "Aluguel"),
        ("Casa à venda - no Setor...", "Compra"),
        ("CASA PARA ALUGUEL CONDOMÍNIO ECOLÓGICO...", "Aluguel"),
        # Bordas: sinônimos, acento e caixa.
        ("Apartamento com 2 quartos para locação", "Aluguel"),  # 'locacao' (acento removido)
        ("Cobertura à VENDA no Lago Sul", "Compra"),             # uppercase + acento
        ("Casa temporada na praia", "Aluguel"),                  # 'temporada'
        ("Vende-se apartamento no Sudoeste", "Compra"),          # 'vende'
        # Ambíguo (casa venda E aluguel) e sem operação → None (não chuta).
        ("Casa à venda ou para aluguel", None),
        ("Apartamento 2 quartos Asa Sul", None),
        (None, None),
        ("", None),
    ],
)
def test_operacao_do_titulo(titulo, esperado):
    assert _operacao_do_titulo(titulo) == esperado


# --- caminho feliz: 1 GET só, lê o `titulo` do resumo ----------------------
def test_venda_vira_compra(monkeypatch):
    monkeypatch.setattr(navent_listings, "settings", _fake_settings())
    monkeypatch.setattr(
        navent_listings.requests, "get", lambda *a, **k: _resumo("Casa à venda - no Setor Sul")
    )
    assert fetch_operation(3000000001, "10000001") == "Compra"


def test_aluguel_vira_aluguel(monkeypatch):
    monkeypatch.setattr(navent_listings, "settings", _fake_settings())
    monkeypatch.setattr(
        navent_listings.requests, "get", lambda *a, **k: _resumo("Casa para aluguel - Jardim Botânico")
    )
    assert fetch_operation(3000000001, "10000001") == "Aluguel"


def test_resumo_com_envelope_content(monkeypatch):
    """O resumo pode vir embrulhado ({'content': [...]}); a extração `_itens` é tolerante."""
    monkeypatch.setattr(navent_listings, "settings", _fake_settings())
    resp = _Resp(200, {"content": [{"titulo": "Apartamento à venda no Sudoeste"}]})
    monkeypatch.setattr(navent_listings.requests, "get", lambda *a, **k: resp)
    assert fetch_operation(1, "1") == "Compra"


# --- caminhos best-effort → None -------------------------------------------
def test_resumo_vazio_retorna_none(monkeypatch):
    monkeypatch.setattr(navent_listings, "settings", _fake_settings())
    monkeypatch.setattr(navent_listings.requests, "get", lambda *a, **k: _Resp(200, []))
    assert fetch_operation(1, "1") is None


def test_item_sem_titulo_retorna_none(monkeypatch):
    monkeypatch.setattr(navent_listings, "settings", _fake_settings())
    monkeypatch.setattr(navent_listings.requests, "get", lambda *a, **k: _Resp(200, [{"outroCampo": 1}]))
    assert fetch_operation(1, "1") is None


def test_titulo_sem_operacao_retorna_none(monkeypatch):
    monkeypatch.setattr(navent_listings, "settings", _fake_settings())
    monkeypatch.setattr(
        navent_listings.requests, "get", lambda *a, **k: _resumo("Apartamento 2 quartos Asa Sul")
    )
    assert fetch_operation(1, "1") is None


def test_formato_inesperado_retorna_none(monkeypatch):
    """Resposta sem lista de itens (formato que não conhecemos) → None, não levanta."""
    monkeypatch.setattr(navent_listings, "settings", _fake_settings())
    monkeypatch.setattr(navent_listings.requests, "get", lambda *a, **k: _Resp(200, {"algoInesperado": True}))
    assert fetch_operation(1, "1") is None


def test_http_erro_retorna_none_e_loga(monkeypatch, caplog):
    """HTTP não-ok (ex.: 500 'Access is denied') → None e loga um warning com o status."""
    monkeypatch.setattr(navent_listings, "settings", _fake_settings())
    monkeypatch.setattr(
        navent_listings.requests, "get", lambda *a, **k: _Resp(500, text="Access is denied")
    )
    with caplog.at_level(logging.WARNING, logger="jare.navent"):
        assert fetch_operation(1, "1") is None
    assert "500" in caplog.text


def test_excecao_de_rede_retorna_none(monkeypatch):
    """Qualquer exceção (timeout/conexão) é engolida (best-effort) → None, não levanta."""
    monkeypatch.setattr(navent_listings, "settings", _fake_settings())

    def _raise(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(navent_listings.requests, "get", _raise)
    assert fetch_operation(1, "1") is None


def test_sem_token_retorna_none_sem_tocar_a_rede(monkeypatch):
    """Guard no topo: sem NAVENT_TOKEN nem tenta o GET (protege os testes de ingestão)."""
    monkeypatch.setattr(navent_listings, "settings", _fake_settings(token=""))

    def _boom(*a, **k):
        raise AssertionError("não devia tocar a rede sem token")

    monkeypatch.setattr(navent_listings.requests, "get", _boom)
    assert fetch_operation(1, "1") is None


def test_sem_idnavplat_ou_imobiliaria_retorna_none(monkeypatch):
    monkeypatch.setattr(navent_listings, "settings", _fake_settings())

    def _boom(*a, **k):
        raise AssertionError("não devia tocar a rede sem os identificadores")

    monkeypatch.setattr(navent_listings.requests, "get", _boom)
    assert fetch_operation(None, "10000001") is None
    assert fetch_operation(3000000001, None) is None


# --- contrato da URL: 1 chamada só + path/param certos ---------------------
def test_url_do_resumo_e_uma_chamada_so(monkeypatch):
    """Captura a(s) URL(s) chamada(s) e confere o contrato do único GET (resumo):

    - path TEM que bater com /v1/imobiliarias/<cod>/anuncios/online/resumo;
    - o param TEM que se chamar exatamente `idAvisoNavplat` e trazer o idnavplat;
    - PROVA que há UMA chamada só (não faz mais o 2º GET do detalhe — via fechada).
    Um typo no nome do parâmetro/no path resolveria None em silêncio — este teste pega.
    """
    monkeypatch.setattr(navent_listings, "settings", _fake_settings())
    chamadas = []

    def _capture(url, *a, **k):
        chamadas.append(url)
        return _resumo("Casa à venda no Lago Norte")

    monkeypatch.setattr(navent_listings.requests, "get", _capture)
    assert fetch_operation(3000000001, "10000001") == "Compra"

    assert len(chamadas) == 1  # uma chamada só: nada de 2º GET do detalhe
    parsed = urlparse(chamadas[0])
    assert parsed.path == "/v1/imobiliarias/10000001/anuncios/online/resumo"
    # nome EXATO do parâmetro + valor = idnavplat (parse_qs pega o param, não um substring)
    assert parse_qs(parsed.query).get("idAvisoNavplat") == ["3000000001"]
