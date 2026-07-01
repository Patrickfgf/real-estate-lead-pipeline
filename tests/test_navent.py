"""Testes do cadastro de callback na Navent (parte pura, sem rede)."""
import json
import types

import pytest

from src import navent
from src.navent import build_callback_config


class _Resp:
    """Resposta HTTP falsa o suficiente pra `cmd_register` (sem rede)."""

    def __init__(self, status_code, json_data=None, text=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json = json_data
        self.text = text if text is not None else (json.dumps(json_data) if json_data is not None else "")

    def json(self):
        if self._json is None:
            raise ValueError("sem corpo JSON")
        return self._json


def _fake_settings():
    return types.SimpleNamespace(
        navent_token="tok",
        navent_base_url="https://api.test",
        webhook_public_url="https://novo.fly.dev",
        wimoveis_webhook_secret="seg-novo",
    )


def test_monta_url_juntando_rota_do_webhook():
    cfg = build_callback_config("https://tunel.exemplo.com", "segredo123")
    assert cfg["url"] == "https://tunel.exemplo.com/webhook/wimoveis"


def test_remove_barra_final_da_url_publica():
    cfg = build_callback_config("https://tunel.exemplo.com/", "segredo123")
    assert cfg["url"] == "https://tunel.exemplo.com/webhook/wimoveis"  # sem barra dupla


def test_header_e_segredo_que_a_navent_reenvia():
    cfg = build_callback_config("https://x.com", "meu-segredo")
    # Casa com _check_secret do ingest (header x-webhook-token).
    assert cfg["authorizationHeaderKey"] == "x-webhook-token"
    assert cfg["authorizationHeaderValue"] == "meu-segredo"


# --- Read-modify-write: re-cadastrar NÃO pode apagar a config existente -------
# A Navent guarda no callback campos que NÓS não montamos (ex.: as inscrições de
# evento e o idioma do corpo). O PUT é full-replace, então `register` precisa
# partir da config atual (GET) e sobrescrever só os 3 campos que mudam.
def test_preserva_campos_existentes_ao_reescrever():
    existing = {
        "url": "https://app-antigo.fly.dev/webhook/wimoveis",
        "authorizationHeaderKey": "x-webhook-token",
        "authorizationHeaderValue": "segredo-antigo",
        "subscriptions": ["CONTACTO", "CONTACTO_MENSAJE"],
        "lenguajeCallbackBody": "PT",
    }
    cfg = build_callback_config("https://app-novo.fly.dev", "novo-segredo", existing=existing)
    # Os campos que NÓS não tocamos têm que sobreviver verbatim.
    assert cfg["subscriptions"] == ["CONTACTO", "CONTACTO_MENSAJE"]
    assert cfg["lenguajeCallbackBody"] == "PT"


def test_override_vence_os_valores_do_existing():
    existing = {
        "url": "https://app-antigo.fly.dev/webhook/wimoveis",
        "authorizationHeaderValue": "segredo-antigo",
        "subscriptions": ["CONTACTO"],
    }
    cfg = build_callback_config("https://app-novo.fly.dev", "novo-segredo", existing=existing)
    # url e segredo são os NOVOS (o ponto de re-cadastrar é justamente reapontar).
    assert cfg["url"] == "https://app-novo.fly.dev/webhook/wimoveis"
    assert cfg["authorizationHeaderValue"] == "novo-segredo"


def test_sem_existing_mantem_corpo_minimo():
    # Compatibilidade: sem config anterior (1º cadastro), o corpo é o mínimo de antes.
    cfg = build_callback_config("https://x.com", "s")
    assert set(cfg) == {"url", "authorizationHeaderKey", "authorizationHeaderValue"}


# --- Orquestração do register: GET (lê) -> merge -> PUT (escreve) -------------
def test_register_preserva_subscriptions_no_put(monkeypatch):
    """O PUT tem que carregar os campos que a Navent já tinha (lidos no GET)."""
    monkeypatch.setattr(navent, "settings", _fake_settings())
    current = {
        "url": "https://velho.fly.dev/webhook/wimoveis",
        "authorizationHeaderKey": "x-webhook-token",
        "authorizationHeaderValue": "seg-velho",
        "subscriptions": ["CONTACTO", "CONTACTO_MENSAJE"],
        "lenguajeCallbackBody": "PT",
    }
    monkeypatch.setattr(navent.requests, "get", lambda *a, **k: _Resp(200, current))
    captured = {}

    def fake_put(url, json=None, headers=None, timeout=None):
        captured["body"] = json
        return _Resp(200, {"ok": True})

    monkeypatch.setattr(navent.requests, "put", fake_put)

    navent.cmd_register()

    assert captured["body"]["subscriptions"] == ["CONTACTO", "CONTACTO_MENSAJE"]
    assert captured["body"]["lenguajeCallbackBody"] == "PT"
    # ...mas reaponta de fato pro app novo, com o segredo novo.
    assert captured["body"]["url"] == "https://novo.fly.dev/webhook/wimoveis"
    assert captured["body"]["authorizationHeaderValue"] == "seg-novo"


def test_register_recusa_put_se_nao_le_config_atual(monkeypatch):
    """GET falhando (≠404) => NÃO faz PUT, pra não apagar a config existente."""
    monkeypatch.setattr(navent, "settings", _fake_settings())
    monkeypatch.setattr(navent.requests, "get", lambda *a, **k: _Resp(500, text="erro interno"))
    chamado = {"put": False}

    def fake_put(*a, **k):
        chamado["put"] = True
        return _Resp(200, {})

    monkeypatch.setattr(navent.requests, "put", fake_put)

    with pytest.raises(SystemExit):
        navent.cmd_register()

    assert chamado["put"] is False


def test_register_dry_run_nao_faz_put(monkeypatch):
    """--dry-run lê a config (GET) mas nunca escreve (PUT)."""
    monkeypatch.setattr(navent, "settings", _fake_settings())
    current = {
        "url": "https://velho.fly.dev/webhook/wimoveis",
        "authorizationHeaderKey": "x-webhook-token",
        "authorizationHeaderValue": "seg-velho",
        "subscriptions": ["CONTACTO"],
    }
    monkeypatch.setattr(navent.requests, "get", lambda *a, **k: _Resp(200, current))
    chamado = {"put": False}
    monkeypatch.setattr(navent.requests, "put", lambda *a, **k: chamado.__setitem__("put", True))

    navent.cmd_register(dry_run=True)

    assert chamado["put"] is False


def _raise_conn(*a, **k):
    raise navent.requests.ConnectionError("sem rede")


def test_register_aborta_em_erro_de_rede_sem_force(monkeypatch):
    """GET que LEVANTA (timeout/conexão) => aborta limpo, sem PUT."""
    monkeypatch.setattr(navent, "settings", _fake_settings())
    monkeypatch.setattr(navent.requests, "get", _raise_conn)
    chamado = {"put": False}
    monkeypatch.setattr(navent.requests, "put", lambda *a, **k: chamado.__setitem__("put", True))

    with pytest.raises(SystemExit):
        navent.cmd_register()

    assert chamado["put"] is False


def test_register_forca_corpo_minimo_em_erro_de_rede(monkeypatch):
    """--force segue com corpo mínimo mesmo quando o GET falha."""
    monkeypatch.setattr(navent, "settings", _fake_settings())
    monkeypatch.setattr(navent.requests, "get", _raise_conn)
    captured = {}

    def fake_put(url, json=None, headers=None, timeout=None):
        captured["body"] = json
        return _Resp(200, {"ok": True})

    monkeypatch.setattr(navent.requests, "put", fake_put)

    navent.cmd_register(force=True)

    assert "subscriptions" not in captured["body"]  # corpo mínimo (nada a preservar)
    assert captured["body"]["url"] == "https://novo.fly.dev/webhook/wimoveis"


def test_register_404_aborta_sem_force(monkeypatch):
    """404 pode ser 'sem callback' OU caminho errado — na dúvida, NÃO clobber."""
    monkeypatch.setattr(navent, "settings", _fake_settings())
    monkeypatch.setattr(navent.requests, "get", lambda *a, **k: _Resp(404, text="not found"))
    chamado = {"put": False}
    monkeypatch.setattr(navent.requests, "put", lambda *a, **k: chamado.__setitem__("put", True))

    with pytest.raises(SystemExit):
        navent.cmd_register()

    assert chamado["put"] is False


def test_register_404_com_force_faz_put_minimo(monkeypatch):
    """1º cadastro de verdade (404) é feito explicitamente com --force."""
    monkeypatch.setattr(navent, "settings", _fake_settings())
    monkeypatch.setattr(navent.requests, "get", lambda *a, **k: _Resp(404, text="not found"))
    captured = {}

    def fake_put(url, json=None, headers=None, timeout=None):
        captured["body"] = json
        return _Resp(200, {})

    monkeypatch.setattr(navent.requests, "put", fake_put)

    navent.cmd_register(force=True)

    assert captured["body"] == {
        "url": "https://novo.fly.dev/webhook/wimoveis",
        "authorizationHeaderKey": "x-webhook-token",
        "authorizationHeaderValue": "seg-novo",
    }


def test_register_recusa_dict_que_nao_parece_callback(monkeypatch):
    """Envelope inesperado ({'data': ...}) não é config — aborta, não PUTa envelope."""
    monkeypatch.setattr(navent, "settings", _fake_settings())
    monkeypatch.setattr(navent.requests, "get", lambda *a, **k: _Resp(200, {"data": {"url": "x"}}))
    chamado = {"put": False}
    monkeypatch.setattr(navent.requests, "put", lambda *a, **k: chamado.__setitem__("put", True))

    with pytest.raises(SystemExit):
        navent.cmd_register()

    assert chamado["put"] is False


def test_print_response_mascara_o_segredo(capsys):
    """O corpo do GET/response NÃO pode vazar authorizationHeaderValue em claro."""
    resp = _Resp(200, {
        "url": "https://x/webhook/wimoveis",
        "authorizationHeaderValue": "super-secreto-1234567890",
    })
    navent._print_response(resp)
    out = capsys.readouterr().out
    assert "super-secreto-1234567890" not in out
    assert "authorizationHeaderValue" in out  # o campo aparece, só que mascarado
