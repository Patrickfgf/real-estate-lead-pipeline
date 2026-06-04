"""Gerador de dados sintéticos (demo) — popula o pipeline para o dashboard/EDA.

Por que existe: o repositório clona vazio (nenhum lead real é versionado). Sem
dados, o dashboard (Fase 5) e os notebooks de EDA não têm o que mostrar. Este
módulo cria um conjunto **realista e reprodutível** de leads brasileiros e os
empurra pelo **pipeline de verdade** (`insert_lead` -> `build_clean`), exercitando
a ingestão, a normalização, o enriquecimento, o dedup de identidade e o scoring —
exatamente o mesmo caminho dos leads reais.

Camada de DESFECHO (simulada): o `leads_clean` tem atributos ricos, mas o
desfecho comercial (estágio no funil, ganho/perdido, tempo de 1ª resposta) vive
no Trello, que o banco não rastreia ainda. Para o dashboard contar a história de
DA completa (funil de conversão, win-rate, SLA), geramos uma tabela
`lead_outcomes_demo` com desfechos **simulados** — correlacionados com a
temperatura/score do lead (mais quente -> resposta mais rápida -> mais conversão).
É claramente rotulada como demo no app e no schema; quando a captura real do
desfecho do Trello existir, é só trocar a fonte.

Reprodutível: RNG e Faker recebem `seed` fixa, então a mesma chamada gera sempre
o mesmo conjunto (as datas deslizam com "hoje", para a demo parecer recente).

Banco isolado: por padrão grava em `data/demo.duckdb` (NÃO no banco operacional),
configurável por `--db`.

CLI:
    python -m src.seed                       # 600 leads, 120 dias, em data/demo.duckdb
    python -m src.seed --leads 1000 --days 180
    python -m src.seed --no-reset            # acrescenta em vez de recriar
"""
import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from faker import Faker

from src.models import Lead

# As funções puras de simulação (resposta/desfecho) NÃO tocam o banco — ficam no
# topo para serem testáveis. A orquestração (`run`) importa db/transform lá dentro,
# depois de fixar o DUCKDB_PATH, para a conexão singleton apontar para o banco demo.

_TZ = ZoneInfo("America/Sao_Paulo")
DEFAULT_DB = "data/demo.duckdb"
DEFAULT_SEED = 42

# DDDs ponderados: a corretora é de Brasília (61/DF domina), com cauda nacional
# para o enriquecimento geográfico (DDD->UF->região) ter o que mostrar.
_DDD_POOL = (
    ["61"] * 45
    + ["62"] * 6 + ["11"] * 8 + ["21"] * 6 + ["31"] * 5 + ["41"] * 3
    + ["51"] * 3 + ["71"] * 3 + ["81"] * 3 + ["85"] * 2 + ["48"] * 2
    + ["27"] * 2 + ["65"] * 2 + ["92"] * 1 + ["98"] * 1
)

# Pool pequeno e realista de corretores da Imobiliaria (uma corretora tem um punhado,
# não centenas) — dá sentido à visão "leads/conversão por corretor" no dashboard.
CORRETORES = [
    "Ana Ribeiro", "Bruno Carvalho", "Carla Nunes",
    "Diego Lima", "Eduarda Sá", "Felipe Rocha",
]

# Etapas do funil (espelham as listas do Trello, sem emoji para virar dado limpo).
FUNNEL_STAGES = ["Novos leads", "Em contato", "Visita agendada", "Proposta"]
STAGE_WON = "Fechado (ganho)"

# Probabilidades por temperatura (mais quente -> avança mais, perde menos).
_P_ADVANCE = {"Quente": 0.78, "Morno": 0.60, "Frio": 0.40}
_P_LOST = {"Quente": 0.10, "Morno": 0.18, "Frio": 0.28}
# Tempo-base de 1ª resposta em minutos (a equipe prioriza lead quente).
_RESP_BASE_MIN = {"Quente": 25, "Morno": 75, "Frio": 180}
# Chance de o lead NUNCA receber 1ª resposta (esfria sem contato).
_P_NO_RESPONSE = {"Quente": 0.03, "Morno": 0.10, "Frio": 0.22}
_SLA_MINUTES = 60  # meta de 1ª resposta usada como referência de SLA no dashboard


