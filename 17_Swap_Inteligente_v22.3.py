import os
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import create_engine, text


# =========================================================
# DASHBOARD EXECUTIVO WMS — V3 DIRETORIA
# ---------------------------------------------------------
# Objetivo:
# - Entregar uma visão executiva de produtos ativos, inativos,
#   apanha, cross docking, efetividade e criticidade por linha.
# - Blindado para produção com tratamento de chaves ausentes
#   no JSON da raw_plan_06.
# - Mantém leitura operacional, mas com visual diretoria.
# =========================================================

VERSAO = "V3 Diretoria"

st.set_page_config(
    page_title=f"Dashboard Executivo WMS — {VERSAO}",
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
            min-height: 110px;
        }
        .kpi-label {font-size: 12px; opacity: 0.82; margin-bottom: 6px;}
        .kpi-value {font-size: 30px; font-weight: 800; line-height: 1.0;}
        .kpi-sub {font-size: 12px; opacity: 0.86; margin-top: 8px;}
        .helper-box {
            background: #f8fafc;
            border: 1px solid #cbd5e1;
            color: #0f172a;
            padding: 12px 14px;
            border-radius: 12px;
            font-size: 13px;
        }
        .section-title {
            font-size: 1.05rem;
            font-weight: 700;
            margin-top: 0.5rem;
            margin-bottom: 0.4rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title(f"📊 Painel Executivo — Ativos, Inativos, Apanha, Cross e Direto Loja ({VERSAO})")
st.caption("Visão diretoria para compra, venda, efetividade do ativo, ruptura potencial e criticidade por linha.")


# =========================================================
# HELPERS
# =========================================================
def env_first(*names, default=None):
    for name in names:
        val = os.getenv(name)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return default


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


def pct(v):
    try:
        return f"{float(v):.1f}%".replace(".", ",")
    except Exception:
        return "0,0%"


def safe_upper(x):
    return str(x or "").strip().upper()


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


def norm_modalidade(s: str) -> str:
    txt = safe_upper(s)
    if "CROSS" in txt:
        return "CROSS DOCKING"
    if "DIRETO" in txt:
        return "DIRETO LOJA"
    if txt in ("", "SEM INFO", "NONE", "NULL"):
        return "SEM INFO"
    return "ARMAZENADOS"


def flag_sim(x) -> bool:
    return safe_upper(x) in {"S", "SIM", "Y", "YES", "TRUE", "1"}


def infer_linha(df: pd.DataFrame) -> pd.Series:
    # prioridade de colunas
    for col in ["secao", "setor", "linha_json", "familia_linha", "categoria"]:
        if col in df.columns:
            s = df[col].fillna("").astype(str).str.strip()
            if s.ne("").any():
                return s.replace({"": "SEM LINHA"}).str.upper()
    return pd.Series(["SEM LINHA"] * len(df), index=df.index)


def infer_tem_saida(df: pd.DataFrame) -> pd.Series:
    # melhor sinal disponível na base atual
    sinais = pd.Series(False, index=df.index)
    for c in ["qtd_saida_30d", "visitas_30d"]:
        if c in df.columns:
            sinais = sinais | pd.to_numeric(df[c], errors="coerce").fillna(0).gt(0)

    if "dias_sem_saida" in df.columns:
        dias = pd.to_numeric(df["dias_sem_saida"], errors="coerce").fillna(0)
        sinais = sinais | dias.between(1, 29)

    if "sem_saida_30d_ou_mais" in df.columns:
        flag_sem_saida = (
            df["sem_saida_30d_ou_mais"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
            .isin(["1", "S", "SIM", "TRUE", "Y"])
        )
        sinais = sinais | (~flag_sem_saida)

    return sinais


def semaforo_html(score: float) -> str:
    if score >= 60:
        return "🔴 CRÍTICA"
    if score >= 35:
        return "🟡 ATENÇÃO"
    return "🟢 CONTROLADA"


def insight_card(texto: str, tone: str = "normal"):
    cores = {
        "normal": ("#0f172a", "#e2e8f0"),
        "risk": ("#7f1d1d", "#fecaca"),
        "good": ("#14532d", "#bbf7d0"),
        "warn": ("#78350f", "#fde68a"),
    }
    bg, bd = cores.get(tone, cores["normal"])
    st.markdown(
        f"""
        <div style="background:{bg};border:1px solid {bd};color:white;padding:12px 14px;border-radius:12px;font-size:13px;">
            {texto}
        </div>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# DB
# =========================================================
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
# EXTRAÇÃO DA RAW PLAN06 — BLINDADA
# =========================================================
# Todas as chaves abaixo são opcionais. Se não existirem no payload,
# o JSON_EXTRACT retorna NULL e o CAST/COALESCE assume o fallback.
BASE_SQL = """
SELECT
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.NROEMPRESA')) AS nroempresa,
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.CODIGO')) AS codigo,
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.DESCRICAO')) AS descricao,
    UPPER(TRIM(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.STATUS')))) AS status_produto,
    CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.ESTOQUECD')), ''), '0') AS DECIMAL(18,2)) AS estoque_cd,
    UPPER(TRIM(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.ENDERECADO')))) AS enderecado,
    COALESCE(NULLIF(TRIM(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.MODALIDADECD'))), ''), 'SEM INFO') AS modalidade_cd,
    UPPER(TRIM(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.STATUS_ESTQCD')))) AS status_estqcd,
    NULLIF(TRIM(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.POSICAO'))), '') AS posicao,
    CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.PENDENCIA_COMPRA')), ''), '0') AS DECIMAL(18,2)) AS pendencia_compra,
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.SETOR')) AS setor,
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.SECAO')) AS secao,
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.FORNECEDOR')) AS fornecedor,
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.LINHA')) AS linha_json,
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.FAMILIA')) AS familia_linha,
    JSON_UNQUOTE(JSON_EXTRACT(payload, '$.CATEGORIA')) AS categoria,
    CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.DIAS_SEM_SAIDA')), ''), '0') AS DECIMAL(18,2)) AS dias_sem_saida,
    UPPER(TRIM(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.SEM_SAIDA_30D_OU_MAIS')))) AS sem_saida_30d_ou_mais,
    CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.QTD_SAIDA_30D')), ''), '0') AS DECIMAL(18,2)) AS qtd_saida_30d,
    CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.VISITAS_30D')), ''), '0') AS DECIMAL(18,2)) AS visitas_30d,
    UPPER(TRIM(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.COMPRADO_COM_SEM_SAIDA_30D_OU_MAIS')))) AS comprado_com_sem_saida_30d_ou_mais
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
def load_plan06(cd: str) -> pd.DataFrame:
    df = read_sql_df(
        BASE_SQL + " AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.NROEMPRESA')) = :cd",
        {"cd": str(cd)},
    )
    if df.empty:
        return df

    # Normalizações
    for c in ["estoque_cd", "pendencia_compra", "dias_sem_saida", "qtd_saida_30d", "visitas_30d"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    df["status_produto"] = df["status_produto"].fillna("").astype(str).str.strip().str.upper()
    df["modalidade_norm"] = df["modalidade_cd"].apply(norm_modalidade)
    df["linha_exec"] = infer_linha(df)

    # Flags
    df["flag_ativo"] = df["status_produto"].eq("A")
    df["flag_inativo"] = df["status_produto"].eq("I")
    df["flag_com_estoque"] = df["estoque_cd"].gt(0)
    df["flag_enderecado"] = df["enderecado"].apply(flag_sim)

    df["flag_sem_apanha"] = df["flag_ativo"] & df["flag_com_estoque"] & (~df["flag_enderecado"])
    df["flag_cross_armazenado"] = (
        df["modalidade_norm"].eq("CROSS DOCKING")
        & (
            df["status_estqcd"].fillna("").astype(str).str.strip().str.upper().eq("SIM")
            | df["posicao"].fillna("").astype(str).str.strip().ne("")
            | df["flag_enderecado"]
        )
    )
    df["flag_cross_sem_estoque"] = df["modalidade_norm"].eq("CROSS DOCKING") & (~df["flag_com_estoque"])
    df["flag_ativo_sem_estoque"] = df["flag_ativo"] & (~df["flag_com_estoque"])
    df["flag_tem_saida"] = infer_tem_saida(df)
    df["flag_ativo_efetivo"] = (
        df["flag_ativo"] & df["flag_com_estoque"] & df["flag_enderecado"] & df["flag_tem_saida"]
    )

    return df


# =========================================================
# FILTROS
# =========================================================
empresas = load_empresas()
if not empresas:
    st.warning("Nenhuma empresa encontrada em raw_plan_06.")
    st.stop()

f1, f2, f3 = st.columns([1, 1.1, 1.2])
with f1:
    empresa = st.selectbox("CD", options=empresas, index=0)
with f2:
    top_n = st.selectbox("Top grupos", options=[10, 15, 20, 30, 50], index=2)
with f3:
    filtro_linha = st.text_input("Filtrar linha (opcional)")

base = load_plan06(empresa)
if base.empty:
    st.info("Sem dados para o CD selecionado.")
    st.stop()

if filtro_linha.strip():
    filtro = filtro_linha.strip().upper()
    base = base[base["linha_exec"].astype(str).str.contains(filtro, na=False)]

if base.empty:
    st.info("Sem dados após aplicar filtro.")
    st.stop()


# =========================================================
# SEGMENTAÇÃO
# =========================================================
ativos_venda = base[base["flag_ativo"]].copy()
ativos_com_estoque = base[base["flag_ativo"] & base["flag_com_estoque"]].copy()
ativos_com_apanha = base[base["flag_ativo"] & base["flag_com_estoque"] & base["flag_enderecado"]].copy()
ativos_efetivos = base[base["flag_ativo_efetivo"]].copy()

inativos_compra = base[(base["flag_inativo"]) & (base["pendencia_compra"] > 0)].copy()
inativos_estoque = base[(base["flag_inativo"]) & (base["flag_com_estoque"])].copy()
sem_apanha = base[base["flag_sem_apanha"]].copy()
cross = base[base["modalidade_norm"].eq("CROSS DOCKING")].copy()
cross_armazenado = base[base["flag_cross_armazenado"]].copy()
cross_sem_estoque = base[base["flag_cross_sem_estoque"]].copy()
armazenados = base[(base["modalidade_norm"].eq("ARMAZENADOS")) & (base["flag_ativo"])].copy()
direto_loja = base[base["modalidade_norm"].eq("DIRETO LOJA")].copy()
ativos_sem_estoque = base[base["flag_ativo_sem_estoque"]].copy()


# =========================================================
# KPIs DIRETORIA
# =========================================================
q_ativos = ativos_venda["codigo"].nunique()
q_ativos_estoque = ativos_com_estoque["codigo"].nunique()
q_ativos_apanha = ativos_com_apanha["codigo"].nunique()
q_ativos_efetivos = ativos_efetivos["codigo"].nunique()
q_cross_desvio = cross_armazenado["codigo"].nunique()

conv_ativo_apanha = (q_ativos_apanha / q_ativos * 100) if q_ativos else 0
conv_ativo_venda = (q_ativos_efetivos / q_ativos * 100) if q_ativos else 0
conv_estoque_apanha = (q_ativos_apanha / q_ativos_estoque * 100) if q_ativos_estoque else 0

k1, k2, k3, k4 = st.columns(4)
with k1:
    kpi_card("Ativos p/ Venda", br_int(q_ativos), "Base ativa comercial")
with k2:
    kpi_card("Cross", br_int(q_cross_desvio), "Desvio da estratégia de cross docking")
with k3:
    kpi_card("Ativos eficazes", br_int(q_ativos_efetivos), f"Conversão ativo → venda: {pct(conv_ativo_venda)}")
with k4:
    kpi_card("Ativos com apanha", br_int(q_ativos_apanha), f"Conversão ativo → apanha: {pct(conv_ativo_apanha)}")

k5, k6, k7, k8 = st.columns(4)
with k5:
    kpi_card("Ativos com estoque", br_int(q_ativos_estoque), f"{pct((q_ativos_estoque / q_ativos * 100) if q_ativos else 0)} da base ativa")
with k6:
    kpi_card("Inativos p/ Compra", br_int(inativos_compra["codigo"].nunique()), "Inativos ainda com pendência de compra")
with k7:
    kpi_card("Inativos com estoque", br_int(inativos_estoque["codigo"].nunique()), f"Estoque físico: {br_num(inativos_estoque['estoque_cd'].sum())}")
with k8:
    kpi_card("Ativos sem apanha", br_int(sem_apanha["codigo"].nunique()), f"Conversão estoque → apanha: {pct(conv_estoque_apanha)}")

st.markdown(
    """
    <div class="helper-box">
        <b>Conceitos do painel.</b> <b>Cross</b> representa SKU de cross docking com sinal de armazenagem no CD
        (estoque/endereço/posição), ou seja, desvio da estratégia de cross. <b>Ativos eficazes</b> representa SKU ativo,
        com estoque, com apanha e com sinal de saída recente disponível na base atual.
    </div>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# FUNIL EXECUTIVO
# =========================================================
st.markdown("## Funil de eficiência do ativo")
funil = pd.DataFrame(
    {
        "etapa": ["Ativos p/ Venda", "Ativos com estoque", "Ativos com apanha", "Ativos eficazes"],
        "qtd_skus": [q_ativos, q_ativos_estoque, q_ativos_apanha, q_ativos_efetivos],
    }
)
funil["pct_base"] = np.where(funil["qtd_skus"].max() > 0, funil["qtd_skus"] / funil["qtd_skus"].max() * 100, 0)

fig_funil = go.Figure(
    go.Funnel(
        y=funil["etapa"],
        x=funil["qtd_skus"],
        text=[f"{br_int(v)} | {pct(p)}" for v, p in zip(funil["qtd_skus"], funil["pct_base"])],
        textposition="inside",
        marker={"color": ["#0F172A", "#334155", "#2563EB", "#16A34A"]},
        opacity=0.96,
        hovertemplate="<b>%{y}</b><br>Qtd SKUs: %{x:,.0f}<extra></extra>",
    )
)
fig_funil.update_layout(height=430, margin=dict(l=10, r=10, t=20, b=10))
st.plotly_chart(fig_funil, use_container_width=True)


# =========================================================
# HISTOGRAMAS COMPARATIVOS
# =========================================================
resumo = pd.DataFrame([
    {"categoria": "Inativos p/ Compra", "qtd_skus": inativos_compra["codigo"].nunique(), "qtd_estoque_cd": inativos_compra["estoque_cd"].sum()},
    {"categoria": "Ativos p/ Venda", "qtd_skus": ativos_venda["codigo"].nunique(), "qtd_estoque_cd": ativos_venda["estoque_cd"].sum()},
    {"categoria": "Inativos com Estoque", "qtd_skus": inativos_estoque["codigo"].nunique(), "qtd_estoque_cd": inativos_estoque["estoque_cd"].sum()},
    {"categoria": "Ativos com Estoque e sem Apanha", "qtd_skus": sem_apanha["codigo"].nunique(), "qtd_estoque_cd": sem_apanha["estoque_cd"].sum()},
    {"categoria": "Cross Docking", "qtd_skus": cross["codigo"].nunique(), "qtd_estoque_cd": cross["estoque_cd"].sum()},
    {"categoria": "Armazenados", "qtd_skus": armazenados["codigo"].nunique(), "qtd_estoque_cd": armazenados["estoque_cd"].sum()},
    {"categoria": "Direto Loja", "qtd_skus": direto_loja["codigo"].nunique(), "qtd_estoque_cd": direto_loja["estoque_cd"].sum()},
])

st.markdown("## Histogramas comparativos")
c1, c2 = st.columns(2)
mapa_cores = {
    "Inativos p/ Compra": "#991B1B",
    "Ativos p/ Venda": "#0F172A",
    "Inativos com Estoque": "#B45309",
    "Ativos com Estoque e sem Apanha": "#2563EB",
    "Cross Docking": "#F97316",
    "Armazenados": "#334155",
    "Direto Loja": "#0EA5E9",
}
with c1:
    fig_skus = px.bar(
        resumo,
        x="categoria",
        y="qtd_skus",
        text_auto=True,
        color="categoria",
        color_discrete_map=mapa_cores,
    )
    fig_skus.update_traces(textposition="outside", cliponaxis=False)
    fig_skus.update_layout(height=460, xaxis_title="", yaxis_title="Qtd SKUs", showlegend=False, margin=dict(t=40, b=20, l=20, r=20))
    st.plotly_chart(fig_skus, use_container_width=True)

with c2:
    fig_est = px.bar(
        resumo,
        x="categoria",
        y="qtd_estoque_cd",
        text_auto=".2s",
        color="categoria",
        color_discrete_map=mapa_cores,
    )
    fig_est.update_traces(textposition="outside", cliponaxis=False)
    fig_est.update_layout(height=460, xaxis_title="", yaxis_title="Estoque CD", showlegend=False, margin=dict(t=40, b=20, l=20, r=20))
    st.plotly_chart(fig_est, use_container_width=True)


# =========================================================
# COMPARATIVO POR MODALIDADE
# =========================================================
st.markdown("## Comparativo por modalidade operacional")
mod = (
    base.groupby("modalidade_norm", as_index=False)
    .agg(
        qtd_skus=("codigo", "nunique"),
        qtd_estoque_cd=("estoque_cd", "sum"),
        qtd_sem_apanha=("flag_sem_apanha", "sum"),
    )
    .sort_values(["qtd_skus", "qtd_estoque_cd"], ascending=[False, False])
)

fig_mod = px.bar(
    mod,
    x="modalidade_norm",
    y=["qtd_skus", "qtd_sem_apanha"],
    barmode="group",
    text_auto=True,
    labels={"value": "Qtd", "modalidade_norm": "Modalidade", "variable": "Indicador"},
    color_discrete_sequence=["#334155", "#2563EB"],
)
fig_mod.update_traces(textposition="outside", cliponaxis=False)
fig_mod.update_layout(height=500, xaxis_title="", yaxis_title="SKUs", legend_title_text="Indicador", margin=dict(t=40, b=20, l=20, r=20))
st.plotly_chart(fig_mod, use_container_width=True)
st.dataframe(mod, use_container_width=True, hide_index=True)


# =========================================================
# HISTOGRAMA POR LINHA — V3
# =========================================================
st.markdown("## Histograma por linha e modalidade executiva")

linha_hist = pd.concat([
    armazenados.assign(status_hist="Armazenado"),
    cross_armazenado.assign(status_hist="Cross Armazenado"),
    cross_sem_estoque.assign(status_hist="Cross sem Estoque"),
    ativos_sem_estoque.assign(status_hist="Ativo sem Estoque"),
], ignore_index=True)

if linha_hist.empty:
    st.info("Não há dados suficientes para montar o histograma por linha.")
else:
    linha_hist_resumo = (
        linha_hist.groupby(["linha_exec", "status_hist"], as_index=False)
        .agg(qtd_skus=("codigo", "nunique"))
    )

    ordem_status = ["Armazenado", "Cross Armazenado", "Cross sem Estoque", "Ativo sem Estoque"]
    paleta = {
        "Armazenado": "#334155",
        "Cross Armazenado": "#F97316",
        "Cross sem Estoque": "#EAB308",
        "Ativo sem Estoque": "#2563EB",
    }

    linha_pivot = linha_hist_resumo.pivot_table(
        index="linha_exec",
        columns="status_hist",
        values="qtd_skus",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()

    for col in ordem_status:
        if col not in linha_pivot.columns:
            linha_pivot[col] = 0

    linha_pivot["qtd_critica_total"] = linha_pivot[ordem_status].sum(axis=1)
    total_critico_cd = max(float(linha_pivot["qtd_critica_total"].sum()), 1.0)

    # Score corrigido: 60% volume absoluto no CD + 40% composição crítica interna
    linha_pivot["pct_linha_critica"] = np.where(
        linha_pivot["qtd_critica_total"] > 0,
        (linha_pivot["Armazenado"] + linha_pivot["Cross Armazenado"]) / linha_pivot["qtd_critica_total"] * 100,
        0,
    )
    linha_pivot["peso_volume_cd"] = linha_pivot["qtd_critica_total"] / total_critico_cd * 100
    linha_pivot["score_criticidade"] = (linha_pivot["pct_linha_critica"] * 0.4) + (linha_pivot["peso_volume_cd"] * 0.6)
    linha_pivot["semaforo"] = linha_pivot["score_criticidade"].apply(semaforo_html)
    linha_pivot = linha_pivot.sort_values(["qtd_critica_total", "score_criticidade"], ascending=[False, False])

    ordem_linhas = linha_pivot["linha_exec"].tolist()

    linha_hist_resumo = linha_hist_resumo.merge(
        linha_pivot[["linha_exec", "qtd_critica_total", "score_criticidade", "semaforo"]],
        on="linha_exec",
        how="left",
    )
    linha_hist_resumo["pct_linha"] = np.where(
        linha_hist_resumo["qtd_critica_total"] > 0,
        linha_hist_resumo["qtd_skus"] / linha_hist_resumo["qtd_critica_total"] * 100,
        0,
    )
    linha_hist_resumo["label_barra"] = linha_hist_resumo["qtd_skus"].astype(int).astype(str)

    fig_linha = px.bar(
        linha_hist_resumo,
        x="linha_exec",
        y="qtd_skus",
        color="status_hist",
        barmode="stack",
        category_orders={"linha_exec": ordem_linhas, "status_hist": ordem_status},
        color_discrete_map=paleta,
        labels={"linha_exec": "Linha", "qtd_skus": "Qtd SKUs", "status_hist": "Modalidade executiva"},
        text="label_barra",
        custom_data=["pct_linha", "qtd_critica_total", "score_criticidade", "semaforo"],
    )

    fig_linha.update_traces(
        textposition="inside",
        textfont_size=12,
        hovertemplate=(
            "<b>Linha:</b> %{x}"
            "<br><b>Status:</b> %{fullData.name}"
            "<br><b>Qtd SKUs:</b> %{y:,.0f}"
            "<br><b>% da criticidade da linha:</b> %{customdata[0]:.1f}%"
            "<br><b>Total crítico da linha:</b> %{customdata[1]:,.0f}"
            "<br><b>Score criticidade:</b> %{customdata[2]:.1f}"
            "<br><b>Semáforo:</b> %{customdata[3]}"
            "<extra></extra>"
        ),
    )
    fig_linha.update_layout(
        height=860,
        xaxis_title="",
        yaxis_title="Qtd SKUs",
        legend_title_text="Modalidade executiva",
        xaxis_tickangle=70,
        font=dict(size=14),
        margin=dict(t=30, b=120, l=30, r=30),
        hoverlabel=dict(font_size=14),
    )
    st.plotly_chart(fig_linha, use_container_width=True)

    st.markdown("### Semáforo de criticidade por linha")
    semaforo_view = linha_pivot[["linha_exec", "qtd_critica_total", "pct_linha_critica", "peso_volume_cd", "score_criticidade", "semaforo"]].copy()
    semaforo_view.columns = ["Linha", "Qtd crítica total", "% crítica interna", "% peso no CD", "Score criticidade", "Semáforo"]
    semaforo_view["% crítica interna"] = semaforo_view["% crítica interna"].map(pct)
    semaforo_view["% peso no CD"] = semaforo_view["% peso no CD"].map(pct)
    semaforo_view["Score criticidade"] = semaforo_view["Score criticidade"].map(lambda v: f"{v:.1f}")
    st.dataframe(semaforo_view, use_container_width=True, hide_index=True)


# =========================================================
# RANKINGS E RECOMENDAÇÕES — V3
# =========================================================
st.markdown("## Ranking executivo e ação recomendada")

ranking_linhas = (
    linha_pivot[["linha_exec", "qtd_critica_total", "score_criticidade", "semaforo"]]
    .rename(columns={"linha_exec": "linha"})
    .copy()
    if not linha_hist.empty
    else pd.DataFrame(columns=["linha", "qtd_critica_total", "score_criticidade", "semaforo"])
)

if not ranking_linhas.empty:
    ranking_linhas["acao_recomendada"] = np.select(
        [
            ranking_linhas["score_criticidade"].astype(float).ge(60),
            ranking_linhas["score_criticidade"].astype(float).ge(35),
        ],
        [
            "Executar plano de ataque da linha / revisar apanha / expurgar cross armazenado",
            "Revisar endereçamento e abastecimento / tratar ativos sem estoque",
        ],
        default="Monitorar e manter governança",
    )
    st.dataframe(
        ranking_linhas.sort_values(["qtd_critica_total", "score_criticidade"], ascending=[False, False]).head(top_n),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("Sem base suficiente para ranking executivo por linha.")


# =========================================================
# DETALHAMENTO
# =========================================================
st.markdown("## Detalhamento gerencial")
aba1, aba2, aba3, aba4 = st.tabs([
    "Inativos para compra",
    "Ativos sem apanha",
    "Cross Docking",
    "Efetividade do ativo",
])

with aba1:
    top_inativos = (
        inativos_compra.groupby(["fornecedor", "linha_exec"], as_index=False)
        .agg(qtd_skus=("codigo", "nunique"), qtd_estoque_cd=("estoque_cd", "sum"))
        .sort_values(["qtd_skus", "qtd_estoque_cd"], ascending=[False, False])
        .head(top_n)
    )
    st.dataframe(top_inativos, use_container_width=True, hide_index=True)
    st.dataframe(inativos_compra.sort_values(["estoque_cd"], ascending=False).head(300), use_container_width=True, hide_index=True)

with aba2:
    sem_apanha_resumo = (
        sem_apanha.groupby("modalidade_norm", as_index=False)
        .agg(qtd_skus=("codigo", "nunique"), qtd_estoque_cd=("estoque_cd", "sum"))
        .sort_values(["qtd_skus", "qtd_estoque_cd"], ascending=[False, False])
    )
    st.dataframe(sem_apanha_resumo, use_container_width=True, hide_index=True)
    st.dataframe(sem_apanha.sort_values(["estoque_cd"], ascending=False).head(300), use_container_width=True, hide_index=True)

with aba3:
    cross_resumo = (
        cross.assign(cross_armazenado=lambda x: x["flag_cross_armazenado"].map({True: "SIM", False: "NAO"}))
        .groupby(["cross_armazenado", "flag_com_estoque"], as_index=False)
        .agg(qtd_skus=("codigo", "nunique"), qtd_estoque_cd=("estoque_cd", "sum"))
        .sort_values(["qtd_skus", "qtd_estoque_cd"], ascending=[False, False])
    )
    cross_resumo["flag_com_estoque"] = cross_resumo["flag_com_estoque"].map({True: "Com estoque", False: "Sem estoque"})
    st.dataframe(cross_resumo, use_container_width=True, hide_index=True)
    st.dataframe(cross.sort_values(["estoque_cd"], ascending=False).head(300), use_container_width=True, hide_index=True)

with aba4:
    efetividade = pd.DataFrame([
        {"Indicador": "Ativos p/ Venda", "Qtd SKUs": q_ativos, "% da base": 100.0 if q_ativos else 0},
        {"Indicador": "Ativos com estoque", "Qtd SKUs": q_ativos_estoque, "% da base": (q_ativos_estoque / q_ativos * 100) if q_ativos else 0},
        {"Indicador": "Ativos com apanha", "Qtd SKUs": q_ativos_apanha, "% da base": conv_ativo_apanha},
        {"Indicador": "Ativos eficazes", "Qtd SKUs": q_ativos_efetivos, "% da base": conv_ativo_venda},
    ])
    efetividade["% da base"] = efetividade["% da base"].map(pct)
    st.dataframe(efetividade, use_container_width=True, hide_index=True)

    ofensores = ativos_venda.copy()
    ofensores["status_exec"] = np.select(
        [
            ofensores["flag_ativo_efetivo"],
            ofensores["flag_sem_apanha"],
            ofensores["flag_ativo_sem_estoque"],
        ],
        [
            "Ativo Eficaz",
            "Ativo sem Apanha",
            "Ativo sem Estoque",
        ],
        default="Ativo com Ajuste",
    )
    ranking = (
        ofensores.groupby(["linha_exec", "status_exec"], as_index=False)
        .agg(qtd_skus=("codigo", "nunique"))
        .sort_values(["qtd_skus"], ascending=False)
        .head(60)
    )
    st.dataframe(ranking, use_container_width=True, hide_index=True)


# =========================================================
# INSIGHTS AUTOMÁTICOS
# =========================================================
st.markdown("## Insights automáticos")
i1, i2, i3 = st.columns(3)

with i1:
    insight_card(
        f"<b>Base ativa comercial:</b> {br_int(q_ativos)} SKUs. "
        f"{br_int(q_ativos_estoque)} têm estoque e {br_int(q_ativos_apanha)} já estão com apanha.",
        "normal",
    )

with i2:
    tone = "risk" if conv_ativo_venda < 20 else "warn" if conv_ativo_venda < 40 else "good"
    insight_card(
        f"<b>Ativos eficazes:</b> {br_int(q_ativos_efetivos)} SKUs, "
        f"representando <b>{pct(conv_ativo_venda)}</b> da base ativa.",
        tone,
    )

with i3:
    tone = "risk" if q_cross_desvio > 0 else "good"
    insight_card(
        f"<b>Cross com desvio:</b> {br_int(q_cross_desvio)} SKUs com sinal de armazenagem, "
        f"o que indica desvio da estratégia de cross docking.",
        tone,
    )

st.caption(
    "Regras aplicadas: Cross Docking normal e Comercial entram em um único grupo quando a modalidade contém 'CROSS'. "
    "Armazenados excluem CROSS e DIRETO LOJA. Ativos sem apanha = item ativo, com estoque no CD e ENDERECADO = NAO. "
    "Ativos eficazes = item ativo, com estoque, apanha e sinal de saída recente disponível na raw_plan_06 desta versão. "
    "No histograma por linha: Armazenado = cinza escuro, Cross Armazenado = laranja, Cross sem Estoque = amarelo, Ativo sem Estoque = azul."
)

