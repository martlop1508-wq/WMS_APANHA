import os
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text


# ============================================================
# CONFIG
# ============================================================
st.set_page_config(
    page_title="WMS V23 - Governança de Norma",
    page_icon="📦",
    layout="wide",
)

st.title("📦 WMS V23 — Dashboard Executivo de Governança de Norma")
st.caption(
    "Visão diretoria: impacto financeiro, ruptura, economia operacional, "
    "governança de dados e execução de norma."
)


# ============================================================
# DB
# ============================================================
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
def read_sql_df(sql, params=None):
    engine = get_engine()
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


# ============================================================
# QUERIES
# ============================================================
BASE_SQL = """
SELECT
    NROEMPRESA,
    SEQPRODUTO,
    DESCCOMPLETA,
    DEP,
    CODRUA,
    NROPREDIO,
    NROAPARTAMENTO,
    NROSALA,
    LINHA,
    visitas,
    volumes,
    media_dia_cx,
    norma_atual,
    norma_sugerida,
    repos_simulada,
    repos_real_30d,
    desvio,
    status_operacao,
    ganho_potencial_repos,
    status_layout,
    acao_recomendada,
    justificativa_automatica,
    score_decisao,
    reposicoes_evitar_30d,
    minutos_evitados_30d,
    horas_evitadas_30d,
    custo_operacional_evitar_30d,
    status_validacao_norma
FROM vw_motor_decisao_v22_2_validado
WHERE 1=1
"""


# ============================================================
# REGRAS AJUSTADAS
# ============================================================
def classificar_governanca(row):
    """
    Ajuste conceitual:
    - SEM_NORMA_SISTEMA não significa necessariamente ausência de norma no negócio
    - Pode ser apenas ausência de carga ETL das tabelas auxiliares de norma
    """
    status_valid = str(row.get("status_validacao_norma", "") or "").strip()
    norma_atual = row.get("norma_atual")
    acao = str(row.get("acao_recomendada", "") or "").strip()

    if status_valid == "SEM_NORMA_SISTEMA":
        if pd.notna(norma_atual) and float(norma_atual or 0) > 0:
            return "SEM_CARGA_ETL_NORMA"
        return "REVISAR_PARAMETRIZACAO_OPERACIONAL"

    if status_valid == "DIVERGENTE_LAYOUT":
        return "DIVERGENCIA_NORMA"

    if acao == "INVESTIGAR_RUPTURA":
        return "ATENCAO_OPERACIONAL"

    return "OK"


def classificar_prioridade(v):
    if pd.isna(v):
        return "BAIXA"
    if v > 1000:
        return "CRITICA"
    if v > 300:
        return "ALTA"
    if v > 100:
        return "MEDIA"
    return "BAIXA"


def classificar_tipo_execucao(row):
    governanca = str(row.get("status_governanca", "") or "")
    acao = str(row.get("acao_recomendada", "") or "")

    if governanca == "SEM_CARGA_ETL_NORMA" and acao in ("AUMENTAR_NORMA", "REDUZIR_NORMA"):
        return "CRIAR_CARGA_ETL_NORMA"

    if governanca == "DIVERGENCIA_NORMA":
        return "VALIDAR_DIVERGENCIA"

    if governanca == "REVISAR_PARAMETRIZACAO_OPERACIONAL":
        return "REVISAR_CADASTRO"

    if acao == "REENDERECAR":
        return "REENDERECAR"

    if acao == "INVESTIGAR_RUPTURA":
        return "INVESTIGAR"

    return "MANTER"


