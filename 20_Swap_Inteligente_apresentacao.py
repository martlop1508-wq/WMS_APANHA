import os
from urllib.parse import quote_plus

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import create_engine, text

st.set_page_config(
    page_title="Painel Executivo Plan06",
    page_icon="📊",
    layout="wide",
)

# =========================================================
# VISUAL
# =========================================================
st.markdown(
    """
    <style>
        .block-container {padding-top: 1rem; padding-bottom: 1rem;}
        .kpi-card {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            border-radius: 16px;
            padding: 14px 16px;
            border: 1px solid rgba(255,255,255,0.08);
            box-shadow: 0 6px 18px rgba(0,0,0,0.12);
            color: white;
            min-height: 106px;
        }
        .kpi-label {font-size: 12px; opacity: 0.80; margin-bottom: 6px;}
        .kpi-value {font-size: 28px; font-weight: 800; line-height: 1.0;}
        .kpi-sub {font-size: 12px; opacity: 0.82; margin-top: 8px;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📊 Painel Executivo — Produtos Inativos, Ativos, Cross e Direto Loja")
st.caption("Visão gerencial orientada à decisão sobre compra, venda, apanha e modalidade operacional.")

# =========================================================
# DB
# =========================================================
def env_first(*names, default=None):
    for name in names:
        val = os.getenv(name)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return default


def build_engine():
    host = env_first("WMS_DB_HOST", "DB_HOST", default="127.0.0.1")
    port = env_first("WMS_DB_PORT", "DB_PORT", default="3306")
    name = env_first("WMS_DB_NAME", "DB_NAME", default="wms_apanha")
    user = env_first("WMS_DB_USER", "DB_USER", default="wms_user")
    password = env_first("WMS_DB_PASS", "DB_PASS", "WMS_DB_PASSWORD", "DB_PASSWORD", default="")

    if not password:
        st.error("Senha do banco não encontrada nas variáveis de ambiente.")
        st.stop()

    safe_password = quote_plus(password)
    url = f"mysql+pymysql://{user}:{safe_password}@{host}:{port}/{name}?charset=utf8mb4"
    return create_engine(url, pool_pre_ping=True)


@st.cache_resource
def get_engine():
    return build_engine()


@st.cache_data(ttl=300)
def read_sql_df(sql: str, params=None) -> pd.DataFrame:
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


# =========================================================
# HELPERS
# =========================================================
def kpi_card(label: str, value: str, sub: str = ""):
    st.markdown(
        f"""
        <div class='kpi-card'>
            <div class='kpi-label'>{label}</div>
            <div class='kpi-value'>{value}</div>
            <div class='kpi-sub'>{sub}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def br_int(v):
    try:
        return f"{int(round(float(v))):,}".replace(",", ".")
    except Exception:
        return "0"


def br_num(v, casas=2):
    try:
        s = f"{float(v):,.{casas}f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "0,00"


def money(v):
    return f"R$ {br_num(v, 2)}"


def norm_modalidade(s: str) -> str:
    txt = str(s or "").strip().upper()
    if "CROSS" in txt:
        return "CROSS DOCKING"
    if "DIRETO" in txt:
        return "DIRETO LOJA"
    if txt in ("", "SEM INFO", "NONE", "NULL"):
        return "SEM INFO"
    return "ARMAZENADOS"


def faixa_cor(cat: str) -> str:
    mapa = {
        "Produtos Inativos p/ Compra": "#b91c1c",
        "Ativos p/ Venda": "#15803d",
        "Inativos com Estoque": "#ea580c",
        "Ativos com Estoque e sem Apanha": "#7c3aed",
        "Cross Docking": "#2563eb",
        "Armazenados": "#0f766e",
        "Direto Loja": "#475569",
    }
    return mapa.get(cat, "#334155")


# =========================================================
# BASE PLAN06
# =========================================================
BASE_SQL = """
SELECT
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.NROEMPRESA')) AS nroempresa,
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.CODIGO')) AS codigo,
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.DESCRICAO')) AS descricao,
    UPPER(TRIM(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.STATUS')))) AS status_produto,
    CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.ESTOQUECD')), ''), '0') AS DECIMAL(18,2)) AS estoque_cd,
    CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.CUSTOLIQUIDOCD')), ''), '0') AS DECIMAL(18,2)) AS custo_liquido_cd,
    UPPER(TRIM(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.ENDERECADO')))) AS enderecado,
    COALESCE(NULLIF(TRIM(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.MODALIDADECD'))), ''), 'SEM INFO') AS modalidade_cd,
    UPPER(TRIM(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.STATUS_ESTQCD')))) AS status_estqcd,
    NULLIF(TRIM(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.POSICAO'))), '') AS posicao,
    CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.PENDENCIA_COMPRA')), ''), '0') AS DECIMAL(18,2)) AS pendencia_compra,
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.SETOR')) AS setor,
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.SECAO')) AS secao,
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.FORNECEDOR')) AS fornecedor
FROM raw_plan_06
WHERE 1=1
"""


@st.cache_data(ttl=300)
def load_empresas():
    sql = """
    SELECT DISTINCT JSON_UNQUOTE(JSON_EXTRACT(payload, '$.NROEMPRESA')) AS nroempresa
    FROM raw_plan_06
    WHERE JSON_UNQUOTE(JSON_EXTRACT(payload, '$.NROEMPRESA')) IS NOT NULL
    ORDER BY 1
    """
    df = read_sql_df(sql)
    return df["nroempresa"].dropna().astype(str).tolist()


@st.cache_data(ttl=300)
def load_plan06(cd: str):
    df = read_sql_df(BASE_SQL + " AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.NROEMPRESA')) = :cd", {"cd": str(cd)})
    if df.empty:
        return df

    for c in ["estoque_cd", "custo_liquido_cd", "pendencia_compra"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    df["valor_estoque_cd"] = df["estoque_cd"] * df["custo_liquido_cd"]
    df["modalidade_norm"] = df["modalidade_cd"].apply(norm_modalidade)
    df["flag_cross_armazenado"] = (
        df["modalidade_norm"].eq("CROSS DOCKING")
        & (
            df["status_estqcd"].eq("SIM")
            | df["posicao"].fillna("").astype(str).str.strip().ne("")
            | df["enderecado"].isin(["SIM", "S"])
        )
    )
    return df


# =========================================================
# FILTROS
# =========================================================
empresas = load_empresas()
if not empresas:
    st.warning("Nenhuma empresa encontrada em raw_plan_06.")
    st.stop()

c1, c2, c3 = st.columns([1, 1.2, 1.2])
with c1:
    empresa = st.selectbox("CD", options=empresas, index=0)
with c2:
    top_n = st.selectbox("Top grupos no detalhamento", options=[10, 15, 20, 30], index=1)
with c3:
    exibir_valor = st.checkbox("Exibir valor estimado de estoque", value=True)

base = load_plan06(empresa)
if base.empty:
    st.info("Sem dados para o CD selecionado.")
    st.stop()

# =========================================================
# SEGMENTAÇÃO EXECUTIVA
# =========================================================
ativos_venda = base[base["status_produto"].eq("A")].copy()
inativos_compra = base[(base["status_produto"].eq("I")) & (base["pendencia_compra"] > 0)].copy()
inativos_estoque = base[(base["status_produto"].eq("I")) & (base["estoque_cd"] > 0)].copy()
sem_apanha = base[(base["status_produto"].eq("A")) & (base["estoque_cd"] > 0) & (base["enderecado"].eq("NAO"))].copy()
cross = base[base["modalidade_norm"].eq("CROSS DOCKING")].copy()
cross_armazenado = base[base["flag_cross_armazenado"]].copy()
armazenados = base[(base["modalidade_norm"].eq("ARMAZENADOS")) & (base["status_produto"].eq("A"))].copy()
direto_loja = base[base["modalidade_norm"].eq("DIRETO LOJA")].copy()

resumo = pd.DataFrame([
    {
        "categoria": "Produtos Inativos p/ Compra",
        "qtd_skus": inativos_compra["codigo"].nunique(),
        "qtd_estoque_cd": inativos_compra["estoque_cd"].sum(),
        "valor_estimado": inativos_compra["valor_estoque_cd"].sum(),
    },
    {
        "categoria": "Ativos p/ Venda",
        "qtd_skus": ativos_venda["codigo"].nunique(),
        "qtd_estoque_cd": ativos_venda["estoque_cd"].sum(),
        "valor_estimado": ativos_venda["valor_estoque_cd"].sum(),
    },
    {
        "categoria": "Inativos com Estoque",
        "qtd_skus": inativos_estoque["codigo"].nunique(),
        "qtd_estoque_cd": inativos_estoque["estoque_cd"].sum(),
        "valor_estimado": inativos_estoque["valor_estoque_cd"].sum(),
    },
    {
        "categoria": "Ativos com Estoque e sem Apanha",
        "qtd_skus": sem_apanha["codigo"].nunique(),
        "qtd_estoque_cd": sem_apanha["estoque_cd"].sum(),
        "valor_estimado": sem_apanha["valor_estoque_cd"].sum(),
    },
    {
        "categoria": "Cross Docking",
        "qtd_skus": cross["codigo"].nunique(),
        "qtd_estoque_cd": cross["estoque_cd"].sum(),
        "valor_estimado": cross["valor_estoque_cd"].sum(),
    },
    {
        "categoria": "Armazenados",
        "qtd_skus": armazenados["codigo"].nunique(),
        "qtd_estoque_cd": armazenados["estoque_cd"].sum(),
        "valor_estimado": armazenados["valor_estoque_cd"].sum(),
    },
    {
        "categoria": "Direto Loja",
        "qtd_skus": direto_loja["codigo"].nunique(),
        "qtd_estoque_cd": direto_loja["estoque_cd"].sum(),
        "valor_estimado": direto_loja["valor_estoque_cd"].sum(),
    },
])

# =========================================================
# KPIS
# =========================================================
kk1, kk2, kk3, kk4 = st.columns(4)
with kk1:
    kpi_card("Produtos inativos p/ compra", br_int(inativos_compra["codigo"].nunique()), "Inativos com pendência de compra")
with kk2:
    kpi_card("Ativos p/ venda", br_int(ativos_venda["codigo"].nunique()), "Base ativa comercial")
with kk3:
    kpi_card("Inativos com estoque", br_int(inativos_estoque["codigo"].nunique()), f"Estoque: {br_num(inativos_estoque['estoque_cd'].sum())}")
with kk4:
    kpi_card("Ativos sem apanha", br_int(sem_apanha["codigo"].nunique()), f"Estoque: {br_num(sem_apanha['estoque_cd'].sum())}")

kk5, kk6, kk7, kk8 = st.columns(4)
with kk5:
    kpi_card("Cross Docking", br_int(cross["codigo"].nunique()), "Cross normal + comercial em um grupo")
with kk6:
    kpi_card("Cross armazenado", br_int(cross_armazenado["codigo"].nunique()), "Cross com sinal de armazenagem")
with kk7:
    kpi_card("Armazenados", br_int(armazenados["codigo"].nunique()), f"Estoque: {br_num(armazenados['estoque_cd'].sum())}")
with kk8:
    kpi_card("Direto Loja", br_int(direto_loja["codigo"].nunique()), f"Estoque: {br_num(direto_loja['estoque_cd'].sum())}")

# =========================================================
# HISTOGRAMAS / COMPARATIVOS
# =========================================================
plot_df = resumo.copy()
plot_df["cor"] = plot_df["categoria"].map(faixa_cor)

st.markdown("## Histogramas comparativos")

cx1, cx2 = st.columns(2)
with cx1:
    fig_skus = px.bar(
        plot_df,
        x="categoria",
        y="qtd_skus",
        text="qtd_skus",
        color="categoria",
        color_discrete_map={c: faixa_cor(c) for c in plot_df['categoria'].tolist()},
    )
    fig_skus.update_layout(height=430, xaxis_title="", yaxis_title="Qtd SKUs", showlegend=False)
    st.plotly_chart(fig_skus, use_container_width=True)

with cx2:
    fig_est = px.bar(
        plot_df,
        x="categoria",
        y="qtd_estoque_cd",
        text="qtd_estoque_cd",
        color="categoria",
        color_discrete_map={c: faixa_cor(c) for c in plot_df['categoria'].tolist()},
    )
    fig_est.update_traces(texttemplate="%{text:,.2f}")
    fig_est.update_layout(height=430, xaxis_title="", yaxis_title="Qtd física estoque CD", showlegend=False)
    st.plotly_chart(fig_est, use_container_width=True)

if exibir_valor:
    fig_val = px.bar(
        plot_df,
        x="categoria",
        y="valor_estimado",
        text="valor_estimado",
        color="categoria",
        color_discrete_map={c: faixa_cor(c) for c in plot_df['categoria'].tolist()},
    )
    fig_val.update_traces(texttemplate="R$ %{text:,.2f}")
    fig_val.update_layout(height=430, xaxis_title="", yaxis_title="Valor estimado", showlegend=False)
    st.plotly_chart(fig_val, use_container_width=True)

# =========================================================
# ANÁLISE POR MODALIDADE
# =========================================================
st.markdown("## Comparativo por modalidade operacional")
mod = (
    base.groupby("modalidade_norm", as_index=False)
    .agg(
        qtd_skus=("codigo", "nunique"),
        qtd_estoque_cd=("estoque_cd", "sum"),
        valor_estimado=("valor_estoque_cd", "sum"),
        qtd_sem_apanha=("enderecado", lambda s: int(((base.loc[s.index, 'status_produto'].eq('A')) & (base.loc[s.index, 'estoque_cd'] > 0) & (base.loc[s.index, 'enderecado'].eq('NAO'))).sum())),
    )
    .sort_values(["qtd_skus", "qtd_estoque_cd"], ascending=[False, False])
)
fig_mod = px.bar(
    mod,
    x="modalidade_norm",
    y=["qtd_skus", "qtd_sem_apanha"],
    barmode="group",
    labels={"value": "Qtd", "modalidade_norm": "Modalidade", "variable": "Indicador"},
)
fig_mod.update_layout(height=420, xaxis_title="", yaxis_title="SKUs")
st.plotly_chart(fig_mod, use_container_width=True)
st.dataframe(mod, use_container_width=True, hide_index=True)

# =========================================================
# DETALHAMENTOS GERENCIAIS
# =========================================================
st.markdown("## Detalhamento gerencial")
aba1, aba2, aba3, aba4 = st.tabs([
    "Inativos para compra",
    "Ativos sem apanha",
    "Cross Docking",
    "Direto Loja / Armazenados",
])

with aba1:
    top_inativos = (
        inativos_compra.groupby(["fornecedor", "setor"], as_index=False)
        .agg(qtd_skus=("codigo", "nunique"), qtd_estoque_cd=("estoque_cd", "sum"), valor_estimado=("valor_estoque_cd", "sum"))
        .sort_values(["qtd_skus", "qtd_estoque_cd"], ascending=[False, False])
        .head(top_n)
    )
    st.dataframe(top_inativos, use_container_width=True, hide_index=True)
    st.dataframe(inativos_compra.sort_values(["valor_estoque_cd", "estoque_cd"], ascending=False).head(200), use_container_width=True, hide_index=True)

with aba2:
    sem_apanha_resumo = (
        sem_apanha.groupby("modalidade_cd", as_index=False)
        .agg(qtd_skus=("codigo", "nunique"), qtd_estoque_cd=("estoque_cd", "sum"), valor_estimado=("valor_estoque_cd", "sum"))
        .sort_values(["qtd_skus", "qtd_estoque_cd"], ascending=[False, False])
    )
    st.dataframe(sem_apanha_resumo, use_container_width=True, hide_index=True)
    st.dataframe(sem_apanha.sort_values(["valor_estoque_cd", "estoque_cd"], ascending=False).head(200), use_container_width=True, hide_index=True)

with aba3:
    cross_resumo = (
        cross.assign(cross_armazenado=lambda x: x["flag_cross_armazenado"].map({True: "SIM", False: "NAO"}))
        .groupby(["cross_armazenado", "enderecado"], as_index=False)
        .agg(qtd_skus=("codigo", "nunique"), qtd_estoque_cd=("estoque_cd", "sum"), valor_estimado=("valor_estoque_cd", "sum"))
        .sort_values(["qtd_skus", "qtd_estoque_cd"], ascending=[False, False])
    )
    st.dataframe(cross_resumo, use_container_width=True, hide_index=True)
    st.dataframe(cross.sort_values(["valor_estoque_cd", "estoque_cd"], ascending=False).head(200), use_container_width=True, hide_index=True)

with aba4:
    mix = pd.concat([
        armazenados.assign(grupo_exec="ARMAZENADOS"),
        direto_loja.assign(grupo_exec="DIRETO LOJA"),
    ], ignore_index=True)
    mix_resumo = (
        mix.groupby("grupo_exec", as_index=False)
        .agg(qtd_skus=("codigo", "nunique"), qtd_estoque_cd=("estoque_cd", "sum"), valor_estimado=("valor_estoque_cd", "sum"))
        .sort_values(["qtd_skus", "qtd_estoque_cd"], ascending=[False, False])
    )
    st.dataframe(mix_resumo, use_container_width=True, hide_index=True)
    st.dataframe(mix.sort_values(["valor_estoque_cd", "estoque_cd"], ascending=False).head(200), use_container_width=True, hide_index=True)

# =========================================================
# LEITURA EXECUTIVA
# =========================================================
st.markdown("## Leitura executiva")
col_a, col_b, col_c = st.columns(3)
with col_a:
    st.info(
        f"Maior risco comercial imediato: **{br_int(inativos_compra['codigo'].nunique())} SKUs** inativos ainda com pendência de compra."
    )
with col_b:
    st.info(
        f"Maior risco operacional imediato: **{br_int(sem_apanha['codigo'].nunique())} SKUs** ativos com estoque no CD e sem apanha."
    )
with col_c:
    st.info(
        f"Desvio de modalidade: **{br_int(cross_armazenado['codigo'].nunique())} SKUs** de cross com sinal de armazenagem no CD."
    )

st.caption(
    "Regra aplicada: Cross Docking normal e Comercial entram em um único grupo quando a modalidade contém 'CROSS'. "
    "Armazenados excluem CROSS e DIRETO LOJA. Ativos sem apanha = item ativo, com estoque no CD e ENDERECADO = NAO. "
    "Inativos para compra = STATUS = I com PENDENCIA_COMPRA > 0."
)

