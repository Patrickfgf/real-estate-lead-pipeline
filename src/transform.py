"""Camada curada (Fase 2): limpeza, normalização e enriquecimento dos leads.

Lê a camada CRUA (`leads_raw` — exatamente o que o portal mandou) e produz a
camada CURADA (`leads_clean`) por um transform em pandas. A camada curada é a
base analítica que o dashboard (Fase 5) e o scoring (Fase 3) consomem.

É **idempotente**: `build_clean()` reconstrói `leads_clean` inteira a partir de
`leads_raw` (CREATE OR REPLACE), então pode rodar quantas vezes quiser. Volume é
baixo (alguns leads/dia), então um rebuild completo é simples e barato.

As funções de normalização são **puras** (sem banco) — fáceis de testar e de
reaproveitar. A orquestração (`build_clean`) é o único ponto que toca o DuckDB.

CLI:
    python -m src.transform        # reconstrói leads_clean e imprime um resumo
"""
import json
import re
import sys

import pandas as pd

from src.db import leads_raw_dataframe, rebuild_clean_table


# ---------------------------------------------------------------------------
# Normalização de telefone (padrão Brasil)
# ---------------------------------------------------------------------------
def normalize_phone(raw: str | None) -> dict:
    """Normaliza um telefone brasileiro para forma canônica + metadados.

    Tolera máscara ("(61) 99999-8888"), só dígitos ("61988887777") e o código
    de país ("+55..."). Distingue celular (9 dígitos, começa com 9) de fixo
    (8 dígitos). Sem DDD identificável, marca como inválido — não chuta.
    """
    # Tolera entrada não-string (None, ou NaN quando o telefone vem NULL do banco
    # e o pandas lê como float) — o lead pode legitimamente não ter telefone.
    digits = re.sub(r"\D", "", raw if isinstance(raw, str) else "")
    # Remove o código do país (+55) quando o tamanho indica que ele está presente.
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]

    ddd = number = None
    if len(digits) in (10, 11):  # DDD (2) + fixo (8) ou celular (9)
        ddd, number = digits[:2], digits[2:]

    is_mobile = bool(number) and len(number) == 9 and number.startswith("9")
    # Valida o DDD contra a tabela oficial (`_DDD_UF`, Anatel): rejeita 0800/0300,
    # ramais e números internacionais cujos 2 primeiros dígitos não formam um DDD
    # brasileiro real (ex.: um número US de 11 dígitos cairia como "DDD 14"). Exige
    # ainda celular (9 díg.) começando com 9, ou fixo de 8 díg. Sem isso, telefone
    # lixo era marcado como válido e contaminava o scoring e o `person_key` do dedup.
    valid = (ddd in _DDD_UF) and bool(number) and (is_mobile or len(number) == 8)
    e164 = f"+55{ddd}{number}" if valid else None
    return {
        "phone_e164": e164,
        "phone_ddd": ddd,
        "phone_is_mobile": is_mobile,
        "phone_valid": valid,
    }


# ---------------------------------------------------------------------------
# Normalização de e-mail
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(raw: str | None) -> dict:
    """Minúsculas + trim, validação de formato e extração do domínio."""
    # Tolera entrada não-string (None/NaN) — e-mail também é opcional no lead.
    email = (raw if isinstance(raw, str) else "").strip().lower()
    valid = bool(_EMAIL_RE.match(email))
    domain = email.split("@", 1)[1] if valid else None
    return {"email_clean": email or None, "email_valid": valid, "email_domain": domain}


# ---------------------------------------------------------------------------
# Limpeza de nome
# ---------------------------------------------------------------------------
_NAME_PARTICLES = {"de", "da", "do", "das", "dos", "e"}