# ---------------------------------------------------------------------------
# Simulação de desfecho (funções puras — testáveis, sem banco)
# ---------------------------------------------------------------------------
def simulate_first_response_minutes(temperature: str, rng: random.Random) -> int | None:
    """Minutos até a 1ª resposta do corretor (None = nunca respondido).

    Distribuição exponencial em torno de uma base por temperatura: lead quente é
    respondido mais rápido. Uma fração (maior para frios) nunca recebe resposta.
    """
    if rng.random() < _P_NO_RESPONSE.get(temperature, 0.15):
        return None
    base = _RESP_BASE_MIN.get(temperature, 120)
    minutes = rng.expovariate(1 / base)
    return int(min(minutes, 60 * 72))  # teto de 72h


def simulate_outcome(temperature: str, rng: random.Random) -> dict:
    """Caminha o lead pelo funil e devolve o desfecho simulado.

    A cada etapa o lead pode: avançar (p_advance), ser perdido (p_lost) ou ficar
    em aberto (resto). Avançar além de "Proposta" = ganho. Resposta rápida (<= SLA)
    dá um leve empurrão; sem resposta, o avanço cai pela metade. Tudo dirigido pela
    temperatura (que já encoda o tier do score), então no agregado lead quente
    converte mais — a história que o dashboard conta.
    """
    resp = simulate_first_response_minutes(temperature, rng)
    p_adv = _P_ADVANCE.get(temperature, 0.5)
    if resp is not None and resp <= _SLA_MINUTES:
        p_adv = min(0.92, p_adv + 0.06)
    if resp is None:
        p_adv *= 0.5
    p_lost = _P_LOST.get(temperature, 0.2)

    idx = 0
    status = "open"
    while True:
        r = rng.random()
        if r < p_adv:
            if idx == len(FUNNEL_STAGES) - 1:
                status = "won"
                break
            idx += 1
        elif r < p_adv + p_lost:
            status = "lost"
            break
        else:
            status = "open"
            break

    won = status == "won"
    return {
        "first_response_minutes": resp,
        "status": status,
        "stage_reached": STAGE_WON if won else FUNNEL_STAGES[idx],
        "stage_index": len(FUNNEL_STAGES) if won else idx,
    }


# ---------------------------------------------------------------------------
# Geração de leads sintéticos (funções puras — devolvem objetos Lead)
# ---------------------------------------------------------------------------
def _make_phone(rng: random.Random, valid: bool = True) -> str:
    """Telefone BR mascarado. `valid=False` produz lixo (0800/curto) p/ a curada filtrar."""
    if not valid:
        return rng.choice(["0800 123 4567", "0300 789 1234", "9999", "+1 415 555 2671"])
    ddd = rng.choice(_DDD_POOL)
    if rng.random() < 0.85:  # celular (9 dígitos, começa com 9)
        num = "9" + "".join(str(rng.randint(0, 9)) for _ in range(8))
        return f"({ddd}) {num[:5]}-{num[5:]}"
    num = "".join(str(rng.randint(2, 9)) for _ in range(8))  # fixo (8 dígitos)
    return f"({ddd}) {num[:4]}-{num[4:]}"


def _listing_ref(rng: random.Random) -> str:
    return f"IM{rng.randint(100000, 999999)}"


