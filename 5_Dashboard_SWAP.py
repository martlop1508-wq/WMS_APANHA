# /opt/wms_apanha/web/pages/5_Dashboard_SWAP.py
# Painel SWAP Inteligente (P/Q/R) — EXECUTIVO / GESTÃO (DIFERENTE do 2_Sugestoes)
# - KPIs + Semáforo P/Q/R (endereços e produtos)
# - Top 20 Rua / DEP
# - P→R como padrão do sistema
# - Filtros com Aplicar/Limpar (sem aplicar = padrão)
# - SELECT dinâmico (evita 1054) + fallback colunas (evita KeyError)

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd
import streamlit as st
import mysql.connector


# =========================================================
# DB
# =========================================================
def get_db_conn():
    cfg = st.secrets.get("mysql", {})
    host = cfg.get("host", "localhost")
    user = cfg.get("user", "wms_user")
    password = cfg.get("password", "")
    database = cfg.get("database", "wms_apanha")
    port = int(cfg.get("port", 3306))

    return mysql.connector.connect(
        host=host,
        user=user,
        password=password,
        database=database,
        port=port,
        autocommit=True,
    )


@st.cache_data(ttl=120, show_spinner=False)
def fetch_df(query: str, params: Optional[tuple] = None) -> pd.DataFrame:
    cnx = get_db_conn()
    try:
        cur = cnx.cursor()
        cur.execute(query, params or ())
        cols = [c[0] for c in cur.description] if cur.description else []
        rows = cur.fetchall() if cur.description else []
        return pd.DataFrame(rows, columns=cols)
    finally:
        try:
            cnx.close()
        except Exception:
            pass


@st.cache_data(ttl=300, show_spinner=False)
def table_exists(table_name: str) -> bool:
    q = """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name = %s
        LIMIT 1
    """
    df = fetch_df(q, (table_name,))
    return not df.empty


@st.cache_data(ttl=300, show_spinner=False)
def get_table_columns(table_name: str) -> set:
    q = """
        SELECT UPPER(COLUMN_NAME) AS column_name
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
    """
    df = fetch_df(q, (table_name,))
    if df.empty:
        return set()
    return set(df["column_name"].astype(str).str.upper().tolist())


def pick_col(cols: set, candidates: List[str]) -> Optional[str]:
    up = {c.upper(): c for c in cols}
    for cand in candidates:
        if cand.upper() in up:
            return up[cand.upper()]
    return None


# =========================================================
# HELPERS / NORMALIZAÇÃO
# =========================================================
def norm_cd(x) -> str:
    if x is None:
        return ""
    return str(x).strip()


def norm_dep(x) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    return s.zfill(2) if s.isdigit() else s


def norm_rua(x) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    return s.zfill(3) if s.isdigit() else s


def norm_int(x) -> Optional[int]:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    try:
        return int(str(x).strip())
    except Exception:
        return None


def to_float_series(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .replace("None", "0")
        .replace("nan", "0")
        .astype(float)
    )


def addr_str(rua: str, pred: Optional[int], ap: Optional[int], sl: Optional[int]) -> str:
    r = (rua or "").strip()
    p = "" if pred is None else str(pred).zfill(2)
    a = "" if ap is None else str(ap).zfill(1)
    s = "" if sl is None else str(sl).zfill(1)
    parts = [r, p, a, s]
    parts = [x for x in parts if x != ""]
    return "-".join(parts)


def derive_rua_from_endereco(endereco: str) -> str:
    if not endereco:
        return ""
    s = str(endereco).strip()
    for sep in ["-", " ", ".", "/"]:
        if sep in s:
            s = s.split(sep)[0]
            break
    s2 = "".join([ch for ch in s if ch.isdigit()]) or s
    return norm_rua(s2)


def fmt_int(x: int) -> str:
    return f"{int(x):,}".replace(",", ".")


def pct(x: float) -> str:
    return f"{x:.1f}%".replace(".", ",")


def curva_from_value(v: float, p_cut: float, q_cut: float) -> str:
    if v >= p_cut:
        return "P"
    if v >= q_cut:
        return "Q"
    return "R"