def ajustar_dataframe(df):
    df = df.copy()

    for col in [
        "visitas", "volumes", "media_dia_cx", "norma_atual", "norma_sugerida",
        "repos_simulada", "repos_real_30d", "desvio", "ganho_potencial_repos",
        "score_decisao", "reposicoes_evitar_30d", "minutos_evitados_30d",
        "horas_evitadas_30d", "custo_operacional_evitar_30d"
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["status_governanca"] = df.apply(classificar_governanca, axis=1)
    df["prioridade_execucao"] = df["custo_operacional_evitar_30d"].apply(classificar_prioridade)
    df["tipo_execucao"] = df.apply(classificar_tipo_execucao, axis=1)

    return df


def money(v):
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


# ============================================================
# LOAD BASE
# ============================================================
try:
    df_raw = read_sql_df(BASE_SQL)
except Exception as e:
    st.error(f"Erro ao ler vw_motor_decisao_v22_2_validado: {e}")
    st.stop()

df_raw = ajustar_dataframe(df_raw)


# ============================================================
# SIDEBAR
# ============================================================
st.sidebar.header("Filtros")

empresas = sorted([str(x) for x in df_raw["NROEMPRESA"].dropna().astype(str).unique().tolist()])
empresa_sel = st.sidebar.multiselect(
    "Empresa",
    options=empresas,
    default=empresas[:1] if empresas else []
)

acoes = sorted(df_raw["acao_recomendada"].dropna().astype(str).unique().tolist())
acao_sel = st.sidebar.multiselect(
    "Ação recomendada",
    options=acoes,
    default=acoes
)

governancas = sorted(df_raw["status_governanca"].dropna().astype(str).unique().tolist())
governanca_sel = st.sidebar.multiselect(
    "Status governança",
    options=governancas,
    default=governancas
)

validacoes = sorted(df_raw["status_validacao_norma"].dropna().astype(str).unique().tolist())
valid_sel = st.sidebar.multiselect(
    "Status bruto da validação",
    options=validacoes,
    default=validacoes
)

linhas = sorted(df_raw["LINHA"].dropna().astype(str).unique().tolist())
linha_sel = st.sidebar.multiselect(
    "Linha",
    options=linhas,
    default=[]
)

prioridades = ["CRITICA", "ALTA", "MEDIA", "BAIXA"]
prioridade_sel = st.sidebar.multiselect(
    "Prioridade",
    options=prioridades,
    default=prioridades
)

texto_busca = st.sidebar.text_input("Buscar SKU / descrição")


# ============================================================
# FILTER
# ============================================================
df = df_raw.copy()

if empresa_sel:
    df = df[df["NROEMPRESA"].astype(str).isin(empresa_sel)]

if acao_sel:
    df = df[df["acao_recomendada"].astype(str).isin(acao_sel)]

if governanca_sel:
    df = df[df["status_governanca"].astype(str).isin(governanca_sel)]

if valid_sel:
    df = df[df["status_validacao_norma"].astype(str).isin(valid_sel)]

if linha_sel:
    df = df[df["LINHA"].astype(str).isin(linha_sel)]

if prioridade_sel:
    df = df[df["prioridade_execucao"].astype(str).isin(prioridade_sel)]

if texto_busca:
    t = texto_busca.strip().lower()
    df = df[
        df["SEQPRODUTO"].astype(str).str.lower().str.contains(t, na=False)
        | df["DESCCOMPLETA"].astype(str).str.lower().str.contains(t, na=False)
    ]


# ============================================================
# KPIS
# ============================================================
total_skus = len(df)
impacto_total = float(df["custo_operacional_evitar_30d"].fillna(0).sum())
horas_total = float(df["horas_evitadas_30d"].fillna(0).sum())
ruptura_qtd = int((df["acao_recomendada"] == "INVESTIGAR_RUPTURA").sum())
sem_carga_etl_qtd = int((df["status_governanca"] == "SEM_CARGA_ETL_NORMA").sum())
divergencia_norma_qtd = int((df["status_governanca"] == "DIVERGENCIA_NORMA").sum())

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("SKUs no recorte", f"{total_skus:,}".replace(",", "."))
c2.metric("Impacto potencial", money(impacto_total))
c3.metric("Horas evitáveis", f"{horas_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
c4.metric("Investigar ruptura", f"{ruptura_qtd:,}".replace(",", "."))
c5.metric("Sem carga ETL norma", f"{sem_carga_etl_qtd:,}".replace(",", "."))
c6.metric("Divergência norma", f"{divergencia_norma_qtd:,}".replace(",", "."))

st.info(
    "Ajuste conceitual aplicado: ausência de registro nas tabelas auxiliares de norma "
    "não é mais tratada automaticamente como 'sem norma no sistema'. "
    "Quando existe norma_atual no layout e não existe carga nas tabelas auxiliares, "
    "o dashboard classifica como 'SEM_CARGA_ETL_NORMA'."
)

st.divider()


# ============================================================
# RESUMOS
# ============================================================
left, right = st.columns((1, 1))

with left:
    st.subheader("Resumo por ação recomendada")
    resumo_acao = (
        df.groupby("acao_recomendada", dropna=False)
        .agg(
            qtd_skus=("SEQPRODUTO", "count"),
            horas_evitadas_30d=("horas_evitadas_30d", "sum"),
            custo_operacional_evitar_30d=("custo_operacional_evitar_30d", "sum"),
        )
        .reset_index()
        .sort_values(["custo_operacional_evitar_30d", "qtd_skus"], ascending=[False, False])
    )
    st.dataframe(resumo_acao, use_container_width=True, hide_index=True)

with right:
    st.subheader("Resumo por governança")
    resumo_governanca = (
        df.groupby("status_governanca", dropna=False)
        .agg(
            qtd_skus=("SEQPRODUTO", "count"),
            horas_evitadas_30d=("horas_evitadas_30d", "sum"),
            custo_operacional_evitar_30d=("custo_operacional_evitar_30d", "sum"),
        )
        .reset_index()
        .sort_values(["custo_operacional_evitar_30d", "qtd_skus"], ascending=[False, False])
    )
    st.dataframe(resumo_governanca, use_container_width=True, hide_index=True)

st.divider()


# ============================================================
# TABS
# ============================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Top Economia",
    "Top Ruptura",
    "Governança",
    "Plano de Execução",
    "Base Analítica",
])

