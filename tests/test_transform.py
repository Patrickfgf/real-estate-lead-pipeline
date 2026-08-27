"""Testes da camada curada: normalização, enriquecimento, dedup e lead scoring.

As funções de normalização são puras (sem banco) — testadas direto. O
`build_clean` é testado de ponta a ponta inserindo leads crus e conferindo a
camada `leads_clean` resultante.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from src.db import get_connection, insert_lead
from src.models import Lead
from src.transform import (
    build_clean,
    clean_message,
    clean_name,
    ddd_to_uf,
    dfimoveis_operation,
    enrich,
    extract_extras,
    flag_duplicates,
    has_listing_intent,
    listing_url,
    normalize_email,
    normalize_phone,
    safe_dfimoveis_listing_url,
    score_lead,
    score_to_temperature,
    uf_to_regiao,
)

_TZ = ZoneInfo("America/Sao_Paulo")


# --------------------------------------------------------------------------
# normalize_phone
# --------------------------------------------------------------------------
def test_phone_celular_com_mascara():
    r = normalize_phone("(61) 99999-8888")
    assert r["phone_e164"] == "+5561999998888"
    assert r["phone_ddd"] == "61"
    assert r["phone_is_mobile"] is True
    assert r["phone_valid"] is True


def test_phone_so_digitos_e_codigo_pais_dao_o_mesmo_e164():
    assert normalize_phone("61988887777")["phone_e164"] == "+5561988887777"
    assert normalize_phone("+5561988887777")["phone_e164"] == "+5561988887777"


def test_phone_fixo_nao_e_celular():
    r = normalize_phone("(61) 3333-4444")
    assert r["phone_valid"] is True
    assert r["phone_is_mobile"] is False
    assert r["phone_e164"] == "+556133334444"


def test_phone_sem_ddd_e_invalido():
    r = normalize_phone("988887777")  # 9 dígitos, sem DDD identificável
    assert r["phone_valid"] is False
    assert r["phone_e164"] is None


def test_phone_vazio():
    r = normalize_phone(None)
    assert r["phone_valid"] is False
    assert r["phone_e164"] is None


def test_phone_0800_internacional_e_ddd_inexistente_sao_invalidos():
    """Valida o DDD contra a tabela Anatel: 0800/0300, número internacional e DDD
    inexistente NÃO podem virar telefone BR válido (senão furam scoring e dedup)."""
    # 0800: os 2 primeiros dígitos ("08") não são um DDD real
    assert normalize_phone("08001234567")["phone_valid"] is False
    # número US (+1 415 555 2671): "14" é DDD de SP, mas o número (9 díg.) não começa
    # com 9 → não é um celular BR válido
    r_us = normalize_phone("+1 415 555 2671")
    assert r_us["phone_valid"] is False
    assert r_us["phone_e164"] is None
    # DDD claramente inexistente
    assert normalize_phone("00988887777")["phone_valid"] is False


# --------------------------------------------------------------------------
# normalize_email
# --------------------------------------------------------------------------
def test_email_normaliza_e_extrai_dominio():
    r = normalize_email("  Maria.Souza@Email.COM ")
    assert r["email_clean"] == "maria.souza@email.com"
    assert r["email_valid"] is True
    assert r["email_domain"] == "email.com"


def test_email_invalido():
    r = normalize_email("nao-e-email")
    assert r["email_valid"] is False
    assert r["email_domain"] is None


def test_email_vazio():
    r = normalize_email(None)
    assert r["email_clean"] is None
    assert r["email_valid"] is False


# --------------------------------------------------------------------------
# clean_name
# --------------------------------------------------------------------------
def test_clean_name_capitaliza_e_mantem_particulas():
    assert clean_name("  joao   DA silva ") == "Joao da Silva"
    assert clean_name("MARIA SOUZA") == "Maria Souza"
    assert clean_name(None) is None


# --------------------------------------------------------------------------
# clean_message — remove o boilerplate promocional que o imovelweb anexa
# --------------------------------------------------------------------------
def test_clean_message_remove_spam_imovelweb():
    poluida = (
        " Olá! Quero ser contatado sobre este imóvel em venda que vi em Wimoveis. "
        "¡Após entrar em contato, peça que te avaliem! ¡Envie o seguinte link e seu "
        "bom atendimento será refletido nos seus anúncios! "
        "https://www.imovelweb.com.br/panel/feedback/400500600?utm_source=integracion"
    )
    assert (
        clean_message(poluida)
        == "Olá! Quero ser contatado sobre este imóvel em venda que vi em Wimoveis."
    )


def test_clean_message_preserva_texto_limpo():
    limpa = "Tenho interesse no apartamento. Podemos agendar visita?"
    assert clean_message(f"  {limpa}  ") == limpa


def test_clean_message_none_e_so_espaco_viram_none():
    assert clean_message(None) is None
    assert clean_message("   ") is None


def test_clean_message_preserva_exclamacao_espanhola_legitima():
    # '¡' fora do boilerplate da Navent (lead que escreve em espanhol) NÃO é truncado —
    # o corte ancora no marcador exato '¡Após'/'¡Después', não em qualquer '¡'.
    msg = "¡Hola! Tengo interés en este inmueble."
    assert clean_message(msg) == msg


# --------------------------------------------------------------------------
# enriquecimento geográfico
# --------------------------------------------------------------------------
def test_ddd_para_uf_e_regiao():
    assert ddd_to_uf("61") == "DF"
    assert ddd_to_uf("11") == "SP"
    assert ddd_to_uf("00") is None
    assert ddd_to_uf(None) is None
    assert uf_to_regiao("DF") == "Centro-Oeste"
    assert uf_to_regiao("SP") == "Sudeste"
    assert uf_to_regiao(None) is None


# --------------------------------------------------------------------------
# extract_extras (garimpo do raw_payload)
# --------------------------------------------------------------------------
def test_extras_dfimoveis_transacao_e_temperatura():
    payload = '{"transactionType": "SELL", "temperature": "Alta", "leadOrigin": "Grupo OLX"}'
    r = extract_extras(payload)
    assert r["transaction_type"] == "Compra"
    assert r["portal_temperature"] == "Alta"
    assert r["lead_origin"] == "Grupo OLX"
    assert r["is_destaque"] is False


def test_extras_wimoveis_destaque():
    r = extract_extras('{"planoDePublicacao": "DESTAQUE"}')
    assert r["is_destaque"] is True
    assert r["transaction_type"] is None


def test_extras_wimoveis_destacado_es():
    # a Navent manda 'destacado' (espanhol, minúsculo) no payload real do callback
    assert extract_extras('{"planoDePublicacao": "destacado"}')["is_destaque"] is True


def test_extras_aluguel_e_payload_quebrado():
    assert extract_extras('{"transactionType": "RENT"}')["transaction_type"] == "Aluguel"
    quebrado = extract_extras("isto não é json")
    assert quebrado["transaction_type"] is None
    assert quebrado["is_destaque"] is False


# --------------------------------------------------------------------------
# dfimoveis_operation — tipo de operação (Compra/Aluguel) do DFImóveis
# --------------------------------------------------------------------------
# Motivo empírico: até 2026-08 os payloads reais do DFImóveis NÃO traziam
# transactionType (0 de 101 leads) — desde então trazem, com o valor "SALE" (ver o
# teste do vocabulário abaixo). O fallback segue valendo para os leads que não o
# tragam: o sinal de aluguel é o clientListingId (código do CRM
# da corretora). A função prefere transactionType quando presente e, senão, cai
# numa heurística sobre o clientListingId.
def test_dfimoveis_operation_prefere_transaction_type():
    # Quando presente, transactionType manda (SELL->Compra, RENT->Aluguel).
    assert dfimoveis_operation({"transactionType": "SELL"}) == "Compra"
    assert dfimoveis_operation({"transactionType": "RENT"}) == "Aluguel"
    # transactionType vence até quando o clientListingId sugeriria outra coisa.
    assert dfimoveis_operation(
        {"transactionType": "SELL", "clientListingId": "al0001"}
    ) == "Compra"


def test_dfimoveis_operation_aceita_vocabulario_alternativo():
    """A DFImóveis passou a mandar SALE (2026-08) onde a doc do GrupoZAP diz SELL.

    O mapa é tolerante de propósito: aceitar um sinônimo a mais nunca classifica
    errado — só resolve um caso que hoje cairia em None e iria pro quadro fallback.
    """
    assert dfimoveis_operation({"transactionType": "SALE"}) == "Compra"
    assert dfimoveis_operation({"transactionType": "RENTAL"}) == "Aluguel"
    # minúsculas também (o mapa normaliza com .upper())
    assert dfimoveis_operation({"transactionType": "sale"}) == "Compra"
    # o tipo explícito vence a heurística do clientListingId, igual ao SELL
    assert dfimoveis_operation({"transactionType": "SALE", "clientListingId": "al0001"}) == "Compra"
    # e chega até a camada curada
    assert extract_extras('{"transactionType": "SALE"}')["transaction_type"] == "Compra"
    assert extract_extras('{"transactionType": "RENTAL"}')["transaction_type"] == "Aluguel"


def test_dfimoveis_operation_fallback_aluguel_pelo_client_listing_id():
    # Sem transactionType, o clientListingId do CRM sinaliza aluguel.
    assert dfimoveis_operation({"clientListingId": "al0001"}) == "Aluguel"
    assert dfimoveis_operation({"clientListingId": "ALUGUEL "}) == "Aluguel"  # com espaço
    assert dfimoveis_operation({"clientListingId": "al123"}) == "Aluguel"


def test_dfimoveis_operation_fallback_nao_aluguel_retorna_none():
    # 'CA' = casa (tipo do imóvel), não a operação → indefinido, não afirmamos Compra.
    assert dfimoveis_operation({"clientListingId": "CA0277"}) is None
    # Caso de borda crítico: 'alpaineiras' (Al. Paineiras, nome de condomínio)
    # começa com 'al' mas NÃO é aluguel — a regex ^al\d exige um dígito após 'al'.
    assert dfimoveis_operation({"clientListingId": "alpaineiras"}) is None
    assert dfimoveis_operation({"clientListingId": "1368266"}) is None  # numérico
    assert dfimoveis_operation({"clientListingId": ""}) is None  # vazio
    assert dfimoveis_operation({}) is None  # ausente


def test_dfimoveis_operation_tolera_str_json_e_none():
    # Aceita o payload como JSON string, dict ou None sem quebrar.
    assert dfimoveis_operation('{"transactionType": "RENT"}') == "Aluguel"
    assert dfimoveis_operation('{"clientListingId": "al0001"}') == "Aluguel"
    assert dfimoveis_operation("isto não é json") is None
    assert dfimoveis_operation(None) is None


# --------------------------------------------------------------------------
# enrich — precedência do transaction_type PERSISTIDO sobre o derivado do payload
# --------------------------------------------------------------------------
# A partir da Fase 1 o `transaction_type` é persistido em `leads_raw` (inclui o
# resultado da API Navent no Wimóveis). `leads_raw_dataframe()` passa a trazer essa
# coluna, e `enrich()` também deriva `transaction_type` do payload via extract_extras.
# O persistido tem PRECEDÊNCIA; o derivado é só fallback quando o persistido é nulo.
def _enrich_df(raw_payload, persisted=None):
    """Monta o df mínimo que `enrich` consome (phone/email/name/raw_payload)."""
    row = {"phone": None, "email": None, "name": "cliente", "raw_payload": raw_payload}
    if persisted is not None:
        row["transaction_type"] = persisted
    return pd.DataFrame([row])


def test_enrich_persistido_vence_o_derivado_do_payload():
    # persistido = "Aluguel" (veio da API Navent); payload deriva "Compra" (SELL).
    df = _enrich_df('{"transactionType": "SELL"}', persisted="Aluguel")
    out = enrich(df)
    # sem colisão de coluna (uma só) e o valor persistido prevalece
    assert list(out.columns).count("transaction_type") == 1
    assert out["transaction_type"].iloc[0] == "Aluguel"


def test_enrich_usa_derivado_quando_persistido_e_nulo():
    # Caso de produção: a coluna vem de leads_raw_dataframe() com NaN (tipo não setado)
    # → cai no fallback derivado do payload (RENT → Aluguel).
    df = _enrich_df('{"transactionType": "RENT"}', persisted=float("nan"))
    out = enrich(df)
    assert out["transaction_type"].iloc[0] == "Aluguel"


def test_enrich_sem_coluna_persistida_deriva_do_payload():
    # Compatibilidade: df sem a coluna transaction_type (ex.: chamada direta) deriva normalmente.
    df = _enrich_df('{"transactionType": "SELL"}', persisted=None)
    out = enrich(df)
    assert out["transaction_type"].iloc[0] == "Compra"


# --------------------------------------------------------------------------
# lead scoring (Fase 3) — rubrica do cliente: intenção > telefone > e-mail
# --------------------------------------------------------------------------
def test_has_listing_intent():
    assert has_listing_intent("AP-ASA-SUL-2Q-123") is True
    assert has_listing_intent("   ") is False
    assert has_listing_intent(None) is False


# --------------------------------------------------------------------------
# listing_url — link público do anúncio por portal
# --------------------------------------------------------------------------
def test_listing_url_wimoveis_monta_do_idnavplat():
    assert (
        listing_url("wimoveis", "3026198578")
        == "https://www.wimoveis.com.br/propriedades/imovel-3026198578.html"
    )


def test_listing_url_wimoveis_codigo_alfanumerico_nao_vira_url():
    # 'referencia' associada (código do CRM) não compõe a URL pública — só o idnavplat
    assert listing_url("wimoveis", "AP-ASA-SUL-2Q-123") is None


def test_listing_url_nao_monta_url_de_dfimoveis():
    # A URL do DFImóveis NÃO é montada a partir do listing_ref: ela chega pronta no
    # payload (campo `listingUrl`) e é persistida em coluna própria. Além disso o
    # listing_ref guarda o clientListingId (código do CRM, ex. "Plano500"), que não é
    # o id que compõe a URL pública (esse é o originListingId). Montar daqui daria link errado.
    assert listing_url("dfimoveis", "87027856") is None
    assert listing_url("dfimoveis", "CASA-LAGO-SUL-3Q-456") is None


# ---------------------------------------------------------------------------
# safe_dfimoveis_listing_url — a URL vem de FORA (payload de terceiro) e vira link
# clicável no card do corretor, então passa por allowlist antes de ser aceita.
# ---------------------------------------------------------------------------
def test_safe_dfimoveis_listing_url_aceita_url_canonica():
    url = "https://www.dfimoveis.com.br/meta/247550"
    assert safe_dfimoveis_listing_url(url) == url
    # a forma longa (com slug) também é válida
    longa = "https://www.dfimoveis.com.br/imovel/apartamento-3-quartos-venda-noroeste-1415858"
    assert safe_dfimoveis_listing_url(longa) == longa
    # subdomínio do portal é aceito
    assert safe_dfimoveis_listing_url("https://m.dfimoveis.com.br/meta/1") is not None
    # apex sem www
    assert safe_dfimoveis_listing_url("https://dfimoveis.com.br/meta/1") is not None


def test_safe_dfimoveis_listing_url_rejeita_host_de_fora():
    # o caso perigoso: host que apenas CONTÉM o domínio (checagem por `in` aceitaria)
    assert safe_dfimoveis_listing_url("https://dfimoveis.com.br.golpe.tld/meta/1") is None
    assert safe_dfimoveis_listing_url("https://naodfimoveis.com.br/meta/1") is None
    assert safe_dfimoveis_listing_url("https://exemplo.com/meta/1") is None


def test_safe_dfimoveis_listing_url_rejeita_esquema_inseguro():
    assert safe_dfimoveis_listing_url("http://www.dfimoveis.com.br/meta/1") is None  # sem TLS
    assert safe_dfimoveis_listing_url("javascript:alert(1)") is None
    assert safe_dfimoveis_listing_url("data:text/html,<b>x</b>") is None
    assert safe_dfimoveis_listing_url("//www.dfimoveis.com.br/meta/1") is None  # sem esquema


def test_safe_dfimoveis_listing_url_rejeita_lixo_e_abuso():
    assert safe_dfimoveis_listing_url(None) is None
    assert safe_dfimoveis_listing_url("") is None
    assert safe_dfimoveis_listing_url("   ") is None
    assert safe_dfimoveis_listing_url(12345) is None  # tipo errado não quebra
    # newline forjaria linhas falsas na descrição do card do Trello
    assert safe_dfimoveis_listing_url("https://www.dfimoveis.com.br/meta/1\n- 👤 Nome: Falso") is None
    assert safe_dfimoveis_listing_url("https://www.dfimoveis.com.br/meta/1\r\nX") is None
    # URL absurdamente longa polui o card
    assert safe_dfimoveis_listing_url("https://www.dfimoveis.com.br/meta/" + "9" * 400) is None


def test_safe_dfimoveis_listing_url_rejeita_divergencia_de_parser():
    """A barra invertida faz urlparse e navegador discordarem sobre o host.

    "https://evil.tld\\@www.dfimoveis.com.br/x" tem hostname "www.dfimoveis.com.br"
    para o urlparse (RFC 3986) e "evil.tld" para o Chrome (WHATWG, que normaliza \\ → /
    antes de separar a autoridade). Aceitar isso seria validar um host e mandar o
    corretor para outro — bypass completo da allowlist.
    """
    assert safe_dfimoveis_listing_url("https://evil.tld\\@www.dfimoveis.com.br/x") is None
    assert safe_dfimoveis_listing_url("https://www.dfimoveis.com.br\\@evil.tld/x") is None
    # userinfo comum (sem barra invertida) os dois parsers já leem igual — e é rejeitado
    assert safe_dfimoveis_listing_url("https://www.dfimoveis.com.br@evil.tld/x") is None


def test_safe_dfimoveis_listing_url_rejeita_invisiveis_unicode():
    """Separadores e controles invisíveis: quebram linha no card ou disfarçam o destino."""
    base = "https://www.dfimoveis.com.br/meta/1"
    assert safe_dfimoveis_listing_url(base + "\u2028- 👤 Nome: Falso") is None  # LINE SEPARATOR
    assert safe_dfimoveis_listing_url(base + "\u2029x") is None  # PARAGRAPH SEPARATOR
    assert safe_dfimoveis_listing_url(base + "\u202e" + "gpj.exe") is None  # RLO (spoof visual)
    assert safe_dfimoveis_listing_url(base + "\u0085x") is None  # NEL (C1)


def test_safe_dfimoveis_listing_url_normaliza_host_maiusculo():
    """Host em maiúsculas é a mesma origem — tem que ser aceito, não rejeitado."""
    assert safe_dfimoveis_listing_url("https://WWW.DFIMOVEIS.COM.BR/meta/1") is not None


def test_safe_dfimoveis_listing_url_faz_trim():
    assert safe_dfimoveis_listing_url("  https://www.dfimoveis.com.br/meta/1  ") == (
        "https://www.dfimoveis.com.br/meta/1"
    )


def test_listing_url_sem_ref_retorna_none():
    assert listing_url("wimoveis", None) is None
    assert listing_url("wimoveis", "   ") is None


def test_score_respeita_hierarquia_do_cliente():
    """intenção (sozinha) > telefone+e-mail > só telefone > só e-mail > nada."""
    so_intencao = score_lead(listing_intent=True, phone_valid=False,
                             phone_is_mobile=False, email_valid=False)
    tel_e_email = score_lead(listing_intent=False, phone_valid=True,
                             phone_is_mobile=True, email_valid=True)
    so_telefone = score_lead(listing_intent=False, phone_valid=True,
                             phone_is_mobile=False, email_valid=False)
    so_email = score_lead(listing_intent=False, phone_valid=False,
                          phone_is_mobile=False, email_valid=True)
    nada = score_lead(listing_intent=False, phone_valid=False,
                      phone_is_mobile=False, email_valid=False)
    # o lead com intenção supera até quem tem telefone E e-mail (o piso de 60 > 40)
    assert so_intencao > tel_e_email > so_telefone > so_email > nada == 0
    assert score_lead(listing_intent=True, phone_valid=True,
                      phone_is_mobile=True, email_valid=True) == 100


def test_score_celular_desempata_dentro_do_tier_de_telefone():
    celular = score_lead(listing_intent=False, phone_valid=True, phone_is_mobile=True, email_valid=False)
    fixo = score_lead(listing_intent=False, phone_valid=True, phone_is_mobile=False, email_valid=False)
    so_intencao = score_lead(listing_intent=True, phone_valid=False, phone_is_mobile=False, email_valid=False)
    assert celular > fixo
    assert celular < so_intencao  # nem o melhor telefone alcança a intenção


def test_score_to_temperature_faixas():
    quente = score_lead(listing_intent=True, phone_valid=False, phone_is_mobile=False, email_valid=False)
    morno = score_lead(listing_intent=False, phone_valid=True, phone_is_mobile=False, email_valid=False)
    frio = score_lead(listing_intent=False, phone_valid=False, phone_is_mobile=False, email_valid=True)
    assert score_to_temperature(quente) == "Quente"
    assert score_to_temperature(morno) == "Morno"
    assert score_to_temperature(frio) == "Frio"
    assert score_to_temperature(0) == "Frio"


# --------------------------------------------------------------------------
# flag_duplicates (entity-resolution-lite entre portais)
# --------------------------------------------------------------------------
def test_dedup_marca_primario_e_cross_portal():
    base = datetime(2026, 6, 1, 10, 0, tzinfo=_TZ)
    df = pd.DataFrame(
        [
            # mesma pessoa (mesmo telefone), portais diferentes — o mais antigo é o primário
            {"source": "wimoveis", "received_at": base, "phone_valid": True,
             "phone_e164": "+5561999998888", "email_valid": False, "email_clean": None},
            {"source": "dfimoveis", "received_at": base + timedelta(hours=1), "phone_valid": True,
             "phone_e164": "+5561999998888", "email_valid": False, "email_clean": None},
            # pessoa distinta, sem chave — fica sozinha
            {"source": "dfimoveis", "received_at": base + timedelta(hours=2), "phone_valid": False,
             "phone_e164": None, "email_valid": False, "email_clean": None},
        ]
    )
    out = flag_duplicates(df).sort_values("received_at").reset_index(drop=True)

    assert out.loc[0, "is_primary"] and not out.loc[0, "is_duplicate"]
    assert out.loc[1, "is_duplicate"] and out.loc[1, "cross_portal"]
    assert out.loc[0, "cross_portal"]  # o grupo inteiro é marcado como cross-portal
    assert out.loc[2, "is_primary"] and not out.loc[2, "cross_portal"]


def test_dedup_desempate_deterministico_com_received_at_igual():
    """Quando received_at empata (dois portais no mesmo instante), o primário é
    estável entre rebuilds — desempata por (source, external_id), não pela ordem
    física das linhas no banco."""
    base = datetime(2026, 6, 1, 10, 0, tzinfo=_TZ)
    linhas = [
        {"source": "wimoveis", "external_id": "b", "received_at": base, "phone_valid": True,
         "phone_e164": "+5561999998888", "email_valid": False, "email_clean": None},
        {"source": "dfimoveis", "external_id": "a", "received_at": base, "phone_valid": True,
         "phone_e164": "+5561999998888", "email_valid": False, "email_clean": None},
    ]
    prim_normal = flag_duplicates(pd.DataFrame(linhas))
    prim_invertido = flag_duplicates(pd.DataFrame(list(reversed(linhas))))
    chave = lambda out: out[out["is_primary"]]["external_id"].tolist()  # noqa: E731
    # independente da ordem de entrada, o primário é o mesmo (dfimoveis/"a" ordena 1º)
    assert chave(prim_normal) == chave(prim_invertido) == ["a"]


# --------------------------------------------------------------------------
# build_clean — ponta a ponta (raw -> clean)
# --------------------------------------------------------------------------
def _lead(ext, source, phone, raw_payload, when):
    return Lead(
        external_id=ext, source=source, name="cliente teste",
        email="cliente@email.com", phone=phone, message="oi",
        raw_payload=raw_payload, received_at=when,
    )


def test_build_clean_enriquece_e_deduplica_entre_portais():
    base = datetime(2026, 6, 1, 9, 0, tzinfo=_TZ)
    # Mesma pessoa (telefone normaliza para o mesmo E.164) entrando nos dois portais.
    insert_lead(_lead("clean-w-1", "wimoveis", "(61) 90000-1111",
                       '{"planoDePublicacao": "DESTAQUE"}', base))
    insert_lead(_lead("clean-d-1", "dfimoveis", "61900001111",
                       '{"transactionType": "SELL", "temperature": "Alta"}', base + timedelta(hours=1)))

    resumo = build_clean()
    assert resumo["raw"] >= 2
    assert resumo["duplicados"] >= 1
    assert resumo["cross_portal"] >= 1

    con = get_connection()
    linha_w = con.execute(
        "SELECT uf, regiao, is_destaque, phone_e164, name_clean, is_primary "
        "FROM leads_clean WHERE external_id = 'clean-w-1'"
    ).fetchone()
    uf, regiao, is_destaque, e164, name_clean, is_primary = linha_w
    assert uf == "DF" and regiao == "Centro-Oeste"
    assert is_destaque is True
    assert e164 == "+5561900001111"
    assert name_clean == "Cliente Teste"
    assert is_primary is True  # entrou primeiro → é o primário do par

    linha_d = con.execute(
        "SELECT transaction_type, portal_temperature, is_duplicate, cross_portal "
        "FROM leads_clean WHERE external_id = 'clean-d-1'"
    ).fetchone()
    transacao, temperatura, is_dup, cross = linha_d
    assert transacao == "Compra"
    assert temperatura == "Alta"
    assert is_dup is True and cross is True


def test_build_clean_pontua_e_classifica_temperatura():
    base = datetime(2026, 6, 3, 9, 0, tzinfo=_TZ)
    # Lead "quente": veio com intenção num anúncio (listing_ref) + celular + e-mail.
    insert_lead(Lead(
        external_id="score-hot", source="wimoveis", name="Lead Quente",
        email="hot@email.com", phone="(61) 98888-7777", message="quero esse",
        listing_ref="AP-ASA-SUL-2Q-999", raw_payload="{}", received_at=base,
    ))
    # Lead "frio": só e-mail — sem telefone válido e sem anúncio.
    insert_lead(Lead(
        external_id="score-cold", source="dfimoveis", name="Lead Frio",
        email="cold@email.com", phone=None, message="oi",
        listing_ref=None, raw_payload="{}", received_at=base + timedelta(minutes=1),
    ))

    build_clean()
    con = get_connection()
    hot = con.execute(
        "SELECT listing_intent, lead_score, lead_temperature "
        "FROM leads_clean WHERE external_id = 'score-hot'"
    ).fetchone()
    assert hot[0] is True
    assert hot[1] == 100  # intenção(60) + telefone(25) + celular(5) + e-mail(10)
    assert hot[2] == "Quente"

    cold = con.execute(
        "SELECT listing_intent, lead_score, lead_temperature "
        "FROM leads_clean WHERE external_id = 'score-cold'"
    ).fetchone()
    assert cold[0] is False
    assert cold[2] == "Frio"