# =========================================================
# CSS (visual mais “dashboard”)
# =========================================================
def css():
    st.markdown(
        """
<style>
/* layout geral */
.block-container{padding-top:1.2rem;padding-bottom:2rem}
h1,h2,h3{letter-spacing:-0.02em}

/* badges */
.badge{display:inline-block;padding:3px 10px;border-radius:999px;border:1px solid #e5e7eb;background:#f8fafc;font-size:12px}
.badge-p{background:#dcfce7;border-color:#86efac;color:#166534;font-weight:800}
.badge-q{background:#fef9c3;border-color:#fde047;color:#854d0e;font-weight:800}
.badge-r{background:#fee2e2;border-color:#fca5a5;color:#7f1d1d;font-weight:800}

/* cards */
.card{padding:14px;border:1px solid #eee;border-radius:16px;background:#fff}
.card-title{font-size:12px;color:#6b7280;margin-bottom:4px}
.card-value{font-size:26px;font-weight:900;margin-bottom:6px}
.card-sub{font-size:12px;color:#6b7280}

/* semáforos */
.semaforo{display:flex;gap:10px;flex-wrap:wrap}
.box{min-width:140px;padding:10px 12px;border-radius:14px;border:1px solid #eee;background:#fff}
.box .t{font-size:12px;color:#6b7280}
.box .v{font-size:22px;font-weight:900;margin-top:2px}
.boxP{background:#dcfce7;border-color:#86efac}
.boxQ{background:#fef9c3;border-color:#fde047}
.boxR{background:#fee2e2;border-color:#fca5a5}

/* status */
.status{padding:10px 12px;border-radius:14px;border:1px solid #e5e7eb;background:#f8fafc}
.small{font-size:12px;color:#6b7280}
</style>
        """,
        unsafe_allow_html=True,
    )


def curva_css(val: str) -> str:
    v = str(val).upper().strip()
    if v == "P":
        return "background-color:#dcfce7;color:#166534;font-weight:800"
    if v == "Q":
        return "background-color:#fef9c3;color:#854d0e;font-weight:800"
    if v == "R":
        return "background-color:#fee2e2;color:#7f1d1d;font-weight:800"
    return ""


# =========================================================
# LOAD BASE (dinâmico / robusto)
# =========================================================
@st.cache_data(ttl=60, show_spinner=False)
def load_base(cd: str, dep: Optional[str], rua: Optional[str], limit: int) -> pd.DataFrame:
    view = "app_layout_visitas_atual" if table_exists("app_layout_visitas_atual") else "gp00_layout_visitas"
    cols = get_table_columns(view)

    col_cd = pick_col(cols, ["NROEMPRESA", "CD"])
    col_sku = pick_col(cols, ["SEQPRODUTO", "SKU", "CODPRODUTO"])
    col_desc = pick_col(cols, ["DESCCOMPLETA", "DESCRICAO", "DESC_PRODUTO"])
    col_dep = pick_col(cols, ["DEP", "CODLINHA", "LINHA", "LINHASEPARACAO"])
    col_rua = pick_col(cols, ["CODRUA", "RUA"])
    col_pred = pick_col(cols, ["NROPREDIO", "PRED", "PREDIO"])
    col_ap = pick_col(cols, ["NROAPARTAMENTO", "AP", "APARTAMENTO"])
    col_sl = pick_col(cols, ["NROSALA", "SL", "SALA"])
    col_endereco = pick_col(cols, ["ENDERECO", "END"])

    col_vis = pick_col(cols, ["VISITAS", "visitas"])
    col_vol = pick_col(cols, ["VOLUMES", "volumes"])
    col_mdcx = pick_col(cols, ["MEDIA_DIA_CX", "media_dia_cx", "MEDIA_DIARIA_CX"])

    if not col_cd:
        raise RuntimeError(f"Base {view} não tem CD/NROEMPRESA. Colunas (amostra): {sorted(list(cols))[:40]}")

    select_parts = [
        f"{col_cd} AS CD",
        f"{col_sku} AS SKU" if col_sku else "NULL AS SKU",
        f"{col_desc} AS DESCCOMPLETA" if col_desc else "NULL AS DESCCOMPLETA",
        f"{col_dep} AS DEP" if col_dep else "NULL AS DEP",
        f"{col_rua} AS RUA" if col_rua else "NULL AS RUA",
        f"{col_pred} AS PRED" if col_pred else "NULL AS PRED",
        f"{col_ap} AS AP" if col_ap else "NULL AS AP",
        f"{col_sl} AS SL" if col_sl else "NULL AS SL",
        f"{col_endereco} AS ENDERECO" if col_endereco else "NULL AS ENDERECO",
        f"{col_vis} AS VISITAS" if col_vis else "0 AS VISITAS",
        f"{col_vol} AS VOLUMES" if col_vol else "NULL AS VOLUMES",
        f"{col_mdcx} AS MEDIA_DIA_CX" if col_mdcx else "NULL AS MEDIA_DIA_CX",
    ]

    where = [f"{col_cd} = %s"]
    params: List = [cd]

    if rua and col_rua:
        where.append(f"{col_rua} = %s")
        params.append(norm_rua(rua))
    if dep and col_dep:
        where.append(f"{col_dep} = %s")
        params.append(norm_dep(dep))

    q = f"""
        SELECT {", ".join(select_parts)}
        FROM {view}
        WHERE {" AND ".join(where)}
        LIMIT {int(limit)}
    """

    df = fetch_df(q, tuple(params))
    if df.empty:
        return df

    df["CD"] = df["CD"].map(norm_cd)
    df["DEP"] = df["DEP"].map(norm_dep) if "DEP" in df.columns else ""
    df["RUA"] = df["RUA"].map(norm_rua) if "RUA" in df.columns else ""

    for c in ["PRED", "AP", "SL"]:
        df[c] = df[c].apply(norm_int) if c in df.columns else None

    if "ENDERECO" not in df.columns:
        df["ENDERECO"] = None

    # se ENDERECO vazio, monta
    if df["ENDERECO"].isna().all() or (df["ENDERECO"].astype(str).str.strip() == "None").all():
        df["ENDERECO"] = [
            addr_str(r, p, a, s) for r, p, a, s in zip(df["RUA"], df["PRED"], df["AP"], df["SL"])
        ]
    else:
        if df["RUA"].astype(str).str.strip().eq("").all():
            df["RUA"] = df["ENDERECO"].astype(str).apply(derive_rua_from_endereco)

    df["VISITAS"] = to_float_series(df["VISITAS"].fillna(0)) if "VISITAS" in df.columns else 0.0

    if "VOLUMES" in df.columns and df["VOLUMES"].notna().any():
        try:
            df["VOLUMES"] = to_float_series(df["VOLUMES"].fillna(0))
        except Exception:
            pass

    if "MEDIA_DIA_CX" in df.columns and df["MEDIA_DIA_CX"].notna().any():
        try:
            df["MEDIA_DIA_CX"] = to_float_series(df["MEDIA_DIA_CX"].fillna(0))
        except Exception:
            pass

    return df


