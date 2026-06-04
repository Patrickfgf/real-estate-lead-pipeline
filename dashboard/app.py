"""Dashboard de leads (Fase 5) — add-on de Data Analysis sobre a camada curada.

Lê `leads_clean` (atributos reais do pipeline) e `lead_outcomes_demo` (desfecho
SIMULADO: funil, ganho/perdido, tempo de 1ª resposta) de um DuckDB e entrega uma
visão analítica pensada para uma corretora: volume, operação (compra/aluguel),
desempenho por corretor, quando os leads chegam (dia/hora), funil de conversão,
win-rate e SLA de resposta.

Rodar localmente:
    python -m src.seed                 # 1) gera os dados demo (data/demo.duckdb)
    streamlit run dashboard/app.py     # 2) sobe o dashboard

Hospedado (ex.: Streamlit Community Cloud): se o banco não existir, o app gera os
dados demo automaticamente na 1ª execução (auto-seed). A fonte é o `DUCKDB_PATH`
(default data/demo.duckdb).
"""
import os
import sys

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Garante a raiz do repo no sys.path para o `import src` (lazy, no auto-seed) funcionar
# quando o Streamlit roda o app a partir de um subdiretório — ex.: Community Cloud, que
# coloca dashboard/ no path, não a raiz do repositório.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.getenv("DUCKDB_PATH", "data/demo.duckdb")
SLA_MIN = 60  # meta de 1ª resposta (min) — bate com src.seed._SLA_MINUTES

TEMP_COLORS = {"Quente": "#ef4444", "Morno": "#f59e0b", "Frio": "#38bdf8"}
TEMP_ORDER = ["Quente", "Morno", "Frio"]
SOURCE_LABEL = {"wimoveis": "Wimóveis", "dfimoveis": "DFImóveis"}
TX_LABEL = {"Compra": "Compra", "Aluguel": "Aluguel"}
FUNNEL_ORDER = ["Novos leads", "Em contato", "Visita agendada", "Proposta", "Fechado (ganho)"]
DIAS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
_MARGIN = {"t": 50, "b": 0, "l": 0, "r": 0}

st.set_page_config(page_title="Jaré · Dashboard de Leads", page_icon="🏠", layout="wide")


def _ensure_demo_db(db_path: str) -> None:
    """Gera os dados demo se o banco não existir (1ª execução em ambiente hospedado)."""
    if os.path.exists(db_path):
        return
    with st.spinner("Gerando dados demo (primeira execução)…"):
        from src.seed import run
        run(db_path=db_path)
        # solta o lock de escrita para o load_data reabrir em read-only no mesmo processo
        from src import db
        db.close()