def _make_lead(
    *,
    source: str,
    seq: int,
    person: dict,
    received_at: datetime,
    rng: random.Random,
    faker: Faker,
) -> Lead:
    """Monta um Lead canônico + o `raw_payload` no formato do portal de origem.

    `person` carrega a identidade (nome/telefone/e-mail) para permitir duplicados e
    cross-portal: a mesma pessoa pode reaparecer em outro lead/portal.
    """
    has_listing = rng.random() < 0.55  # intenção num anúncio específico
    listing = _listing_ref(rng) if has_listing else None
    external_id = f"{source}-{received_at:%Y%m}-{seq:05d}-{rng.randint(1000, 9999)}"
    lead_date = received_at
    advertiser = rng.choice(CORRETORES)  # corretor que recebeu o lead
    agency = "IMOB"
    tx = rng.choice(["SELL", "SELL", "RENT"])  # operação do anúncio (compra/aluguel)

    if source == "wimoveis":
        plano = rng.choice(["DESTAQUE", "SUPER_DESTAQUE", "SIMPLES", "SIMPLES", "SIMPLES"])
        payload = {
            "idEvento": external_id,
            "tipoEvento": "CONTACTO",
            "nome": person["name"],
            "email": person["email"],
            "telefone": person["phone"],
            "mensagem": faker.sentence(nb_words=12),
            "referencia": listing,
            "dataRegistro": lead_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "idContato": rng.randint(10000, 99999),
            "codigoImobiliaria": agency,
            "codigoDoAnunciante": advertiser,
            "cpf": None,
            "planoDePublicacao": plano,
            "transactionType": tx,
        }
    else:  # dfimoveis (VrSync)
        temp = rng.choice(["Baixa", "Média", "Média", "Alta"])
        payload = {
            "originLeadId": external_id,
            "leadOrigin": "DFImóveis",
            "timestamp": lead_date.strftime("%Y-%m-%dT%H:%M:%S-03:00"),
            "originListingId": listing,
            "name": person["name"],
            "email": person["email"],
            "phoneNumber": (person["phone"] or "").replace(" ", "").replace("-", "")
            .replace("(", "").replace(")", "") or None,
            "message": faker.sentence(nb_words=12),
            "temperature": temp,
            "transactionType": tx,
        }

    return Lead(
        external_id=external_id,
        source=source,
        name=person["name"],
        email=person["email"],
        phone=person["phone"],
        message=payload.get("mensagem") or payload.get("message"),
        listing_ref=listing,
        advertiser_code=advertiser,
        agency_code=agency,
        cpf=None,
        lead_date=lead_date,
        raw_payload=json.dumps(payload, ensure_ascii=False),
        received_at=received_at,
    )


def generate_leads(
    n: int,
    days: int,
    rng: random.Random,
    faker: Faker,
    end_date: datetime | None = None,
) -> list[Lead]:
    """Gera `n` leads sintéticos espalhados nos últimos `days` dias.

    ~12% são duplicados (a mesma pessoa reaparece, às vezes em outro portal ->
    cross-portal). ~10% têm telefone inválido e ~6% e-mail inválido, para a camada
    curada ter o que limpar/filtrar.
    """
    end = end_date or datetime.now(_TZ)
    people: list[dict] = []
    leads: list[Lead] = []

    for i in range(n):
        reuse = people and rng.random() < 0.12  # reaproveita uma pessoa (duplicado)
        if reuse:
            person = dict(rng.choice(people))
        else:
            valid_phone = rng.random() < 0.90
            has_phone = rng.random() < 0.95
            valid_email = rng.random() < 0.94
            name = faker.name()
            person = {
                "name": name,
                "phone": _make_phone(rng, valid=valid_phone) if has_phone else None,
                "email": (
                    faker.email() if valid_email else f"{faker.user_name()}@invalido"
                ) if rng.random() < 0.97 else None,
            }
            people.append(person)

        # Cross-portal: o duplicado costuma vir de um portal diferente do original.
        source = rng.choice(["wimoveis", "dfimoveis"])
        offset = timedelta(
            days=rng.randint(0, max(0, days - 1)),
            hours=rng.randint(0, 23),
            minutes=rng.randint(0, 59),
        )
        received_at = end - offset
        leads.append(
            _make_lead(source=source, seq=i, person=person, received_at=received_at, rng=rng, faker=faker)
        )
    return leads


# ---------------------------------------------------------------------------
# Tabela de desfecho simulada (demo)
# ---------------------------------------------------------------------------
_OUTCOMES_SCHEMA = """
CREATE OR REPLACE TABLE lead_outcomes_demo (
    person_key              VARCHAR,
    source                  VARCHAR,
    received_at             TIMESTAMPTZ,
    lead_temperature        VARCHAR,
    lead_score              INTEGER,
    first_response_minutes  INTEGER,   -- NULL = sem 1ª resposta
    responded_within_sla    BOOLEAN,
    status                  VARCHAR,   -- won / lost / open
    stage_reached           VARCHAR,
    stage_index             INTEGER
);
"""


