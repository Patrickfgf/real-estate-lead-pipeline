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
    clean_name,
    ddd_to_uf,
    extract_extras,
    flag_duplicates,
    has_listing_intent,
    normalize_email,
    normalize_phone,
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
    r = extract_extras(payload, "dfimoveis")
    assert r["transaction_type"] == "Compra"
    assert r["portal_temperature"] == "Alta"
    assert r["lead_origin"] == "Grupo OLX"
    assert r["is_destaque"] is False


def test_extras_wimoveis_destaque():
    r = extract_extras('{"planoDePublicacao": "DESTAQUE"}', "wimoveis")
    assert r["is_destaque"] is True
    assert r["transaction_type"] is None


def test_extras_aluguel_e_payload_quebrado():
    assert extract_extras('{"transactionType": "RENT"}', "dfimoveis")["transaction_type"] == "Aluguel"
    quebrado = extract_extras("isto não é json", "wimoveis")
    assert quebrado["transaction_type"] is None
    assert quebrado["is_destaque"] is False


# --------------------------------------------------------------------------
# lead scoring (Fase 3) — rubrica do cliente: intenção > telefone > e-mail
# --------------------------------------------------------------------------
def test_has_listing_intent():
    assert has_listing_intent("AP-ASA-SUL-2Q-123") is True
    assert has_listing_intent("   ") is False
    assert has_listing_intent(None) is False


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
