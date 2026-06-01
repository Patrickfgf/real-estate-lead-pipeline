"""Carga dos leads no Trello (Fase 4): cria um card por lead novo.

Idempotente: cada lead recebe um card no máximo uma vez. O vínculo fica em
`leads_raw.trello_card_id`; um marcador `jare-ext:<source>:<external_id>` também
vai na descrição do card, para rastreabilidade e dedup de segurança.

CLI (útil pra configurar e testar com credenciais reais):
    python -m src.trello check    # valida key/token e mostra o usuário
    python -m src.trello lists     # lista boards e listas com seus IDs
    python -m src.trello push      # envia ao Trello os leads pendentes no banco
"""
import sys

import requests

from src.config import settings
from src.db import fetch_pending_leads, set_trello_card_id

_TRELLO_API = "https://api.trello.com/1"
_TIMEOUT = 15


def _auth() -> dict:
    return {"key": settings.trello_api_key, "token": settings.trello_api_token}


def _fmt_dt(value) -> str:
    """Formata datetime do DuckDB em texto curto; tolera None/str."""
    if value is None:
        return "—"
    try:
        return value.strftime("%d/%m/%Y %H:%M")
    except AttributeError:
        return str(value)


def _card_name(lead: dict) -> str:
    ref = lead.get("listing_ref")
    base = f"🏠 {lead['name']}"
    return f"{base} — {ref}" if ref else base


def _card_desc(lead: dict) -> str:
    linhas = [
        f"**Lead Wimóveis** · recebido {_fmt_dt(lead.get('received_at'))}",
        "",
        f"- 👤 Nome: {lead.get('name') or '—'}",
        f"- 📧 Email: {lead.get('email') or '—'}",
        f"- 📱 Telefone: {lead.get('phone') or '—'}",
        f"- 🏢 Anúncio (ref): {lead.get('listing_ref') or '—'}",
        f"- 🧑‍💼 Anunciante: {lead.get('advertiser_code') or '—'}",
        f"- 🏬 Imobiliária: {lead.get('agency_code') or '—'}",
        f"- 🗓️ Data do lead: {_fmt_dt(lead.get('lead_date'))}",
        "",
        "💬 **Mensagem:**",
        lead.get("message") or "—",
        "",
        "---",
        f"jare-ext:{lead['source']}:{lead['external_id']}",
    ]
    return "\n".join(linhas)


def create_card(lead: dict) -> str:
    """Cria o card no Trello e devolve o id. Lança em erro de HTTP."""
    if not settings.trello_list_id:
        raise RuntimeError("TRELLO_LIST_ID não configurado no .env")
    resp = requests.post(
        f"{_TRELLO_API}/cards",
        params={**_auth(), "idList": settings.trello_list_id},
        data={"name": _card_name(lead), "desc": _card_desc(lead), "pos": "top"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def push_pending_leads(limit: int = 50) -> dict:
    """Envia ao Trello os leads pendentes. Best-effort: falha em um não para os outros."""
    pendentes = fetch_pending_leads(limit)
    criados, falhas = 0, 0
    for lead in pendentes:
        try:
            card_id = create_card(lead)
            set_trello_card_id(lead["source"], lead["external_id"], card_id)
            criados += 1
        except Exception as exc:  # noqa: BLE001 — best-effort, segue para o próximo
            falhas += 1
            print(f"[trello] falha no lead {lead['external_id']}: {exc}", file=sys.stderr)
    return {"pendentes": len(pendentes), "criados": criados, "falhas": falhas}


# ----------------------------------------------------------------------------
# CLI de configuração/teste
# ----------------------------------------------------------------------------
def _cli_check() -> None:
    resp = requests.get(f"{_TRELLO_API}/members/me", params=_auth(), timeout=_TIMEOUT)
    resp.raise_for_status()
    me = resp.json()
    print(f"OK - autenticado como @{me.get('username')} ({me.get('fullName')})")


def _cli_lists() -> None:
    boards = requests.get(
        f"{_TRELLO_API}/members/me/boards",
        params={**_auth(), "fields": "name"},
        timeout=_TIMEOUT,
    )
    boards.raise_for_status()
    for board in boards.json():
        print(f"\nBoard: {board['name']}  (id={board['id']})")
        lists = requests.get(
            f"{_TRELLO_API}/boards/{board['id']}/lists",
            params={**_auth(), "fields": "name"},
            timeout=_TIMEOUT,
        )
        lists.raise_for_status()
        for lst in lists.json():
            print(f"   - {lst['name']:<24} TRELLO_LIST_ID={lst['id']}")


def _cli_push() -> None:
    print(push_pending_leads())


if __name__ == "__main__":
    # No console do Windows (cp1252) caracteres fora do mapa quebram o print;
    # 'replace' troca por '?' em vez de derrubar o comando.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    comandos = {"check": _cli_check, "lists": _cli_lists, "push": _cli_push}
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in comandos:
        print(f"uso: python -m src.trello [{' | '.join(comandos)}]", file=sys.stderr)
        sys.exit(1)
    comandos[cmd]()