def compute_curvas(df: pd.DataFrame) -> Tuple[pd.DataFrame, bool]:
    if df.empty:
        return df, False

    # Produto por VISITAS
    v = df["VISITAS"].fillna(0.0).astype(float)
    p_cut = float(v.quantile(0.80))
    q_cut = float(v.quantile(0.50))
    df["CURVA_PRODUTO"] = df["VISITAS"].apply(lambda x: curva_from_value(float(x or 0), p_cut, q_cut))

    # Endereço por soma VISITAS
    addr_key = ["CD", "DEP", "RUA", "PRED", "AP", "SL", "ENDERECO"]
    for c in addr_key:
        if c not in df.columns:
            df[c] = None

    addr_vis = df.groupby(addr_key, dropna=False)["VISITAS"].sum().reset_index(name="VIS_ADDR")
    pv = addr_vis["VIS_ADDR"].fillna(0.0)
    p2 = float(pv.quantile(0.80))
    q2 = float(pv.quantile(0.50))
    addr_vis["CURVA_ENDERECO"] = addr_vis["VIS_ADDR"].apply(lambda x: curva_from_value(float(x or 0), p2, q2))

    df = df.merge(addr_vis[addr_key + ["CURVA_ENDERECO"]], on=addr_key, how="left")
    df["CURVA_ENDERECO"] = df["CURVA_ENDERECO"].fillna("R")
    df["CURVA_PRODUTO"] = df["CURVA_PRODUTO"].fillna("R")

    # Autonomia (dias) se existir
    autonomia_ok = False
    df["AUTONOMIA_DIAS"] = pd.NA

    if "VOLUMES" in df.columns and "MEDIA_DIA_CX" in df.columns:
        try:
            vol = pd.to_numeric(df["VOLUMES"], errors="coerce")
            md = pd.to_numeric(df["MEDIA_DIA_CX"], errors="coerce")
            aut = (vol / md).replace([pd.NA, pd.NaT, float("inf")], pd.NA)
            df["AUTONOMIA_DIAS"] = aut
            autonomia_ok = df["AUTONOMIA_DIAS"].notna().any()
        except Exception:
            autonomia_ok = False

    if autonomia_ok:
        def curva_aut(x):
            if x is None or pd.isna(x):
                return None
            x = float(x)
            if x <= 3:
                return "P"
            if x <= 10:
                return "Q"
            return "R"

        df["CURVA_AUTONOMIA"] = df["AUTONOMIA_DIAS"].apply(curva_aut)
    else:
        df["CURVA_AUTONOMIA"] = None

    return df, autonomia_ok


