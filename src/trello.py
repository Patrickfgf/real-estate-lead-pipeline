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
import threading

import requests

from src.config import settings
from src.db import carded_contacts, fetch_pending_leads, set_trello_card_id
from src.transform import (
    compute_person_key,
    has_listing_intent,
    listing_url,
    normalize_email,
    normalize_phone,
    score_lead,
    score_to_temperature,
)

_TRELLO_API = "https://api.trello.com/1"
_TIMEOUT = 15

# Serializa a carga inteira. `push_pending_leads` é uma sequência ler→criar→marcar
# que dispara a cada webhook (via threadpool): sem este lock, dois leads quase
# simultâneos veem o mesmo pendente com card NULL e ambos criam card — furando a
# garantia "uma pessoa, um card" sob rajada. É um lock SEPARADO do `_lock` do DuckDB
# (db.py) para não bloquear os inserts. No volume do projeto o custo é nulo.
# NOTA (Fase 6/VPS): sob múltiplos workers/processos um lock de processo não basta —
# aí `create_card` precisa virar idempotente de verdade (buscar o card pelo marcador
# jare-ext via Trello search antes de criar).
_push_lock = threading.Lock()


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


def _anuncio_line(lead: dict) -> str:
    """Linha do anúncio no card: link clicável quando temos a URL pública do portal;
    senão o código de referência (ou — quando não há nenhum)."""
    # Precedência: a URL que o portal ENVIOU (DFImóveis, já validada na ingestão) vence
    # a que nós CONSTRUÍMOS do listing_ref (Wimóveis). Portais que não mandam URL nem
    # têm template continuam caindo na referência crua.
    url = lead.get("listing_url") or listing_url(lead.get("source", ""), lead.get("listing_ref"))
    if url:
        return f"- 🔗 Anúncio: {url}"
    return f"- 🏢 Anúncio (ref): {lead.get('listing_ref') or '—'}"


