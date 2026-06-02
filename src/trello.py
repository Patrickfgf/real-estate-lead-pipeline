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
from src.db import carded_contacts, fetch_pending_leads, set_trello_card_id
from src.transform import compute_person_key

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


_SOURCE_DISPLAY = {"wimoveis": "Wimóveis", "dfimoveis": "DFImóveis"}


def _source_display(source: str) -> str:
    """Nome amigável do portal de origem para exibir no card."""
    return _SOURCE_DISPLAY.get(source, source)


def _card_name(lead: dict) -> str:
    ref = lead.get("listing_ref")
    base = f"🏠 {lead['name']}"
    return f"{base} — {ref}" if ref else base


def _card_desc(lead: dict) -> str:
    linhas = [
        f"**Lead {_source_display(lead['source'])}** · recebido {_fmt_dt(lead.get('received_at'))}",
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


def _source_label_id(source: str) -> str | None:
    """ID da etiqueta de origem (por portal) configurada no .env, se houver."""
    return {
        "wimoveis": settings.trello_label_wimoveis,
        "dfimoveis": settings.trello_label_dfimoveis,
    }.get(source) or None


def create_card(lead: dict) -> str:
    """Cria o card no Trello e devolve o id. Lança em erro de HTTP."""
    if not settings.trello_list_id:
        raise RuntimeError("TRELLO_LIST_ID não configurado no .env")
    # idLabels precisa ir na query string; no corpo (data) o Trello ignora.
    params = {**_auth(), "idList": settings.trello_list_id}
    label_id = _source_label_id(lead["source"])
    if label_id:
        params["idLabels"] = label_id
    resp = requests.post(
        f"{_TRELLO_API}/cards",
        params=params,
        data={"name": _card_name(lead), "desc": _card_desc(lead), "pos": "top"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def add_comment(card_id: str, text: str) -> None:
    """Adiciona um comentário ao card (usado ao consolidar leads duplicados)."""
    resp = requests.post(
        f"{_TRELLO_API}/cards/{card_id}/actions/comments",
        params={**_auth(), "text": text},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()


def add_label(card_id: str, label_id: str) -> None:
    """Adiciona uma etiqueta de origem ao card (idempotente do lado do Trello)."""
    resp = requests.post(
        f"{_TRELLO_API}/cards/{card_id}/idLabels",
        params={**_auth(), "value": label_id},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()


def _dup_comment(lead: dict) -> str:
    """Texto anexado ao card quando a mesma pessoa entra de novo (outro portal)."""
    return "\n".join(
        [
            f"🔁 **Mesma pessoa também entrou via {_source_display(lead['source'])}** "
            f"em {_fmt_dt(lead.get('received_at'))} — possível interesse mais quente.",
            "",
            f"💬 {lead.get('message') or '—'}",
            "",
            f"jare-ext:{lead['source']}:{lead['external_id']}",
        ]
    )


def _link_to_existing_card(card_id: str, lead: dict) -> None:
    """Consolida um lead duplicado no card já existente (comentário + etiqueta).

    Best-effort: o vínculo no banco já foi gravado; comentário/etiqueta são extras
    e uma falha aqui não deve interromper a carga dos demais leads.
    """
    try:
        add_comment(card_id, _dup_comment(lead))
        label_id = _source_label_id(lead["source"])
        if label_id:
            add_label(card_id, label_id)  # mostra que o card veio de 2 portais
    except Exception as exc:  # noqa: BLE001 — vínculo já feito; isto é enriquecimento
        print(f"[trello] vínculo ok, anexo falhou ({card_id}): {exc}", file=sys.stderr)


def _carded_person_map() -> dict:
    """Mapa `chave-da-pessoa -> card_id` dos leads que já têm card."""
    mapa: dict[str, str] = {}
    for c in carded_contacts():
        chave = compute_person_key(c.get("phone"), c.get("email"))
        if chave and chave not in mapa:
            mapa[chave] = c["trello_card_id"]
    return mapa


def push_pending_leads(limit: int = 50) -> dict:
    """Envia ao Trello os leads pendentes. Best-effort: falha em um não para os outros.

    Dedup de identidade: se um lead pendente é a MESMA pessoa (telefone/e-mail) de
    alguém que já tem card — inclusive de OUTRO portal — não cria um 2º card.
    Vincula ao card existente e anexa um comentário com a info do novo portal
    ("uma pessoa, um card"). Processa do mais antigo pro mais novo, então o
    primeiro lead da pessoa é o dono do card.
    """
    pendentes = fetch_pending_leads(limit)
    por_pessoa = _carded_person_map()
    criados = vinculados = falhas = 0
    for lead in pendentes:
        try:
            chave = compute_person_key(lead.get("phone"), lead.get("email"))
            if chave and chave in por_pessoa:
                card_id = por_pessoa[chave]
                set_trello_card_id(lead["source"], lead["external_id"], card_id)
                _link_to_existing_card(card_id, lead)
                vinculados += 1
                continue
            card_id = create_card(lead)
            set_trello_card_id(lead["source"], lead["external_id"], card_id)
            if chave:
                por_pessoa[chave] = card_id
            criados += 1
        except Exception as exc:  # noqa: BLE001 — best-effort, segue para o próximo
            falhas += 1
            print(f"[trello] falha no lead {lead['external_id']}: {exc}", file=sys.stderr)
    return {
        "pendentes": len(pendentes),
        "criados": criados,
        "vinculados": vinculados,
        "falhas": falhas,
    }


# ----------------------------------------------------------------------------
# Setup do quadro (infra como código): garante quadro + listas + etiquetas
# ----------------------------------------------------------------------------
WORKSPACE_NAME = "Leads Imobiliaria"
BOARD_NAME = "Leads — Imobiliaria"
PIPELINE = [
    "📥 Novos leads",
    "📞 Em contato",
    "🏠 Visita agendada",
    "💰 Proposta",
    "✅ Fechado (ganho)",
    "❌ Perdido",
]
SOURCE_LABELS = {"Wimóveis": "blue", "DFImóveis": "green"}


def _find_workspace_id(name: str) -> str | None:
    resp = requests.get(
        f"{_TRELLO_API}/members/me/organizations",
        params={**_auth(), "fields": "name,displayName"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    alvo = name.strip().lower()
    for org in resp.json():
        nomes = {org.get("displayName", "").strip().lower(), org.get("name", "").strip().lower()}
        if alvo in nomes:
            return org["id"]
    return None


def _ensure_board(workspace_id: str) -> tuple[str, bool]:
    resp = requests.get(
        f"{_TRELLO_API}/members/me/boards",
        params={**_auth(), "fields": "name,idOrganization,closed"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    for b in resp.json():
        if b.get("idOrganization") == workspace_id and b["name"] == BOARD_NAME and not b.get("closed"):
            return b["id"], False
    novo = requests.post(
        f"{_TRELLO_API}/boards",
        params={**_auth(), "name": BOARD_NAME, "idOrganization": workspace_id, "defaultLists": "false"},
        timeout=_TIMEOUT,
    )
    novo.raise_for_status()
    return novo.json()["id"], True


def _ensure_lists(board_id: str) -> dict:
    atuais = requests.get(
        f"{_TRELLO_API}/boards/{board_id}/lists",
        params={**_auth(), "fields": "name"},
        timeout=_TIMEOUT,
    )
    atuais.raise_for_status()
    por_nome = {item["name"]: item["id"] for item in atuais.json()}
    ids = {}
    for nome in PIPELINE:
        if nome in por_nome:
            ids[nome] = por_nome[nome]
            continue
        nova = requests.post(
            f"{_TRELLO_API}/lists",
            params={**_auth(), "name": nome, "idBoard": board_id, "pos": "bottom"},
            timeout=_TIMEOUT,
        )
        nova.raise_for_status()
        ids[nome] = nova.json()["id"]
    return ids


def _ensure_labels(board_id: str) -> dict:
    atuais = requests.get(
        f"{_TRELLO_API}/boards/{board_id}/labels",
        params={**_auth(), "fields": "name,color"},
        timeout=_TIMEOUT,
    )
    atuais.raise_for_status()
    por_nome = {item["name"]: item["id"] for item in atuais.json() if item.get("name")}
    ids = {}
    for nome, cor in SOURCE_LABELS.items():
        if nome in por_nome:
            ids[nome] = por_nome[nome]
            continue
        nova = requests.post(
            f"{_TRELLO_API}/labels",
            params={**_auth(), "name": nome, "color": cor, "idBoard": board_id},
            timeout=_TIMEOUT,
        )
        nova.raise_for_status()
        ids[nome] = nova.json()["id"]
    return ids


def setup_board() -> dict:
    """Idempotente: garante o quadro, as listas do funil e as etiquetas de origem."""
    workspace_id = _find_workspace_id(WORKSPACE_NAME)
    if not workspace_id:
        raise RuntimeError(f"Area de trabalho '{WORKSPACE_NAME}' nao encontrada no Trello")
    board_id, criado = _ensure_board(workspace_id)
    return {
        "board_id": board_id,
        "board_criado": criado,
        "listas": _ensure_lists(board_id),
        "labels": _ensure_labels(board_id),
    }


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


def _cli_setup() -> None:
    r = setup_board()
    estado = "[criado]" if r["board_criado"] else "[ja existia]"
    print(f"Board: {BOARD_NAME} (id={r['board_id']}) {estado}")
    print("\nListas do funil:")
    for nome, lid in r["listas"].items():
        print(f"   {nome}  ->  {lid}")
    print("\nEtiquetas de origem:")
    for nome, lid in r["labels"].items():
        print(f"   {nome}  ->  {lid}")
    print("\n>>> Coloque no .env:")
    print(f"   TRELLO_LIST_ID={r['listas'].get('📥 Novos leads', '')}")
    print(f"   TRELLO_LABEL_WIMOVEIS={r['labels'].get('Wimóveis', '')}")


if __name__ == "__main__":
    # No console do Windows (cp1252) caracteres fora do mapa quebram o print;
    # 'replace' troca por '?' em vez de derrubar o comando.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    comandos = {"check": _cli_check, "lists": _cli_lists, "push": _cli_push, "setup": _cli_setup}
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in comandos:
        print(f"uso: python -m src.trello [{' | '.join(comandos)}]", file=sys.stderr)
        sys.exit(1)
    comandos[cmd]()