def build_motivo(df: pd.DataFrame) -> pd.Series:
    cp = df["CURVA_PRODUTO"].astype(str).str.upper().str.strip()
    ce = df["CURVA_ENDERECO"].astype(str).str.upper().str.strip()

    out = []
    for a, b in zip(cp, ce):
        if a == "P" and b == "R":
            out.append("Produto P (Alto giro) em Endereço R")
        elif a == "P" and b == "Q":
            out.append("Produto P (Alto giro) em Endereço Q")
        elif a == "Q" and b == "R":
            out.append("Produto Q (Médio giro) em Endereço R")
        else:
            out.append("Outras")
    return pd.Series(out, index=df.index)


def apply_business(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    df["MOTIVO"] = build_motivo(df)

    cp = df["CURVA_PRODUTO"].astype(str).str.upper()
    ce = df["CURVA_ENDERECO"].astype(str).str.upper()

    score = pd.Series(0.0, index=df.index)
    score += ((cp == "P") & (ce == "R")).astype(float) * 4.0
    score += ((cp == "Q") & (ce == "R")).astype(float) * 3.0
    score += ((cp == "P") & (ce == "Q")).astype(float) * 2.0

    vmax = float(df["VISITAS"].max() if df["VISITAS"].max() else 1.0)
    score += (df["VISITAS"].fillna(0).astype(float) / vmax) * 1.5

    df["SCORE_SWAP"] = score.round(3)
    return df


def pqr_counts(series: pd.Series) -> Tuple[int, int, int]:
    s = series.astype(str).str.upper().str.strip()
    return int((s == "P").sum()), int((s == "Q").sum()), int((s == "R").sum())


# =========================================================
# FILTER STATE (Aplicar / Limpar)
# =========================================================
def init_filter_state(default_cd: str):
    if "dash_flt_applied" not in st.session_state:
        st.session_state.dash_flt_applied = False
    if "dash_cd" not in st.session_state:
        st.session_state.dash_cd = default_cd
    if "dash_dep" not in st.session_state:
        st.session_state.dash_dep = ""
    if "dash_rua" not in st.session_state:
        st.session_state.dash_rua = ""
    if "dash_sku" not in st.session_state:
        st.session_state.dash_sku = ""
    if "dash_limit" not in st.session_state:
        st.session_state.dash_limit = 60000


def get_effective_filters(default_cd: str):
    if not st.session_state.dash_flt_applied:
        return {"cd": default_cd, "dep": None, "rua": None, "sku": None, "limit": 60000}

    cd = (st.session_state.dash_cd or default_cd).strip()
    dep = st.session_state.dash_dep.strip() or None
    rua = st.session_state.dash_rua.strip() or None
    sku = st.session_state.dash_sku.strip() or None
    limit = int(st.session_state.dash_limit)

    return {"cd": cd, "dep": dep, "rua": rua, "sku": sku, "limit": limit}


# =========================================================
# UI blocks
# =========================================================
def semaforo_block(title: str, p: int, q: int, r: int):
    st.markdown(f"#### {title}")
    st.markdown(
        f"""
<div class="semaforo">
  <div class="box boxP"><div class="t">P (Alto giro)</div><div class="v">{fmt_int(p)}</div></div>
  <div class="box boxQ"><div class="t">Q (Médio giro)</div><div class="v">{fmt_int(q)}</div></div>
  <div class="box boxR"><div class="t">R (Baixo giro)</div><div class="v">{fmt_int(r)}</div></div>
</div>
        """,
        unsafe_allow_html=True,
    )


def kpi_card(title: str, value: str, sub: str = ""):
    st.markdown(
        f"""
<div class="card">
  <div class="card-title">{title}</div>
  <div class="card-value">{value}</div>
  <div class="card-sub">{sub}</div>
</div>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# MAIN
# =========================================================
def main():
    st.set_page_config(page_title="Painel SWAP Inteligente (P/Q/R)", page_icon="📊", layout="wide")
    css()

    st.title("Painel SWAP Inteligente (P/Q/R)")
    st.caption("Dashboard executivo: KPIs, semáforo P/Q/R, Top 20 e recorte crítico P→R (padrão).")

    # CDs
    cds: List[str] = []
    if table_exists("app_wms_empresas"):
        try:
            dfx = fetch_df("SELECT DISTINCT CD FROM app_wms_empresas ORDER BY CD")
            if not dfx.empty and "CD" in dfx.columns:
                cds = dfx["CD"].astype(str).tolist()
        except Exception:
            cds = []

    if not cds:
        cds = ["164", "364", "464"]

    default_cd = cds[0]
    init_filter_state(default_cd)

    with st.sidebar:
        st.markdown("### Filtros (com botão)")
        st.session_state.dash_cd = st.selectbox(
            "CD (NROEMPRESA / CD)",
            options=cds,
            index=cds.index(st.session_state.dash_cd) if st.session_state.dash_cd in cds else 0,
        )
        st.session_state.dash_dep = st.text_input("DEP (Linha) (opcional)", value=st.session_state.dash_dep)
        st.session_state.dash_rua = st.text_input("Rua (opcional)", value=st.session_state.dash_rua)
        st.session_state.dash_sku = st.text_input("Produto / SKU (opcional)", value=st.session_state.dash_sku)
        st.session_state.dash_limit = st.slider("Amostra máxima (linhas)", 5000, 200000, int(st.session_state.dash_limit), step=5000)

        cA, cB = st.columns(2, gap="small")
        with cA:
            if st.button("✅ Aplicar", type="primary"):
                st.session_state.dash_flt_applied = True
                st.rerun()
        with cB:
            if st.button("🧹 Limpar", type="secondary"):
                st.session_state.dash_flt_applied = False
                st.session_state.dash_cd = default_cd
                st.session_state.dash_dep = ""
                st.session_state.dash_rua = ""
                st.session_state.dash_sku = ""
                st.session_state.dash_limit = 60000
                st.rerun()

        st.markdown("---")
        st.markdown("### Legenda P/Q/R")
        st.markdown(
            """
<span class="badge badge-p">P</span> Alto Giro (Top 20% VISITAS) &nbsp;&nbsp;
<span class="badge badge-q">Q</span> Médio Giro (50–80%) &nbsp;&nbsp;
<span class="badge badge-r">R</span> Baixo Giro (&lt;50%)
            """,
            unsafe_allow_html=True,
        )
        st.markdown("### Autonomia (se houver dados)")
        st.markdown("- **P**: ≤ 3 dias  \n- **Q**: 4 a 10 dias  \n- **R**: > 10 dias")

    eff = get_effective_filters(default_cd)
    cd, dep, rua, sku, limit = eff["cd"], eff["dep"], eff["rua"], eff["sku"], eff["limit"]

    # status barra
    loaded_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    if not st.session_state.dash_flt_applied:
        st.markdown(f"<div class='status'>🟦 <b>Padrão do sistema (sem filtro aplicado)</b> — carregado em {loaded_at}</div>", unsafe_allow_html=True)
    else:
        st.markdown(
            f"<div class='status'>🟩 <b>Filtro aplicado</b> — CD <b>{cd}</b> | DEP <b>{dep or 'Todos'}</b> | "
            f"RUA <b>{rua or 'Todas'}</b> | SKU <b>{sku or 'Todos'}</b> | LIMIT <b>{limit}</b> — {loaded_at}</div>",
            unsafe_allow_html=True,
        )

    st.markdown("---")

    with st.spinner("Carregando base e calculando regras..."):
        df = load_base(cd=cd, dep=dep, rua=rua, limit=limit)

    if df.empty:
        st.warning("Sem dados para os filtros informados.")
        return

    if sku:
        df = df[df["SKU"].astype(str).str.contains(str(sku).strip(), na=False)]
        if df.empty:
            st.warning("Após filtro de SKU, não sobrou dados.")
            return

    df, autonomia_ok = compute_curvas(df)
    df = apply_business(df)

    # KPIs principais
    total = len(df)
    total_sku = int(df["SKU"].nunique()) if "SKU" in df.columns else total
    total_end = int(df["ENDERECO"].nunique()) if "ENDERECO" in df.columns else 0

    pr = df[(df["CURVA_PRODUTO"].astype(str).str.upper() == "P") & (df["CURVA_ENDERECO"].astype(str).str.upper() == "R")].copy()
    qr = df[(df["CURVA_PRODUTO"].astype(str).str.upper() == "Q") & (df["CURVA_ENDERECO"].astype(str).str.upper() == "R")].copy()
    pq = df[(df["CURVA_PRODUTO"].astype(str).str.upper() == "P") & (df["CURVA_ENDERECO"].astype(str).str.upper() == "Q")].copy()

    c1, c2, c3, c4 = st.columns(4, gap="large")
    with c1:
        kpi_card("Linhas analisadas", fmt_int(total), f"amostra (limit {limit})")
    with c2:
        kpi_card("P→R (crítico)", fmt_int(len(pr)), f"% na amostra: {pct(len(pr)/max(total,1)*100)}")
    with c3:
        kpi_card("Q→R (atenção)", fmt_int(len(qr)), f"% na amostra: {pct(len(qr)/max(total,1)*100)}")
    with c4:
        kpi_card("P→Q (atenção)", fmt_int(len(pq)), f"% na amostra: {pct(len(pq)/max(total,1)*100)}")

    if not autonomia_ok:
        st.info("Autonomia não disponível (falta VOLUMES e/ou MEDIA_DIA_CX). Mantendo P/Q/R por VISITAS.")

    st.markdown("---")

    # Semáforos
    p_prod, q_prod, r_prod = pqr_counts(df["CURVA_PRODUTO"])
    p_end, q_end, r_end = pqr_counts(df["CURVA_ENDERECO"])

    left, right = st.columns(2, gap="large")
    with left:
        semaforo_block("Endereços (curva por VISITAS somadas)", p_end, q_end, r_end)
    with right:
        semaforo_block("Produtos (curva por VISITAS)", p_prod, q_prod, r_prod)

    st.markdown("---")

    # Gráficos/Top 20 (dashboard)
    colA, colB = st.columns(2, gap="large")

    with colA:
        st.markdown("### Top 20 por Rua (VISITAS)")
        by_rua = df.groupby("RUA")["VISITAS"].sum().reset_index().sort_values("VISITAS", ascending=False).head(20)
        st.bar_chart(by_rua.set_index("RUA")["VISITAS"], height=280)

        st.markdown("### Top 20 por DEP (Linha) (VISITAS)")
        by_dep = df.groupby("DEP")["VISITAS"].sum().reset_index().sort_values("VISITAS", ascending=False).head(20)
        st.bar_chart(by_dep.set_index("DEP")["VISITAS"], height=280)

    with colB:
        st.markdown("### Principais motivos (incompatibilidade)")
        mot = df["MOTIVO"].value_counts().reset_index()
        mot.columns = ["MOTIVO", "QTD"]
        st.dataframe(mot, use_container_width=True, height=280)

        st.markdown("### Mix de Curvas (Produto x Endereço)")
        mix = pd.crosstab(
            df["CURVA_PRODUTO"].astype(str).str.upper(),
            df["CURVA_ENDERECO"].astype(str).str.upper(),
        )
        mix = mix.reindex(index=["P", "Q", "R"], columns=["P", "Q", "R"], fill_value=0)
        st.dataframe(mix, use_container_width=True, height=280)

    st.markdown("---")

    # Tabela executiva: P→R (padrão do sistema)
    st.markdown("## P→R (padrão do sistema) — Produto P em Endereço R")
    if pr.empty:
        st.info("Nenhum caso P→R encontrado neste recorte.")
    else:
        show_cols = [
            "CD", "DEP", "RUA", "SKU", "DESCCOMPLETA", "ENDERECO",
            "CURVA_ENDERECO", "CURVA_PRODUTO", "VISITAS", "AUTONOMIA_DIAS", "SCORE_SWAP"
        ]
        show_cols = [c for c in show_cols if c in pr.columns]
        pr = pr.sort_values(["SCORE_SWAP", "VISITAS"], ascending=False).head(500)

        sty = pr[show_cols].style.map(curva_css, subset=["CURVA_ENDERECO"]).map(curva_css, subset=["CURVA_PRODUTO"])
        st.dataframe(sty, use_container_width=True, height=520)

    st.markdown(
        "<div class='small'>Este painel é executivo (KPIs + visão geral). "
        "A página <b>Sugestões SWAP v12</b> é operacional (base completa / score / detalhe).</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
