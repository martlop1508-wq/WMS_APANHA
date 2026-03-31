#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pareto Layout Operacional V2.1 — Sugestões de Mudança no Layout
Base: app_layout_visitas_atual | Pareto 20/80/100 | Curva P/Q/R | SWAP DE→PARA
"""

from __future__ import annotations

import os
import re
import math
from dataclasses import dataclass
from typing import Optional, List

import pandas as pd
import pymysql
import streamlit as st
import plotly.express as px

try:
    from auth import require_login, sidebar_nav, topbar_user, logout_button, require_role
except Exception:
    def require_login():
        return
    def sidebar_nav():
        return
    def topbar_user():
        return
    def logout_button():
        return
    def require_role(*args, **kwargs):
        return

st.set_page_config(
    page_title="Pareto Layout Operacional",
    layout="wide",
    initial_sidebar_state="expanded",
)

require_login()
require_role("admin", "gestor", "analista")
sidebar_nav()

st.title("📦 Pareto Layout Operacional — Sugestões de Mudança")
st.caption("Base: app_layout_visitas_atual | Pareto 20/80/100 | Curva P/Q/R por reposição | SWAP DE→PARA")

topbar_user()
logout_button()

def getenv_str(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    return v if v is not None else default

@dataclass
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str

def load_db_config() -> DBConfig:
    host = getenv_str("DB_HOST", "127.0.0.1")
    port = int(getenv_str("DB_PORT", "3306") or 3306)
    user = getenv_str("DB_USER", "wms_user")
    password = getenv_str("DB_PASSWORD", getenv_str("DB_PASS", ""))
    database = getenv_str("DB_NAME", "wms_apanha")
    return DBConfig(host, port, user, password, database)

def get_db_conn(cfg: DBConfig):
    return pymysql.connect(
        host=cfg.host,
        port=int(cfg.port),
        user=cfg.user,
        password=cfg.password,
        database=cfg.database,
        autocommit=True,
        charset="utf8mb4",
        use_unicode=True,
    )

def fetch_df(sql: str, params: tuple = None) -> pd.DataFrame:
    cfg = load_db_config()
    last_err = None
    for _ in range(2):
        try:
            cnx = get_db_conn(cfg)
            cur = cnx.cursor(dictionary=True)
            cur.execute(sql, params or ())
            rows = cur.fetchall()
            cur.close()
            cnx.close()
            df = pd.DataFrame(rows)
            if not df.empty:
                df.columns = [str(c).upper() for c in df.columns]
            return df
        except Exception as e:
            last_err = e
            try:
                cnx.close()
            except Exception:
                pass
    raise last_err

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

def norm_cd(v) -> str:
    return str(v).strip() if v else ""

def norm_rua(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s == "" or s.lower() == "nan":
        return ""
    if re.fullmatch(r"\d+\.0", s):
        s = s.split(".", 1)[0]
    return s.zfill(3) if s.isdigit() else s

def norm_int(v) -> Optional[int]:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        return int(str(v).strip())
    except Exception:
        return None

def to_float_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str).str.replace(",", ".", regex=False).str.strip(),
        errors="coerce",
    )

def addr_str(rua: str, pred: Optional[int], ap: Optional[int], sl: Optional[int]) -> str:
    rua = norm_rua(rua)
    p = "" if pred is None else str(pred)
    a = "" if ap is None else str(ap)
    s = "" if sl is None else str(sl)
    return f"{rua}-{p}-{a}-{s}"

def classify_curva_pqr(dias: float) -> str:
    if pd.isna(dias):
        return "R"
    if dias <= 2:
        return "P"
    if dias <= 10:
        return "Q"
    return "R"

def curva_color_css(c: str) -> str:
    c = (c or "").upper()
    if c == "P":
        return "background-color: #C6EFCE; color: #006100;"
    if c == "Q":
        return "background-color: #FFEB9C; color: #9C6500;"
    if c == "R":
        return "background-color: #FFC7CE; color: #9C0006;"
    return ""

def load_layout(cd: str, linha: Optional[str], limit: int) -> pd.DataFrame:
    where = ["NROEMPRESA = %s"]
    params: List = [cd]

    if linha:
        where.append("LINHA = %s")
        params.append(linha.strip())

    q = f"""
        SELECT
            NROEMPRESA AS CD,
            SEQPRODUTO AS SKU,
            DESCCOMPLETA,
            DEP,
            CODRUA AS RUA,
            NROPREDIO AS PRED,
            NROAPARTAMENTO AS AP,
            NROSALA AS SL,
            LINHA,
            NORMA_APANHA,
            NORMA_PULMAO,
            visitas,
            volumes,
            media_dia_cx
        FROM app_layout_visitas_atual
        WHERE {" AND ".join(where)}
        LIMIT {int(limit)}
    """

    df = fetch_df(q, tuple(params))

    if df.empty:
        return df

    df["CD"] = df["CD"].map(norm_cd)
    df["RUA"] = df["RUA"].map(norm_rua)
    df["DEP"] = df["DEP"].astype(str).str.zfill(2)
    df["PRED"] = df["PRED"].apply(norm_int)
    df["AP"] = df["AP"].apply(norm_int)
    df["SL"] = df["SL"].apply(norm_int)

    for col in ("VISITAS", "VOLUMES", "MEDIA_DIA_CX", "NORMA_APANHA", "NORMA_PULMAO"):
        if col in df.columns:
            df[col] = to_float_series(df[col])

    df["ENDERECO"] = [
        addr_str(r, p, a, s) for r, p, a, s in zip(df["RUA"], df["PRED"], df["AP"], df["SL"])
    ]

    return df

def compute_pareto_curvas(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()

    out["REPOSICAO_DIAS"] = out.apply(
        lambda r: (r["NORMA_APANHA"] / r["MEDIA_DIA_CX"]) if r["MEDIA_DIA_CX"] and r["MEDIA_DIA_CX"] > 0 else pd.NA,
        axis=1,
    )
    out["REPOSICAO_DIAS"] = pd.to_numeric(out["REPOSICAO_DIAS"], errors="coerce")
    out["CURVA_PQR"] = out["REPOSICAO_DIAS"].apply(classify_curva_pqr)

    out["SCORE_PARETO"] = (
        out["VISITAS"].fillna(0) * 0.65
        + out["MEDIA_DIA_CX"].fillna(0) * 0.25
        + out["VOLUMES"].fillna(0) * 0.10
    )

    out = out.sort_values(
        by=["VISITAS", "MEDIA_DIA_CX", "VOLUMES"],
        ascending=[False, False, False],
        kind="mergesort",
    ).reset_index(drop=True)

    total_visitas = out["VISITAS"].sum()
    if total_visitas > 0:
        out["PCT_VISITAS"] = out["VISITAS"] / total_visitas
        out["PCT_ACUM"] = out["PCT_VISITAS"].cumsum()
    else:
        out["PCT_VISITAS"] = 0
        out["PCT_ACUM"] = 0

    def faixa_pareto(p):
        if p <= 0.20:
            return "20%"
        if p <= 0.80:
            return "80%"
        return "100%"

    out["FAIXA_PARETO"] = out["PCT_ACUM"].apply(faixa_pareto)

    return out

def build_swap_pairs(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    base = df.copy()
    base["CP"] = base["CURVA_PQR"].astype(str).str.upper()

    de_mask = (
        ((base["CP"] == "P") & (base["FAIXA_PARETO"] == "100%")) |
        ((base["CP"] == "Q") & (base["FAIXA_PARETO"] == "100%"))
    )

    de = base[de_mask].copy().sort_values("SCORE_PARETO", ascending=False)
    para = base[(base["CP"] == "R") & (base["FAIXA_PARETO"].isin(["20%", "80%"]))].copy()

    used_idx = set()
    pairs = []

    for idx_de, rde in de.iterrows():
        if idx_de in used_idx:
            continue

        cd = rde["CD"]
        dep = rde["DEP"]

        pool = para[(para["CD"] == cd) & (para["DEP"] == dep)].copy()
        pool = pool[~pool.index.isin(used_idx)]

        if pool.empty:
            continue

        rpa = pool.sort_values("SCORE_PARETO").iloc[0]

        used_idx.add(idx_de)
        used_idx.add(rpa.name)

        pairs.append({
            "CD": rde["CD"],
            "DEP": rde["DEP"],
            "SKU_DE": rde["SKU"],
            "DESC_DE": rde.get("DESCCOMPLETA"),
            "END_DE": rde["ENDERECO"],
            "CURVA_DE": rde["CP"],
            "FAIXA_DE": rde["FAIXA_PARETO"],
            "VISITAS_DE": rde["VISITAS"],
            "REPOS_DIAS_DE": rde["REPOSICAO_DIAS"],
            "SKU_PARA": rpa["SKU"],
            "DESC_PARA": rpa.get("DESCCOMPLETA"),
            "END_PARA": rpa["ENDERECO"],
            "CURVA_PARA": rpa["CP"],
            "FAIXA_PARA": rpa["FAIXA_PARETO"],
            "VISITAS_PARA": rpa["VISITAS"],
            "REPOS_DIAS_PARA": rpa["REPOSICAO_DIAS"],
            "MOTIVO": f"Curva {rde['CP']} em {rde['FAIXA_PARETO']} ↔ Curva {rpa['CP']} em {rpa['FAIXA_PARETO']}",
            "SCORE": float(rde.get("SCORE_PARETO", 0)),
        })

    return pd.DataFrame(pairs)

def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")

def main():
    with st.sidebar:
        st.subheader("Filtros")
        cd = st.text_input("CD (NROEMPRESA)", value="164").strip()
        linha = st.text_input("Linha (opcional)", value="").strip()
        limit = st.number_input("Limite de leitura", min_value=100, max_value=50000, value=8000, step=100)
        min_visitas = st.number_input("Mínimo de visitas", min_value=0, max_value=1000, value=0, step=1)

    if not cd:
        st.warning("Informe o CD.")
        return

    try:
        df_layout = load_layout(cd, linha or None, int(limit))
    except Exception as e:
        st.error(f"Falha ao consultar layout: {e}")
        return

    if df_layout.empty:
        st.info("Nenhum registro encontrado.")
        return

    df = compute_pareto_curvas(df_layout)

    if min_visitas > 0:
        df = df[df["VISITAS"] >= min_visitas].copy()

    st.subheader("Resumo rápido")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total SKUs", br_int(len(df)))
    c2.metric("Visitas totais", br_int(df["VISITAS"].sum()))
    c3.metric("Curva P", br_int((df["CURVA_PQR"] == "P").sum()))
    c4.metric("Curva Q", br_int((df["CURVA_PQR"] == "Q").sum()))
    c5.metric("Curva R", br_int((df["CURVA_PQR"] == "R").sum()))

    st.subheader("Pareto 20 / 80 / 100")
    fig = px.bar(
        df.head(100),
        x="SKU",
        y="VISITAS",
        color="FAIXA_PARETO",
        category_orders={"FAIXA_PARETO": ["20%", "80%", "100%"]},
        color_discrete_map={"20%": "#0F172A", "80%": "#2563EB", "100%": "#94A3B8"},
        hover_data={"CURVA_PQR": True, "REPOSICAO_DIAS": ":.1f", "VOLUMES": ":,.0f"},
        title="Top 100 SKUs por visitas",
    )
    fig.update_layout(height=500, xaxis_tickangle=65, margin=dict(b=150))
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Base operacional")
    show_cols = [
        "CD", "SKU", "DESCCOMPLETA", "DEP", "ENDERECO", "LINHA",
        "VISITAS", "VOLUMES", "MEDIA_DIA_CX", "NORMA_APANHA",
        "REPOSICAO_DIAS", "CURVA_PQR", "FAIXA_PARETO",
    ]
    show_cols = [c for c in show_cols if c in df.columns]
    view = df[show_cols].copy()

    st.dataframe(view, use_container_width=True, height=420, hide_index=True)

    st.divider()

    st.subheader("Sugestões de Troca (SWAP) — DE → PARA")
    df_pairs = build_swap_pairs(df)

    if df_pairs.empty:
        st.info("Não encontrei pares DE→PARA com as regras atuais.")
        return

    df_pairs = df_pairs.sort_values("SCORE", ascending=False)

    show_cols_swap = [
        "CD", "DEP", "SKU_DE", "END_DE", "CURVA_DE", "FAIXA_DE",
        "SKU_PARA", "END_PARA", "CURVA_PARA", "FAIXA_PARA",
        "MOTIVO", "SCORE",
    ]

    df_pairs_show = df_pairs[show_cols_swap].copy()

    st.dataframe(df_pairs_show, use_container_width=True, height=420, hide_index=True)

    st.download_button(
        "⬇️ Exportar Plano de Troca (CSV)",
        data=to_csv_bytes(df_pairs_show),
        file_name=f"plano_troca_cd_{cd}.csv",
        mime="text/csv",
        use_container_width=True,
    )

if __name__ == "__main__":
    main()
