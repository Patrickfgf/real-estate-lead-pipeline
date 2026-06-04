"""Testes do gerador sintético (src/seed.py) — funções puras, sem tocar no banco.

Valida o que dá garantia ao dashboard: o desfecho simulado é DETERMINÍSTICO (mesma
seed → mesmo resultado) e MONOTÔNICO na temperatura (lead mais quente converte mais
e é respondido mais rápido, no agregado). Sem isso, os gráficos contariam uma
história não reproduzível.
"""
import random

from faker import Faker

from src.models import Lead
from src.seed import (
    FUNNEL_STAGES,
    STAGE_WON,
    generate_leads,
    simulate_first_response_minutes,
    simulate_outcome,
)


def _winrate(temperature: str, n: int = 4000, seed: int = 0) -> float:
    rng = random.Random(seed)
    won = sum(simulate_outcome(temperature, rng)["status"] == "won" for _ in range(n))
    return won / n


def _mediana(valores: list[int]) -> float:
    s = sorted(valores)
    return s[len(s) // 2]


# --------------------------------------------------------------------------
# simulate_outcome
# --------------------------------------------------------------------------
def test_outcome_deterministico():
    assert simulate_outcome("Quente", random.Random(7)) == simulate_outcome("Quente", random.Random(7))


def test_outcome_campos_validos():
    rng = random.Random(1)
    stages_validos = set(FUNNEL_STAGES) | {STAGE_WON}
    for _ in range(500):
        o = simulate_outcome(rng.choice(["Quente", "Morno", "Frio"]), rng)
        assert o["status"] in {"won", "lost", "open"}
        assert 0 <= o["stage_index"] <= len(FUNNEL_STAGES)
        assert o["stage_reached"] in stages_validos
        # "won" sse e somente se chegou em "Fechado (ganho)"
        assert (o["status"] == "won") == (o["stage_reached"] == STAGE_WON)


def test_winrate_cresce_com_temperatura():
    assert _winrate("Quente") > _winrate("Morno") > _winrate("Frio")


# --------------------------------------------------------------------------
# simulate_first_response_minutes
# --------------------------------------------------------------------------
def test_resposta_quente_mais_rapida_que_fria():
    rng = random.Random(3)
    quente = [m for _ in range(3000) if (m := simulate_first_response_minutes("Quente", rng)) is not None]
    rng = random.Random(3)
    fria = [m for _ in range(3000) if (m := simulate_first_response_minutes("Frio", rng)) is not None]
    assert _mediana(quente) < _mediana(fria)


def test_resposta_pode_ser_none_e_nunca_negativa():
    rng = random.Random(5)
    vals = [simulate_first_response_minutes("Frio", rng) for _ in range(500)]
    assert any(v is None for v in vals)            # frios às vezes nunca respondem
    assert all(v is None or v >= 0 for v in vals)  # quando há resposta, é não-negativa


# --------------------------------------------------------------------------
# generate_leads
# --------------------------------------------------------------------------
def test_generate_leads_conta_e_tipo():
    leads = generate_leads(60, 90, random.Random(2), _faker(2))
    assert len(leads) == 60
    assert all(isinstance(x, Lead) for x in leads)
    assert {x.source for x in leads} <= {"wimoveis", "dfimoveis"}
    assert all(x.raw_payload for x in leads)  # todo lead carrega o payload cru


def test_generate_leads_deterministico():
    def gera() -> list[str]:
        return [x.external_id for x in generate_leads(40, 30, random.Random(11), _faker(11))]

    assert gera() == gera()


def test_generate_leads_tem_duplicados_de_identidade():
    # com ~12% de reuso de pessoa, telefones se repetem (mesma pessoa, leads distintos)
    leads = generate_leads(200, 60, random.Random(4), _faker(4))
    phones = [x.phone for x in leads if x.phone]
    assert len(phones) != len(set(phones))


def _faker(seed: int) -> Faker:
    Faker.seed(seed)
    return Faker("pt_BR")
