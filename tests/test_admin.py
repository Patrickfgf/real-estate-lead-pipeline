"""Testes dos endpoints administrativos (/admin/*) — operação em processo.

Eles existem porque o DuckDB é single-writer: com a API no ar, um segundo
processo (fly ssh console -C "python -m ...") não consegue abrir o banco.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import src.admin as admin
from src.db import insert_dead_letter
from src.main import app

client = TestClient(app)
TOKEN = "admin-de-teste"  # mesmo valor do conftest.py (ADMIN_TOKEN)


def test_rejeita_sem_token():
    assert client.get("/admin/dead-letter").status_code == 401
    assert client.post("/admin/trello/push").status_code == 401


def test_rejeita_token_errado():
    resp = client.get("/admin/dead-letter", headers={"X-Admin-Token": "errado"})
    assert resp.status_code == 401


def test_fail_closed_sem_admin_token_configurado(monkeypatch):
    """Sem ADMIN_TOKEN no ambiente, os endpoints ficam DESABILITADOS (503), não abertos."""
    sem_token = type("S", (), {"admin_token": ""})()
    monkeypatch.setattr(admin, "settings", sem_token)
    resp = client.get("/admin/dead-letter", headers={"X-Admin-Token": "qualquer"})
    assert resp.status_code == 503


def test_dead_letter_lista_entradas():
    insert_dead_letter(
        "dfimoveis",
        "erro de teste",
        json.dumps({"oi": 1}),
        datetime.now(ZoneInfo("America/Sao_Paulo")),
    )
    resp = client.get("/admin/dead-letter", headers={"X-Admin-Token": TOKEN})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(e["error"] == "erro de teste" for e in body["entradas"])


def test_trello_push_sem_credenciais_responde_503():
    # O conftest zera as credenciais do Trello — o endpoint deve avisar, não quebrar.
    resp = client.post("/admin/trello/push", headers={"X-Admin-Token": TOKEN})
    assert resp.status_code == 503