def clean_name(raw: str | None) -> str | None:
    """Colapsa espaços e capitaliza, mantendo partículas ("de/da/do") minúsculas."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    palavras = raw.split()
    out = []
    for i, p in enumerate(palavras):
        low = p.lower()
        out.append(low if (low in _NAME_PARTICLES and i > 0) else low.capitalize())
    return " ".join(out)


# ---------------------------------------------------------------------------
# Limpeza da mensagem (remove o boilerplate promocional do imovelweb)
# ---------------------------------------------------------------------------
# O boilerplate da Navent abre com "¡Após…" (PT) / "¡Después…" (ES). Ancorar nesse
# marcador exato — em vez de cortar em QUALQUER '¡' — evita truncar a mensagem de um
# lead que legitimamente escreva em espanhol ("¡Hola! …").
_BOILERPLATE_RE = re.compile(r"\s*¡(?:Após|Después)\b.*", re.DOTALL | re.IGNORECASE)


def clean_message(raw: str | None) -> str | None:
    """Remove o boilerplate promocional que o imovelweb anexa à mensagem do lead.

    Depois do texto do interessado, o portal cola um bloco pedindo avaliação + um
    link de feedback (.../panel/feedback/...). Esse ruído polui o card do corretor.
    Cortamos do marcador "¡Após"/"¡Después" até o fim e removemos uma URL de feedback
    residual. O texto original continua preservado no `raw_payload`, então nada se perde.
    """
    if not isinstance(raw, str):
        return None
    text = _BOILERPLATE_RE.sub("", raw)
    # Rede de segurança: remove uma URL de feedback do imovelweb que sobre sem o marcador.
    text = re.sub(r"https?://\S*imovelweb\.com\.br/panel/feedback\S*", "", text)
    return text.strip() or None


# ---------------------------------------------------------------------------
# Enriquecimento geográfico por DDD
# ---------------------------------------------------------------------------
# Mapa oficial DDD -> UF (Anatel). A corretora é de Brasília (61 -> DF), mas o
# mapa completo deixa o enriquecimento robusto para leads de qualquer região.
_DDD_UF = {
    "11": "SP", "12": "SP", "13": "SP", "14": "SP", "15": "SP", "16": "SP",
    "17": "SP", "18": "SP", "19": "SP",
    "21": "RJ", "22": "RJ", "24": "RJ", "27": "ES", "28": "ES",
    "31": "MG", "32": "MG", "33": "MG", "34": "MG", "35": "MG", "37": "MG", "38": "MG",
    "41": "PR", "42": "PR", "43": "PR", "44": "PR", "45": "PR", "46": "PR",
    "47": "SC", "48": "SC", "49": "SC",
    "51": "RS", "53": "RS", "54": "RS", "55": "RS",
    "61": "DF", "62": "GO", "64": "GO", "63": "TO", "65": "MT", "66": "MT", "67": "MS",
    "68": "AC", "69": "RO",
    "71": "BA", "73": "BA", "74": "BA", "75": "BA", "77": "BA", "79": "SE",
    "81": "PE", "87": "PE", "82": "AL", "83": "PB", "84": "RN",
    "85": "CE", "88": "CE", "86": "PI", "89": "PI",
    "91": "PA", "93": "PA", "94": "PA", "92": "AM", "97": "AM",
    "95": "RR", "96": "AP", "98": "MA", "99": "MA",
}
_UF_REGIAO = {
    "AC": "Norte", "AP": "Norte", "AM": "Norte", "PA": "Norte", "RO": "Norte",
    "RR": "Norte", "TO": "Norte",
    "AL": "Nordeste", "BA": "Nordeste", "CE": "Nordeste", "MA": "Nordeste",
    "PB": "Nordeste", "PE": "Nordeste", "PI": "Nordeste", "RN": "Nordeste", "SE": "Nordeste",
    "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MT": "Centro-Oeste", "MS": "Centro-Oeste",
    "ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "RS": "Sul", "SC": "Sul",
}


def ddd_to_uf(ddd: str | None) -> str | None:
    return _DDD_UF.get(ddd) if ddd else None


def uf_to_regiao(uf: str | None) -> str | None:
    return _UF_REGIAO.get(uf) if uf else None


# ---------------------------------------------------------------------------
# Extração de campos do payload cru (JSON semi-estruturado)
# ---------------------------------------------------------------------------
_TX_PT = {"SELL": "Compra", "RENT": "Aluguel"}
# Planos de publicação do Wimóveis que indicam anúncio em posição de destaque.
_DESTAQUE = {"DESTAQUE", "DESTACADO", "SUPER_DESTAQUE", "SUPERDESTAQUE", "TRIPLE", "PREMIUM", "TOP"}
# Heurística do CRM (DFImóveis): "al" seguido de dígito ("al0001") marca aluguel.
# Exige o dígito de propósito para NÃO pegar nomes de condomínio como "alpaineiras".
_ALUGUEL_CRM_RE = re.compile(r"^al\d")


def _as_dict(raw_payload: str | dict | None) -> dict:
    """Parseia o payload (JSON str, dict ou None) para dict, tolerando lixo → {}."""
    try:
        d = json.loads(raw_payload) if isinstance(raw_payload, str) else (raw_payload or {})
    except (ValueError, TypeError):
        d = {}
    return d if isinstance(d, dict) else {}


def extract_extras(raw_payload: str | dict | None) -> dict:
    """Garimpa o `raw_payload` por campos úteis que não estão no lead canônico.

    - `transaction_type`: VrSync `transactionType` (SELL/RENT) → Compra/Aluguel
    - `portal_temperature`: VrSync `temperature` (Baixa/Média/Alta) — sinal de scoring
    - `lead_origin`: VrSync `leadOrigin` (ex.: "Grupo OLX")
    - `is_destaque`: Wimóveis `planoDePublicacao` em posição premium
    """
    d = _as_dict(raw_payload)
    tx = (d.get("transactionType") or "").upper()
    plano = (d.get("planoDePublicacao") or "").upper()
    return {
        "transaction_type": _TX_PT.get(tx),
        "portal_temperature": d.get("temperature"),
        "lead_origin": d.get("leadOrigin"),
        "is_destaque": bool(plano) and plano in _DESTAQUE,
    }


def dfimoveis_operation(payload: str | dict | None) -> str | None:
    """Resolve o tipo de operação (Compra/Aluguel) de um lead do DFImóveis.

    Empírico: os payloads reais do DFImóveis NÃO trazem `transactionType` (0 de
    101 leads em produção). O único sinal de aluguel é o `clientListingId` (código
    do CRM da corretora). Por isso:

    1. Preferência: se o payload traz `transactionType`, usa a mesma tradução do
       `extract_extras` (SELL→Compra, RENT→Aluguel).
    2. Fallback (heurística do CRM): normaliza o `clientListingId` (lower+strip) e
       retorna "Aluguel" se contém "aluguel" OU casa a regex `^al\\d` ("al" + dígito).
       Senão → None (indefinido). NÃO afirmamos "Compra" no fallback: um "CA"/"AP"
       é o TIPO do imóvel (casa/apto), que pode ser pra alugar — o roteamento manda
       o indefinido pro quadro de Compra/fallback.
    """
    tipo = extract_extras(payload)["transaction_type"]
    if tipo is not None:
        return tipo
    client_listing_id = (_as_dict(payload).get("clientListingId") or "").strip().lower()
    if "aluguel" in client_listing_id or _ALUGUEL_CRM_RE.match(client_listing_id):
        return "Aluguel"
    return None


# ---------------------------------------------------------------------------
# Link público do anúncio (por portal)
# ---------------------------------------------------------------------------
# O número no fim da URL canônica é o id do anúncio na plataforma. No Wimóveis
# (Navent) esse id é o idnavplat, que guardamos como listing_ref quando a corretora
# não associou um código próprio. O DFImóveis usa um id interno que NÃO vem no
# webhook (só originListingId/clientListingId), então fica sem link até confirmarmos
# a fonte (DetailViewUrl do feed VrSync ou um campo do payload do DFImóveis).
_WIMOVEIS_LISTING_URL = "https://www.wimoveis.com.br/propriedades/imovel-{id}.html"


def listing_url(source: str, listing_ref: str | None) -> str | None:
    """Monta a URL pública do anúncio a partir do portal + listing_ref.

    Wimóveis: o listing_ref é o idnavplat (id do aviso na Navent) quando puramente
    numérico — o número final da URL canônica, que resolve sem precisar do slug.
    Um código de CRM associado (alfanumérico) não compõe a URL pública → None.
    DFImóveis: o id da URL é o id interno do portal, que não chega no webhook → None.
    """
    if not isinstance(listing_ref, str) or not listing_ref.strip():
        return None
    ref = listing_ref.strip()
    # só ASCII 0-9 (str.isdigit aceitaria dígitos Unicode como '²'); o idnavplat é int
    if source == "wimoveis" and re.fullmatch(r"[0-9]+", ref):
        return _WIMOVEIS_LISTING_URL.format(id=ref)
    return None


# ---------------------------------------------------------------------------
# Lead scoring (Fase 3) — rubrica priorizada PELO CLIENTE (a corretora)
# ---------------------------------------------------------------------------
# Hierarquia que o cliente definiu para o lead mais quente:
#   1º) já vem com INTENÇÃO num imóvel anunciado (referencia um anúncio
#       específico — comprar OU alugar, tanto faz: o que pesa é a intenção);
#   2º) deixou TELEFONE;
#   3º) deixou ao menos E-MAIL.
# Os pesos abaixo encodam exatamente essa ordem e ficam centralizados num só
# lugar — se o cliente recalibrar, é só mexer aqui (nada de reescrever lógica).
SCORE_WEIGHTS = {
    "listing_intent": 60,     # tem referência a um imóvel anunciado (listing_ref)
    "phone_valid": 25,        # deixou telefone válido
    "phone_mobile_bonus": 5,  # ...e é celular (contato direto) — desempate no tier do telefone
    "email_valid": 10,        # deixou ao menos e-mail válido
}
# Faixas de temperatura exibidas ao corretor (viram etiqueta no Trello). O piso
# da "intenção" (60) é, DE PROPÓSITO, maior que a soma de todos os sinais abaixo
# dele (25 + 5 + 10 = 40): assim QUALQUER lead com intenção fica acima de QUALQUER
# lead sem ela, respeitando a hierarquia do cliente mesmo com os pesos somados.
SCORE_HOT = 60   # >= 60  -> "Quente" (veio com intenção num anúncio)
SCORE_WARM = 25  # >= 25  -> "Morno"  (deixou telefone)
                 # <  25  -> "Frio"   (só e-mail / contato fraco)


def has_listing_intent(listing_ref) -> bool:
    """True se o lead referencia um imóvel anunciado (código do anúncio presente).

    É o sinal de "intenção" mais forte da rubrica: o interessado não veio genérico
    — veio atrás de um imóvel específico que estava anunciado. Exige string não
    vazia: quando o anúncio vem NULL do banco, o pandas o lê como NaN (float), que
    NÃO é intenção.
    """
    return isinstance(listing_ref, str) and bool(listing_ref.strip())


def score_lead(
    *,
    listing_intent: bool,
    phone_valid: bool,
    phone_is_mobile: bool = False,
    email_valid: bool = False,
) -> int:
    """Pontua o lead de 0 a 100 pela rubrica do cliente. Função pura e testável.

    intenção (60) > telefone (25, +5 se celular) > e-mail (10). Ver SCORE_WEIGHTS.
    """
    score = 0
    if listing_intent:
        score += SCORE_WEIGHTS["listing_intent"]
    if phone_valid:
        score += SCORE_WEIGHTS["phone_valid"]
        if phone_is_mobile:
            score += SCORE_WEIGHTS["phone_mobile_bonus"]
    if email_valid:
        score += SCORE_WEIGHTS["email_valid"]
    return min(score, 100)


def score_to_temperature(score: int) -> str:
    """Mapeia o score numérico para a faixa de temperatura (etiqueta no Trello)."""
    if score >= SCORE_HOT:
        return "Quente"
    if score >= SCORE_WARM:
        return "Morno"
    return "Frio"


def _score_row(row: pd.Series) -> dict:
    """Pontua uma linha JÁ enriquecida (colunas normalizadas + listing_ref crua)."""
    intent = has_listing_intent(row.get("listing_ref"))
    score = score_lead(
        listing_intent=intent,
        phone_valid=bool(row.get("phone_valid")),
        phone_is_mobile=bool(row.get("phone_is_mobile")),
        email_valid=bool(row.get("email_valid")),
    )
    return {
        "listing_intent": intent,
        "lead_score": score,
        "lead_temperature": score_to_temperature(score),
    }


# ---------------------------------------------------------------------------
# Pipeline: enriquecimento + dedup entre portais
# ---------------------------------------------------------------------------
# Colunas (e ordem) da camada curada `leads_clean`.
CLEAN_COLUMNS = [
    "source", "external_id",
    "name", "name_clean",
    "email_clean", "email_valid", "email_domain",
    "phone_e164", "phone_ddd", "phone_is_mobile", "phone_valid",
    "uf", "regiao",
    "transaction_type", "portal_temperature", "lead_origin", "is_destaque",
    "listing_ref", "advertiser_code", "agency_code", "cpf",
    "lead_date", "received_at",
    "person_key", "is_primary", "is_duplicate", "cross_portal",
    "listing_intent", "lead_score", "lead_temperature",
    "trello_card_id",
]


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica as normalizações puras, devolvendo o df com as colunas derivadas."""
    df = df.copy()
    # `transaction_type` pode chegar já PERSISTIDO da crua (Fase 1 — inclui o
    # resultado da API Navent no Wimóveis). Tiramos a coluna antes do concat para
    # não colidir com o `transaction_type` que `extract_extras` deriva do payload
    # (concat cego criaria coluna duplicada e quebraria o reindex do build_clean).
    persisted = df.pop("transaction_type") if "transaction_type" in df.columns else None
    phone = df["phone"].apply(normalize_phone).apply(pd.Series)
    email = df["email"].apply(normalize_email).apply(pd.Series)
    extras = df["raw_payload"].apply(extract_extras).apply(pd.Series)
    df = pd.concat([df, phone, email, extras], axis=1)
    # Precedência: o valor persistido vence; o derivado do payload é só fallback
    # onde o persistido é nulo (NaN quando a crua ainda não teve o tipo resolvido).
    if persisted is not None:
        df["transaction_type"] = persisted.where(persisted.notna(), df["transaction_type"])

    df["name_clean"] = df["name"].apply(clean_name)
    df["uf"] = df["phone_ddd"].apply(ddd_to_uf)
    df["regiao"] = df["uf"].apply(uf_to_regiao)

    # Lead scoring (Fase 3): depende das colunas normalizadas acima + listing_ref.
    scoring = df.apply(_score_row, axis=1).apply(pd.Series)
    df = pd.concat([df, scoring], axis=1)
    return df