@st.cache_data(show_spinner=False)
def load_data(db_path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Carrega leads_clean e lead_outcomes_demo do DuckDB (read-only)."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        leads = con.execute("SELECT * FROM leads_clean").df()
        try:
            outcomes = con.execute("SELECT * FROM lead_outcomes_demo").df()
        except duckdb.CatalogException:
            outcomes = pd.DataFrame()
    finally:
        con.close()
    for df in (leads, outcomes):
        if not df.empty:
            # timestamptz volta em UTC; converte para horário local (dia/hora corretos).
            df["received_at"] = pd.to_datetime(df["received_at"], utc=True).dt.tz_convert("America/Sao_Paulo")
            df["data"] = df["received_at"].dt.date
            df["origem"] = df["source"].map(SOURCE_LABEL).fillna(df["source"])
    return leads, outcomes


def _pct(part: int, whole: int) -> str:
    return f"{(100 * part / whole):.1f}%" if whole else "—"


# ---------------------------------------------------------------------------
# Carregamento + guarda
# ---------------------------------------------------------------------------
_ensure_demo_db(DB_PATH)
if not os.path.exists(DB_PATH):
    st.title("🏠 Jaré · Dashboard de Leads")
    st.warning(f"Banco demo não encontrado em `{DB_PATH}`. Rode `python -m src.seed`.")
    st.stop()

leads_all, outcomes_all = load_data(DB_PATH)
if leads_all.empty:
    st.title("🏠 Jaré · Dashboard de Leads")
    st.warning("A tabela `leads_clean` está vazia. Rode `python -m src.seed` para popular a demo.")
    st.stop()


# ---------------------------------------------------------------------------
# Cabeçalho + banner de demo
# ---------------------------------------------------------------------------
st.title("🏠 Jaré · Dashboard de Leads")
st.caption("Camada curada `leads_clean` + desfecho simulado · pipeline Wimóveis + DFImóveis → DuckDB")
st.info(
    "🧪 **Dados simulados (demo).** Os atributos dos leads passam pelo pipeline real "
    "(ingestão → normalização → enriquecimento → dedup → scoring). O **desfecho** "
    "(funil, ganho/perdido, tempo de resposta) é **simulado** — o leads_clean ainda não "
    "captura o desfecho comercial do Trello. As correlações refletem o modelo de simulação.",
    icon="🧪",
)


# ---------------------------------------------------------------------------
# Filtros (sidebar)
# ---------------------------------------------------------------------------
st.sidebar.header("Filtros")
dmin, dmax = leads_all["data"].min(), leads_all["data"].max()
periodo = st.sidebar.date_input("Período", value=(dmin, dmax), min_value=dmin, max_value=dmax)
d_ini, d_fim = periodo if isinstance(periodo, tuple) and len(periodo) == 2 else (dmin, dmax)

origens = st.sidebar.multiselect(
    "Origem", sorted(leads_all["origem"].unique()), default=sorted(leads_all["origem"].unique())
)
temps = st.sidebar.multiselect("Temperatura", TEMP_ORDER, default=TEMP_ORDER)
corretores_disp = sorted(leads_all["advertiser_code"].dropna().unique())
corretores = st.sidebar.multiselect("Corretor", corretores_disp, default=corretores_disp)


def _filter_leads(df: pd.DataFrame) -> pd.DataFrame:
    m = (
        (df["data"] >= d_ini) & (df["data"] <= d_fim)
        & df["origem"].isin(origens)
        & df["lead_temperature"].isin(temps)
        & df["advertiser_code"].isin(corretores)
    )
    return df[m]


leads = _filter_leads(leads_all)
if leads.empty:
    st.warning("Nenhum lead no filtro selecionado. Amplie o período ou os filtros na barra lateral.")
    st.stop()

# Pessoas únicas (1 linha por pessoa) e o desfecho correspondente.
pessoas = leads[leads["is_primary"]]
if not outcomes_all.empty:
    chaves = set(pessoas["person_key"].dropna())
    outcomes = outcomes_all[outcomes_all["person_key"].isin(chaves)].copy()
    # corretor do lead primário da pessoa (para desempenho por corretor)
    adv_por_pessoa = pessoas.dropna(subset=["person_key"]).set_index("person_key")["advertiser_code"]
    outcomes["corretor"] = outcomes["person_key"].map(adv_por_pessoa)
    outcomes["won"] = outcomes["status"].eq("won")
else:
    outcomes = pd.DataFrame()


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------
total_leads = len(leads)
n_pessoas = len(pessoas)
n_dup = int(leads["is_duplicate"].sum())
n_cross = int((leads["cross_portal"] & leads["is_duplicate"]).sum())
n_quente = int((pessoas["lead_temperature"] == "Quente").sum())

k = st.columns(4)
k[0].metric("Leads recebidos", f"{total_leads:,}".replace(",", "."))
k[1].metric("Pessoas únicas", f"{n_pessoas:,}".replace(",", "."), help="Após dedup de identidade")
k[2].metric("Duplicados", f"{n_dup}", delta=f"{n_cross} cross-portal", delta_color="off")
k[3].metric("Score médio", f"{pessoas['lead_score'].mean():.0f}/100")

if not outcomes.empty:
    won = int(outcomes["won"].sum())
    resp = outcomes["first_response_minutes"].dropna()
    dentro_sla = int(outcomes["responded_within_sla"].fillna(False).sum())
    k2 = st.columns(4)
    k2[0].metric("Leads quentes", f"{n_quente}", delta=_pct(n_quente, n_pessoas), delta_color="off")
    k2[1].metric("Taxa de conversão", _pct(won, n_pessoas), help="Ganhos ÷ pessoas únicas (demo)")
    k2[2].metric("1ª resposta (mediana)", f"{resp.median():.0f} min" if len(resp) else "—")
    k2[3].metric("Dentro do SLA", _pct(dentro_sla, n_pessoas), help=f"1ª resposta ≤ {SLA_MIN} min")

st.divider()


# ---------------------------------------------------------------------------
# Abas analíticas
# ---------------------------------------------------------------------------
tab_visao, tab_op, tab_funil, tab_sla, tab_dados = st.tabs(
    ["📊 Visão geral", "🧭 Operação", "🫙 Funil & conversão", "⏱️ SLA de resposta", "🔎 Dados"]
)

with tab_visao:
    c1, c2 = st.columns([3, 2])
    serie = leads.groupby(["data", "origem"]).size().reset_index(name="leads")
    fig = px.area(serie, x="data", y="leads", color="origem", title="Volume de leads no tempo")
    fig.update_layout(margin=_MARGIN, legend_title_text="")
    c1.plotly_chart(fig, width="stretch")

    por_origem = leads["origem"].value_counts().reset_index()
    por_origem.columns = ["origem", "leads"]
    fig = px.pie(por_origem, names="origem", values="leads", hole=0.55, title="Leads por origem")
    fig.update_layout(margin=_MARGIN)
    c2.plotly_chart(fig, width="stretch")

    c3, c4 = st.columns(2)
    temp_cnt = pessoas["lead_temperature"].value_counts().reindex(TEMP_ORDER).fillna(0).reset_index()
    temp_cnt.columns = ["temperatura", "pessoas"]
    fig = px.bar(
        temp_cnt, x="temperatura", y="pessoas", color="temperatura",
        color_discrete_map=TEMP_COLORS, title="Pessoas por temperatura (lead scoring)",
    )
    fig.update_layout(margin=_MARGIN, showlegend=False)
    c3.plotly_chart(fig, width="stretch")

    fig = px.histogram(pessoas, x="lead_score", nbins=20, title="Distribuição do lead score (0–100)")
    fig.update_layout(margin=_MARGIN, bargap=0.05)
    c4.plotly_chart(fig, width="stretch")

with tab_op:
    c1, c2 = st.columns(2)
    tx = leads["transaction_type"].fillna("Não informado").value_counts().reset_index()
    tx.columns = ["operação", "leads"]
    fig = px.pie(tx, names="operação", values="leads", hole=0.55, title="Compra × Aluguel")
    fig.update_layout(margin=_MARGIN)
    c1.plotly_chart(fig, width="stretch")

    por_cor = pessoas["advertiser_code"].value_counts().reset_index()
    por_cor.columns = ["corretor", "leads"]
    fig = px.bar(por_cor.sort_values("leads"), x="leads", y="corretor", orientation="h",
                 title="Leads por corretor")
    fig.update_layout(margin=_MARGIN)
    c2.plotly_chart(fig, width="stretch")

    c3, c4 = st.columns(2)
    dow = leads["received_at"].dt.dayofweek.value_counts().reindex(range(7)).fillna(0)
    dow_df = pd.DataFrame({"dia": DIAS, "leads": dow.to_numpy()})
    fig = px.bar(dow_df, x="dia", y="leads", title="Leads por dia da semana")
    fig.update_layout(margin=_MARGIN)
    c3.plotly_chart(fig, width="stretch")

    hora = leads["received_at"].dt.hour.value_counts().reindex(range(24)).fillna(0).reset_index()
    hora.columns = ["hora", "leads"]
    fig = px.bar(hora, x="hora", y="leads", title="Leads por hora do dia")
    fig.update_layout(margin=_MARGIN)
    c4.plotly_chart(fig, width="stretch")
    st.caption("Dia/hora em horário de Brasília — útil para dimensionar o plantão de resposta.")

    if not outcomes.empty:
        conv_cor = (
            outcomes.groupby("corretor")["won"].mean().mul(100).reset_index().sort_values("won")
        )
        fig = px.bar(
            conv_cor, x="won", y="corretor", orientation="h",
            title="Taxa de conversão por corretor (%)",
            text=conv_cor["won"].map(lambda v: f"{v:.1f}%"),
        )
        fig.update_layout(margin=_MARGIN, xaxis_title="% ganhos")
        st.plotly_chart(fig, width="stretch")

    # Geografia rebaixada: quase tudo é DF, então fica como um indicador compacto.
    com_uf = leads[leads["uf"].notna()]
    pct_df = _pct(int((com_uf["uf"] == "DF").sum()), len(com_uf))
    st.metric("📍 Leads do DF", pct_df, help="Origem geográfica derivada do DDD do telefone")
    fora = leads[leads["uf"] != "DF"]["uf"].dropna().value_counts()
    if not fora.empty:
        with st.expander(f"Leads de fora do DF ({int(fora.sum())})"):
            st.bar_chart(fora)

with tab_funil:
    if outcomes.empty:
        st.warning("Sem desfecho simulado. Rode `python -m src.seed`.")
    else:
        contagem = [int((outcomes["stage_index"] >= i).sum()) for i in range(len(FUNNEL_ORDER))]
        fig = go.Figure(go.Funnel(
            y=FUNNEL_ORDER, x=contagem, textinfo="value+percent initial",
            marker={"color": ["#64748b", "#38bdf8", "#818cf8", "#f59e0b", "#22c55e"]},
        ))
        fig.update_layout(title="Funil de conversão (pessoas)", margin=_MARGIN)
        st.plotly_chart(fig, width="stretch")

        c1, c2 = st.columns(2)
        wr_origem = outcomes.groupby("origem")["won"].mean().mul(100).reset_index()
        fig = px.bar(
            wr_origem, x="origem", y="won", title="Taxa de conversão por origem (%)",
            text=wr_origem["won"].map(lambda v: f"{v:.1f}%"),
        )
        fig.update_layout(margin=_MARGIN, yaxis_title="% ganhos")
        c1.plotly_chart(fig, width="stretch")

        wr_temp = (
            outcomes.groupby("lead_temperature")["won"].mean().mul(100)
            .reindex(TEMP_ORDER).fillna(0).reset_index()
        )
        wr_temp.columns = ["temperatura", "won"]
        fig = px.bar(
            wr_temp, x="temperatura", y="won", color="temperatura", color_discrete_map=TEMP_COLORS,
            title="Taxa de conversão por temperatura (%)", text=wr_temp["won"].map(lambda v: f"{v:.1f}%"),
        )
        fig.update_layout(margin=_MARGIN, showlegend=False, yaxis_title="% ganhos")
        c2.plotly_chart(fig, width="stretch")
        st.caption("Lead mais quente converte mais — a hierarquia do scoring se reflete no desfecho.")

with tab_sla:
    if outcomes.empty:
        st.warning("Sem desfecho simulado. Rode `python -m src.seed`.")
    else:
        resp = outcomes["first_response_minutes"].dropna()
        c1, c2 = st.columns([3, 2])
        fig = px.histogram(x=resp, nbins=40, title="Tempo até a 1ª resposta (min)")
        fig.add_vline(x=SLA_MIN, line_dash="dash", line_color="#ef4444", annotation_text=f"SLA {SLA_MIN}min")
        fig.update_layout(margin=_MARGIN, showlegend=False, xaxis_title="minutos")
        c1.plotly_chart(fig, width="stretch")

        sla_conv = (
            outcomes.assign(
                resposta=outcomes["responded_within_sla"].map(
                    {True: f"≤ {SLA_MIN} min", False: "lenta/sem resposta"}
                )
            )
            .groupby("resposta")["won"].mean().mul(100).reset_index()
        )
        fig = px.bar(
            sla_conv, x="resposta", y="won", title="Conversão × velocidade de resposta",
            text=sla_conv["won"].map(lambda v: f"{v:.1f}%"), color="resposta",
            color_discrete_map={f"≤ {SLA_MIN} min": "#22c55e", "lenta/sem resposta": "#ef4444"},
        )
        fig.update_layout(margin=_MARGIN, showlegend=False, yaxis_title="% ganhos")
        c2.plotly_chart(fig, width="stretch")
        st.caption("Responder rápido (≤ SLA) está associado a uma conversão maior (modelo demo).")

with tab_dados:
    cols = [
        "received_at", "origem", "name_clean", "advertiser_code", "transaction_type",
        "lead_temperature", "lead_score", "uf", "is_primary", "cross_portal", "listing_intent",
    ]
    st.dataframe(
        leads[cols].sort_values("received_at", ascending=False), width="stretch", hide_index=True,
    )
    st.caption(f"{len(leads)} leads no filtro atual. Camada curada `leads_clean`.")