with tab1:
    st.subheader("Top economia operacional")
    top_economia = df[df["acao_recomendada"].isin(["AUMENTAR_NORMA", "REDUZIR_NORMA", "REENDERECAR"])].copy()
    top_economia = top_economia.sort_values(
        ["custo_operacional_evitar_30d", "horas_evitadas_30d", "score_decisao"],
        ascending=[False, False, False],
    )
    cols = [
        "NROEMPRESA", "SEQPRODUTO", "DESCCOMPLETA", "CODRUA", "NROPREDIO",
        "NROAPARTAMENTO", "NROSALA", "LINHA", "norma_atual", "norma_sugerida",
        "acao_recomendada", "prioridade_execucao", "reposicoes_evitar_30d",
        "horas_evitadas_30d", "custo_operacional_evitar_30d", "justificativa_automatica",
    ]
    st.dataframe(top_economia[cols].head(100), use_container_width=True, hide_index=True)

with tab2:
    st.subheader("Top ruptura para investigação")
    top_ruptura = df[df["acao_recomendada"] == "INVESTIGAR_RUPTURA"].copy()
    top_ruptura["abs_desvio"] = top_ruptura["desvio"].abs()
    top_ruptura = top_ruptura.sort_values(["abs_desvio", "score_decisao"], ascending=[False, False])
    cols = [
        "NROEMPRESA", "SEQPRODUTO", "DESCCOMPLETA", "CODRUA", "NROPREDIO",
        "NROAPARTAMENTO", "NROSALA", "LINHA", "media_dia_cx", "repos_simulada",
        "repos_real_30d", "desvio", "status_governanca", "prioridade_execucao",
        "custo_operacional_evitar_30d", "justificativa_automatica",
    ]
    st.dataframe(top_ruptura[cols].head(100), use_container_width=True, hide_index=True)

with tab3:
    st.subheader("Governança e divergência")
    gov = df.copy()
    gov = gov.sort_values(
        ["status_governanca", "custo_operacional_evitar_30d", "score_decisao"],
        ascending=[True, False, False],
    )
    cols = [
        "NROEMPRESA", "SEQPRODUTO", "DESCCOMPLETA", "CODRUA", "NROPREDIO",
        "NROAPARTAMENTO", "NROSALA", "norma_atual", "status_validacao_norma",
        "status_governanca", "acao_recomendada", "tipo_execucao", "justificativa_automatica",
    ]
    st.dataframe(gov[cols].head(300), use_container_width=True, hide_index=True)

with tab4:
    st.subheader("Plano de execução V23")
    plano = df[df["acao_recomendada"] != "MANTER"].copy()
    plano = plano.sort_values(["custo_operacional_evitar_30d", "score_decisao"], ascending=[False, False])
    cols = [
        "NROEMPRESA", "SEQPRODUTO", "DESCCOMPLETA", "CODRUA", "NROPREDIO",
        "NROAPARTAMENTO", "NROSALA", "norma_atual", "norma_sugerida",
        "acao_recomendada", "tipo_execucao", "prioridade_execucao",
        "status_governanca", "score_decisao", "custo_operacional_evitar_30d",
        "justificativa_automatica",
    ]
    st.dataframe(plano[cols].head(300), use_container_width=True, hide_index=True)

    csv_bytes = plano[cols].to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "Baixar plano de execução CSV",
        data=csv_bytes,
        file_name="plano_execucao_norma_v23_ajustado.csv",
        mime="text/csv",
    )

with tab5:
    st.subheader("Base analítica filtrada")
    st.dataframe(df, use_container_width=True, hide_index=True)

st.divider()
st.caption(
    "Fontes principais: vw_motor_decisao_v22_2_validado, vw_mapa_inteligente_v22_tri, "
    "vw_reposicao_real_resumo_30d e validação de norma com fallback do layout. "
    "Ajuste semântico aplicado para diferenciar ausência de carga ETL de ausência real de norma operacional."
)