def compute_person_key(phone: str | None, email: str | None) -> str | None:
    """Chave de identidade da pessoa a partir de telefone/e-mail CRUS.

    Telefone normalizado (E.164) tem precedência sobre e-mail. É a mesma regra
    usada no dedup em lote (`leads_clean`) e na carga do Trello (evitar 2º card
    para a mesma pessoa) — garantindo que os dois caminhos concordem.
    """
    ph = normalize_phone(phone)
    if ph["phone_valid"]:
        return f"tel:{ph['phone_e164']}"
    em = normalize_email(email)
    if em["email_valid"]:
        return f"email:{em['email_clean']}"
    return None


def _person_key(row: pd.Series) -> str | None:
    """Chave de identidade para uma linha JÁ enriquecida (usa as colunas normalizadas).

    Produz o mesmo formato que `compute_person_key` ("tel:<e164>" / "email:<clean>"),
    mas sem re-normalizar — aqui o telefone/e-mail já passaram por `enrich`.
    """
    if row.get("phone_valid"):
        return f"tel:{row['phone_e164']}"
    if row.get("email_valid"):
        return f"email:{row['email_clean']}"
    return None


def flag_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """Marca a mesma pessoa vinda em leads distintos (inclusive entre portais).

    Agrupa por `person_key`; o lead mais antigo (por `received_at`) é o primário,
    os demais ficam como duplicados. `cross_portal=True` quando o grupo tem mais
    de uma origem (a pessoa entrou no Wimóveis E na DFImóveis).
    """
    # Desempate determinístico: `received_at` é o critério principal (o mais antigo
    # vira o primário), mas quando dois leads da mesma pessoa empatam no instante
    # (dois portais no mesmo segundo) ordenamos por (source, external_id) para que
    # is_primary/is_duplicate não oscilem de um rebuild para o outro.
    sort_cols = [c for c in ("received_at", "source", "external_id") if c in df.columns]
    df = df.sort_values(sort_cols, na_position="last").reset_index(drop=True)
    df["person_key"] = df.apply(_person_key, axis=1)
    df["is_primary"] = True
    df["is_duplicate"] = False
    df["cross_portal"] = False

    com_chave = df[df["person_key"].notna()]
    for _key, grupo in com_chave.groupby("person_key"):
        idx = list(grupo.index)  # já ordenado por received_at (mais antigo 1º)
        cross = df.loc[idx, "source"].nunique() > 1
        for pos, i in enumerate(idx):
            df.at[i, "is_primary"] = pos == 0
            df.at[i, "is_duplicate"] = pos != 0
            df.at[i, "cross_portal"] = cross
    return df