def _card_desc(lead: dict) -> str:
    linhas = [
        f"**Lead {_source_display(lead['source'])}** · recebido {_fmt_dt(lead.get('received_at'))}",
        "",
        f"- 👤 Nome: {lead.get('name') or '—'}",
        f"- 📧 Email: {lead.get('email') or '—'}",
        f"- 📱 Telefone: {lead.get('phone') or '—'}",
        _anuncio_line(lead),
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


# ---------------------------------------------------------------------------
# Roteamento por 2 quadros (Fase 2): Compra vs Locação
# ---------------------------------------------------------------------------
# `transaction_type` -> nomes dos atributos de `settings` do quadro DEDICADO. As
# etiquetas ficam JUNTO da lista de cada quadro porque um id de etiqueta do Trello
# pertence a UM quadro só: resolver lista e etiqueta pelo MESMO mapa garante que o
# add_label nunca use um id de outro quadro (que o Trello recusaria — card sem etiqueta).
_BOARD_ATTRS = {
    "Compra": {
        "list_id": "trello_list_id_compra",
        "wimoveis": "trello_label_wimoveis_compra",
        "dfimoveis": "trello_label_dfimoveis_compra",
        "Quente": "trello_label_quente_compra",
        "Morno": "trello_label_morno_compra",
        "Frio": "trello_label_frio_compra",
    },
    "Aluguel": {
        "list_id": "trello_list_id_aluguel",
        "wimoveis": "trello_label_wimoveis_aluguel",
        "dfimoveis": "trello_label_dfimoveis_aluguel",
        "Quente": "trello_label_quente_aluguel",
        "Morno": "trello_label_morno_aluguel",
        "Frio": "trello_label_frio_aluguel",
    },
}
# Quadro ÚNICO (fallback): tipo None/desconhecido, OU quadro dedicado ainda não
# configurado (list id vazio). Aponta para as env vars originais — preserva 100% o
# comportamento pré-Fase-2 enquanto os 2 quadros não estiverem no .env (rollout seguro).
_FALLBACK_ATTRS = {
    "list_id": "trello_list_id",
    "wimoveis": "trello_label_wimoveis",
    "dfimoveis": "trello_label_dfimoveis",
    "Quente": "trello_label_quente",
    "Morno": "trello_label_morno",
    "Frio": "trello_label_frio",
}


def _board_attrs(transaction_type: str | None) -> dict:
    """Atributos de `settings` do quadro de destino do lead — ÚNICO ponto de decisão
    do roteamento (lista e etiquetas saem sempre do MESMO quadro).

    Roteia por `transaction_type` só quando o quadro dedicado TEM list id configurada;
    sem ela (estado atual: só TRELLO_LIST_ID) ou tipo None/desconhecido → quadro único.
    """
    attrs = _BOARD_ATTRS.get(transaction_type)
    if attrs and getattr(settings, attrs["list_id"], ""):
        return attrs
    return _FALLBACK_ATTRS


def _list_id_for(transaction_type: str | None) -> str:
    """ID da lista de entrada do quadro de destino (dedicado ou fallback único)."""
    return getattr(settings, _board_attrs(transaction_type)["list_id"], "")


def _source_label_id(source: str, transaction_type: str | None = None) -> str | None:
    """ID da etiqueta de ORIGEM (portal) no quadro de destino do lead, se houver."""
    attr = _board_attrs(transaction_type).get(source)
    return (getattr(settings, attr, "") or None) if attr else None


def _lead_temperature(lead: dict) -> str:
    """Temperatura do lead (rubrica do cliente), computada on-the-fly na carga.

    Usa as MESMAS funções puras do `leads_clean` (telefone/e-mail/listing_ref do
    lead cru), então a etiqueta no card concorda com a coluna da camada curada —
    sem depender do rebuild em lote.
    """
    ph = normalize_phone(lead.get("phone"))
    em = normalize_email(lead.get("email"))
    score = score_lead(
        listing_intent=has_listing_intent(lead.get("listing_ref")),
        phone_valid=ph["phone_valid"],
        phone_is_mobile=ph["phone_is_mobile"],
        email_valid=em["email_valid"],
    )
    return score_to_temperature(score)


def _temp_label_id(temperature: str, transaction_type: str | None = None) -> str | None:
    """ID da etiqueta de TEMPERATURA no quadro de destino do lead, se houver."""
    attr = _board_attrs(transaction_type).get(temperature)
    return (getattr(settings, attr, "") or None) if attr else None


# Quão "quente" é cada faixa — para nunca rebaixar a etiqueta de um card na reentrada.
_TEMP_RANK = {"Frio": 1, "Morno": 2, "Quente": 3}


def create_card(lead: dict) -> str:
    """Cria o card no Trello e devolve o id. Lança em erro de HTTP.

    Roteia o card para o quadro do `transaction_type` (Compra vs Locação) via
    `_list_id_for`; tipo None/desconhecido ou quadro dedicado sem list id → quadro
    único (fallback). A etiqueta de ORIGEM (portal) vai já na criação (idLabels na
    query) e a de TEMPERATURA (Fase 3) num 2º passo — SEMPRE do quadro de destino:
    passar várias etiquetas por vírgula em `idLabels` não é confiável (a vírgula vira
    %2C e o Trello recusa o conjunto inteiro — o card sairia sem nenhuma etiqueta).
    """
    tt = lead.get("transaction_type")
    list_id = _list_id_for(tt)
    if not list_id:
        raise RuntimeError("TRELLO_LIST_ID não configurado no .env")
    # idLabels precisa ir na query string; no corpo (data) o Trello ignora.
    params = {**_auth(), "idList": list_id}
    source_label = _source_label_id(lead["source"], tt)
    if source_label:
        params["idLabels"] = source_label
    resp = requests.post(
        f"{_TRELLO_API}/cards",
        params=params,
        data={"name": _card_name(lead), "desc": _card_desc(lead), "pos": "top"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    card_id = resp.json()["id"]

    # Etiqueta de temperatura: best-effort. O card já existe; uma falha aqui não
    # pode derrubar a carga (senão o lead fica pendente e a próxima carga duplica).
    temp_label = _temp_label_id(_lead_temperature(lead), tt)
    if temp_label:
        try:
            add_label(card_id, temp_label)
        except Exception as exc:  # noqa: BLE001 — etiqueta é enriquecimento, card já criado
            print(f"[trello] card criado, etiqueta de temperatura falhou ({card_id}): {exc}", file=sys.stderr)
    return card_id


def add_comment(card_id: str, text: str) -> None:
    """Adiciona um comentário ao card (usado ao consolidar leads duplicados)."""
    resp = requests.post(
        f"{_TRELLO_API}/cards/{card_id}/actions/comments",
        params={**_auth(), "text": text},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()


def add_label(card_id: str, label_id: str) -> None:
    """Adiciona uma etiqueta ao card (idempotente do lado do Trello)."""
    resp = requests.post(
        f"{_TRELLO_API}/cards/{card_id}/idLabels",
        params={**_auth(), "value": label_id},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()


def remove_label(card_id: str, label_id: str) -> None:
    """Remove uma etiqueta do card."""
    resp = requests.delete(
        f"{_TRELLO_API}/cards/{card_id}/idLabels/{label_id}",
        params=_auth(),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()


def _card_label_ids(card_id: str) -> list:
    """IDs das etiquetas atualmente no card (lê o card completo)."""
    resp = requests.get(f"{_TRELLO_API}/cards/{card_id}", params=_auth(), timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("idLabels") or []


def _sync_temperature_label(card_id: str, lead: dict) -> None:
    """Garante que o card carregue a temperatura MAIS QUENTE entre a atual e a do
    lead que reentra — uma única etiqueta de temperatura, nunca rebaixando.

    Ex.: pessoa entrou só com e-mail (Frio) e depois referencia um anúncio (Quente):
    o card sobe para Quente. O contrário (reentrar mais frio) não rebaixa.

    As etiquetas são as do quadro de destino do lead (`transaction_type`) — o card
    reentrante vive num quadro só, então sincronizamos a temperatura DAQUELE quadro.
    """
    tt = lead.get("transaction_type")
    por_nome = {t: _temp_label_id(t, tt) for t in ("Quente", "Morno", "Frio")}
    por_nome = {t: i for t, i in por_nome.items() if i}  # só as configuradas
    if not por_nome:
        return  # etiquetas de temperatura não configuradas no .env
    rank_por_id = {tid: _TEMP_RANK[t] for t, tid in por_nome.items()}

    novo_id = por_nome.get(_lead_temperature(lead))
    atuais = set(_card_label_ids(card_id))
    temp_no_card = [tid for tid in atuais if tid in rank_por_id]

    alvo_rank = max([rank_por_id.get(novo_id, 0)] + [rank_por_id[t] for t in temp_no_card])
    alvo_id = next((tid for tid, r in rank_por_id.items() if r == alvo_rank), None)
    if not alvo_id:
        return
    if alvo_id not in atuais:
        add_label(card_id, alvo_id)
    for tid in temp_no_card:  # tira qualquer outra temperatura (mais fria ou duplicada)
        if tid != alvo_id:
            remove_label(card_id, tid)


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
        label_id = _source_label_id(lead["source"], lead.get("transaction_type"))
        if label_id:
            add_label(card_id, label_id)  # mostra que o card veio de 2 portais
        _sync_temperature_label(card_id, lead)  # esquenta o card se o lead reentrou mais quente
    except Exception as exc:  # noqa: BLE001 — vínculo já feito; isto é enriquecimento
        print(f"[trello] vínculo ok, anexo falhou ({card_id}): {exc}", file=sys.stderr)


def _carded_person_map() -> dict:
    """Mapa `(list_id-do-quadro, chave-da-pessoa) -> card_id` dos leads que já têm card.

    A chave inclui o QUADRO RESOLVIDO (`_list_id_for(transaction_type)`), não o
    `transaction_type` cru, porque a dedup é POR-QUADRO DE DESTINO: a mesma pessoa
    deve ter no máximo um card em CADA quadro. Chavear pelo tipo cru furaria isso
    quando o roteamento está inativo (só TRELLO_LIST_ID) — aí Compra e Aluguel caem
    no MESMO quadro fallback e precisam colapsar num card só. Com roteamento ativo,
    os tipos resolvem para list_ids distintos → um card por quadro.
    """
    mapa: dict[tuple[str, str], str] = {}
    for c in carded_contacts():
        chave = compute_person_key(c.get("phone"), c.get("email"))
        if chave:
            composta = (_list_id_for(c.get("transaction_type")), chave)
            if composta not in mapa:
                mapa[composta] = c["trello_card_id"]
    return mapa


def push_pending_leads(limit: int = 50) -> dict:
    """Envia ao Trello os leads pendentes. Best-effort: falha em um não para os outros.

    Dedup de identidade: se um lead pendente é a MESMA pessoa (telefone/e-mail) de
    alguém que já tem card — inclusive de OUTRO portal — não cria um 2º card.
    Vincula ao card existente e anexa um comentário com a info do novo portal
    ("uma pessoa, um card"). Processa do mais antigo pro mais novo, então o
    primeiro lead da pessoa é o dono do card.
    """
    with _push_lock:  # serializa a carga: ler pendentes → criar/vincular → marcar
        pendentes = fetch_pending_leads(limit)
        por_pessoa = _carded_person_map()
        criados = vinculados = falhas = 0
        for lead in pendentes:
            try:
                chave = compute_person_key(lead.get("phone"), lead.get("email"))
                # Chave POR-QUADRO DE DESTINO: só deduplica contra um card do MESMO
                # quadro resolvido (`_list_id_for`) — não do mesmo transaction_type cru.
                composta = (_list_id_for(lead.get("transaction_type")), chave) if chave else None
                if composta and composta in por_pessoa:
                    card_id = por_pessoa[composta]
                    set_trello_card_id(lead["source"], lead["external_id"], card_id)
                    _link_to_existing_card(card_id, lead)
                    vinculados += 1
                    continue
                card_id = create_card(lead)
                set_trello_card_id(lead["source"], lead["external_id"], card_id)
                if composta:
                    por_pessoa[composta] = card_id
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
# Setup dos quadros (infra como código): garante 2 quadros + listas + etiquetas
# ----------------------------------------------------------------------------
# NB: os nomes de quadro/área são lidos de `settings` DENTRO de setup_board (não em
# constantes de módulo) — capturá-los no import deixaria o valor stale e impediria
# os testes de trocar `settings` por um fake via monkeypatch.
PIPELINE = [
    "📥 Novos leads",
    "📞 Em contato",
    "🏠 Visita agendada",
    "💰 Proposta",
    "✅ Fechado (ganho)",
    "❌ Perdido",
]
SOURCE_LABELS = {"Wimóveis": "blue", "DFImóveis": "green"}
# Etiquetas de temperatura do lead (Fase 3 — scoring). A cor reforça a leitura no
# quadro: vermelho = quente, laranja = morno, azul-claro = frio.
TEMPERATURE_LABELS = {"🔥 Quente": "red", "🌤️ Morno": "orange", "❄️ Frio": "sky"}
BOARD_LABELS = {**SOURCE_LABELS, **TEMPERATURE_LABELS}
# Nome da etiqueta -> PREFIXO da env var do ID dela. O CLI acrescenta o sufixo do
# quadro (_COMPRA/_ALUGUEL) para imprimir a env var por-quadro (Fase 2).
_LABEL_ENV = {
    "Wimóveis": "TRELLO_LABEL_WIMOVEIS",
    "DFImóveis": "TRELLO_LABEL_DFIMOVEIS",
    "🔥 Quente": "TRELLO_LABEL_QUENTE",
    "🌤️ Morno": "TRELLO_LABEL_MORNO",
    "❄️ Frio": "TRELLO_LABEL_FRIO",
}
# Metadados de cada quadro para o CLI imprimir as env vars por-quadro do .env.
_BOARD_ENV = {
    "Compra": {"suffix": "_COMPRA", "list_env": "TRELLO_LIST_ID_COMPRA"},
    "Aluguel": {"suffix": "_ALUGUEL", "list_env": "TRELLO_LIST_ID_ALUGUEL"},
}


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


def _ensure_board(workspace_id: str, board_name: str) -> tuple[str, bool]:
    """Garante um quadro por NOME na área de trabalho (idempotente). Chamado 1× por
    quadro (Compra, Locação) — a busca por nome reaproveita o que já existir."""
    resp = requests.get(
        f"{_TRELLO_API}/members/me/boards",
        params={**_auth(), "fields": "name,idOrganization,closed"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    for b in resp.json():
        if b.get("idOrganization") == workspace_id and b["name"] == board_name and not b.get("closed"):
            return b["id"], False
    novo = requests.post(
        f"{_TRELLO_API}/boards",
        params={**_auth(), "name": board_name, "idOrganization": workspace_id, "defaultLists": "false"},
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
    for nome, cor in BOARD_LABELS.items():
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
    """Idempotente: garante OS DOIS quadros (Compra e Locação), cada um com as listas
    do funil e as etiquetas de origem + temperatura.

    A idempotência por-nome já existe em `_ensure_*`; a Fase 2 só aplica o mesmo
    ensure 2×, um por `transaction_type`. Devolve um dict por quadro
    (`{"Compra": {...}, "Aluguel": {...}}`) com board_id/listas/labels — o CLI usa
    isso para imprimir as env vars por-quadro prontas pro `.env`.
    """
    workspace_id = _find_workspace_id(settings.trello_workspace_name)
    if not workspace_id:
        raise RuntimeError(
            f"Area de trabalho '{settings.trello_workspace_name}' nao encontrada no Trello"
        )
    quadros = {
        "Compra": settings.trello_board_name_compra,
        "Aluguel": settings.trello_board_name_aluguel,
    }
    resultado = {}
    for chave, board_name in quadros.items():
        board_id, criado = _ensure_board(workspace_id, board_name)
        resultado[chave] = {
            "board_name": board_name,
            "board_id": board_id,
            "board_criado": criado,
            "listas": _ensure_lists(board_id),
            "labels": _ensure_labels(board_id),
        }
    return resultado


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
    quadros = setup_board()
    print(">>> IDs dos 2 quadros (Compra e Locação) — coloque no .env:\n")
    for chave, q in quadros.items():
        estado = "[criado]" if q["board_criado"] else "[ja existia]"
        env = _BOARD_ENV[chave]
        print(f"# Quadro {chave}: {q['board_name']} (id={q['board_id']}) {estado}")
        # Lista de entrada do quadro (é o que ATIVA o roteamento daquele tipo).
        print(f"   {env['list_env']}={q['listas'].get('📥 Novos leads', '')}")
        # Etiquetas do quadro, com o sufixo por-quadro na env var.
        for nome, lid in q["labels"].items():
            base = _LABEL_ENV.get(nome)
            if base:
                print(f"   {base}{env['suffix']}={lid}")
        print()


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