def _build_outcomes(con, rng: random.Random) -> int:
    """Cria e popula `lead_outcomes_demo` para cada PESSOA primária do leads_clean."""
    con.execute(_OUTCOMES_SCHEMA)
    primarios = con.execute(
        "SELECT person_key, source, received_at, lead_temperature, lead_score "
        "FROM leads_clean WHERE is_primary AND person_key IS NOT NULL "
        "ORDER BY received_at, person_key"
    ).fetchall()

    linhas = []
    for person_key, source, received_at, temperature, score in primarios:
        out = simulate_outcome(temperature, rng)
        resp = out["first_response_minutes"]
        linhas.append((
            person_key, source, received_at, temperature, int(score),
            resp, (resp is not None and resp <= _SLA_MINUTES),
            out["status"], out["stage_reached"], out["stage_index"],
        ))

    con.executemany(
        "INSERT INTO lead_outcomes_demo VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", linhas
    )
    return len(linhas)


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------
def run(db_path: str = DEFAULT_DB, leads: int = 600, days: int = 120,
        reset: bool = True, seed: int = DEFAULT_SEED) -> dict:
    """Gera os leads, roda o pipeline real e popula o desfecho simulado."""
    # Fixa o banco ANTES de importar src.db (a conexão singleton lê este env). O
    # load_dotenv (override=False) não sobrescreve o que já está em os.environ.
    os.environ["DUCKDB_PATH"] = db_path
    from src import db
    from src.transform import build_clean

    rng = random.Random(seed)
    Faker.seed(seed)
    faker = Faker("pt_BR")

    con = db.get_connection()
    if reset:
        with db._lock:
            con.execute("DELETE FROM leads_raw")
            con.execute("DELETE FROM leads_dead_letter")
            con.execute("DROP TABLE IF EXISTS lead_outcomes_demo")

    gerados = generate_leads(leads, days, rng, faker)
    inseridos = sum(db.insert_lead(lead) for lead in gerados)
    resumo = build_clean()
    n_outcomes = _build_outcomes(con, random.Random(seed + 1))

    return {
        "db": db_path,
        "gerados": len(gerados),
        "inseridos": inseridos,
        **resumo,
        "outcomes": n_outcomes,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gera dados sintéticos (demo) para o dashboard/EDA.")
    p.add_argument("--db", default=DEFAULT_DB, help=f"caminho do DuckDB demo (default: {DEFAULT_DB})")
    p.add_argument("--leads", type=int, default=600, help="quantidade de leads a gerar")
    p.add_argument("--days", type=int, default=120, help="janela de datas, em dias para trás")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="seed do RNG (reprodutibilidade)")
    p.add_argument("--no-reset", action="store_true", help="acrescenta em vez de recriar o banco")
    return p.parse_args(argv)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):  # console Windows (cp1252) tolerante
        try:
            _stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    ns = _parse_args(sys.argv[1:])
    r = run(db_path=ns.db, leads=ns.leads, days=ns.days, reset=not ns.no_reset, seed=ns.seed)

    print("Dados sintéticos (demo) gerados:\n")
    print(f"  banco.................: {r['db']}")
    print(f"  leads gerados.........: {r['gerados']}")
    print(f"  inseridos (raw).......: {r['inseridos']}")
    print(f"  na curada (clean).....: {r['clean']}")
    print(f"  duplicados marcados...: {r['duplicados']} (dos quais cross-portal: {r['cross_portal']})")
    print(f"  desfechos simulados...: {r['outcomes']}")
    if r.get("por_temperatura"):
        ordem = {"Quente": 0, "Morno": 1, "Frio": 2}
        itens = sorted(r["por_temperatura"].items(), key=lambda kv: ordem.get(kv[0], 9))
        print("  por temperatura.......: " + ", ".join(f"{t or '—'}={n}" for t, n in itens))
    print("\nPronto. Rode o dashboard com:  streamlit run dashboard/app.py")