def build_clean() -> dict:
    """Reconstrói `leads_clean` a partir de `leads_raw`. Idempotente.

    Retorna um resumo com as contagens (útil pro CLI e pra testes).
    """
    raw = leads_raw_dataframe()
    if raw.empty:
        vazio = pd.DataFrame(columns=CLEAN_COLUMNS)
        rebuild_clean_table(vazio)
        return {"raw": 0, "clean": 0, "duplicados": 0, "cross_portal": 0}

    curado = flag_duplicates(enrich(raw))
    curado = curado.reindex(columns=CLEAN_COLUMNS)
    rebuild_clean_table(curado)
    return {
        "raw": len(raw),
        "clean": len(curado),
        "duplicados": int(curado["is_duplicate"].sum()),
        "cross_portal": int((curado["cross_portal"] & curado["is_duplicate"]).sum()),
        "por_uf": curado["uf"].value_counts(dropna=False).to_dict(),
        "por_transacao": curado["transaction_type"].value_counts(dropna=False).to_dict(),
        "por_temperatura": curado["lead_temperature"].value_counts(dropna=False).to_dict(),
    }


# ---------------------------------------------------------------------------
# CLI: python -m src.transform
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):  # console Windows (cp1252) tolerante
        try:
            _stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    resumo = build_clean()
    print("Camada curada (leads_clean) reconstruída a partir de leads_raw:\n")
    print(f"  leads crus (raw)......: {resumo['raw']}")
    print(f"  leads na curada.......: {resumo['clean']}")
    print(f"  duplicados marcados...: {resumo['duplicados']}")
    print(f"  dos quais entre portais: {resumo['cross_portal']}")
    if resumo.get("por_uf"):
        ufs = ", ".join(f"{uf or '—'}={n}" for uf, n in resumo["por_uf"].items())
        print(f"  por UF................: {ufs}")
    if resumo.get("por_transacao"):
        txs = ", ".join(f"{tx or '—'}={n}" for tx, n in resumo["por_transacao"].items())
        print(f"  por tipo de transação.: {txs}")
    if resumo.get("por_temperatura"):
        ordem = {"Quente": 0, "Morno": 1, "Frio": 2}
        itens = sorted(resumo["por_temperatura"].items(), key=lambda kv: ordem.get(kv[0], 9))
        temps = ", ".join(f"{t or '—'}={n}" for t, n in itens)
        print(f"  por temperatura.......: {temps}")
