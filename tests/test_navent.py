"""Testes do cadastro de callback na Navent (parte pura, sem rede)."""
from src.navent import build_callback_config


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
