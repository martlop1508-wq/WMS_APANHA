
from __future__ import annotations

# =========================================================
# WMS APANHA — SWAP INTELIGENTE V21.6.3.5.1
# ---------------------------------------------------------
# Objetivo desta versão:
# 1) validar se o item está de fato endereçado antes de sugerir swap
# 2) usar app_plan06 para validar elegibilidade logística
# 3) corrigir a leitura operacional das curvas do slot:
#       P1 / P2 -> macro P
#       P4      -> macro Q
#       P6+     -> macro R
# 4) separar plano de movimentação de inconsistências cadastrais
# 5) reduzir falsos "SEM INFO" no meio da análise
# 6) ampliar geração de sugestões, reduzindo filtros excessivos
# 7) mover explicações para um rodapé recolhível
#
# Observação importante:
# - O código tenta se adaptar ao banco usando INFORMATION_SCHEMA.
# - Quando uma tabela ou coluna não existir, ele faz fallback sem quebrar.
# - A regra principal é logística; não há priorização financeira.
# =========================================================

import os
from io import StringIO
from typing import Optional, List, Tuple, Dict

import numpy as np
import pandas as pd
import streamlit as st
import mysql.connector

st.set_page_config(page_title="WMS Apanha — Swap Inteligente V21.6.3.5.1", layout="wide")

# =========================================================
# BLOCO 1 — CONEXÃO E CAMADA SQL
# =========================================================
def get_db_config() -> dict:
    """Lê secrets ou variáveis de ambiente."""
    try:
        if "mysql" in st.secrets:
            cfg = st.secrets["mysql"]
            return {
                "host": cfg.get("host", "127.0.0.1"),
                "user": cfg.get("user", "wms_user"),
                "password": cfg.get("password", ""),
                "database": cfg.get("database", "wms_apanha"),
                "port": int(cfg.get("port", 3306)),
            }
    except Exception:
        pass

    return {
        "host": os.getenv("WMS_DB_HOST", "127.0.0.1"),
        "user": os.getenv("WMS_DB_USER", "wms_user"),
        "password": os.getenv("WMS_DB_PASS", ""),
        "database": os.getenv("WMS_DB_NAME", "wms_apanha"),
        "port": int(os.getenv("WMS_DB_PORT", "3306")),
    }


def new_connection():
    """Cria uma nova conexão MySQL."""
    cfg = get_db_config()
    return mysql.connector.connect(
        host=cfg["host"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        port=cfg["port"],
        autocommit=False,
    )


def get_conn():
    """
    Retorna uma conexão válida.
    Se não existir ou estiver expirada, recria automaticamente.
    """
    conn = st.session_state.get("mysql_conn")
    try:
        if conn is None or not conn.is_connected():
            conn = new_connection()
            st.session_state["mysql_conn"] = conn
        else:
            conn.ping(reconnect=True, attempts=2, delay=1)
    except Exception:
        conn = new_connection()
        st.session_state["mysql_conn"] = conn
    return conn


def reset_conn():
    """Força descarte da conexão atual."""
    conn = st.session_state.pop("mysql_conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def fetch_df(query: str, params: tuple = ()) -> pd.DataFrame:
    """Executa SQL e devolve dataframe, com tentativa automática de reconexão."""
    conn = get_conn()
    try:
        conn.ping(reconnect=True, attempts=2, delay=1)
        return pd.read_sql(query, conn, params=params)
    except Exception:
        reset_conn()
        conn = get_conn()
        conn.ping(reconnect=True, attempts=2, delay=1)
        return pd.read_sql(query, conn, params=params)

def exec_sql(sql: str, params: tuple = ()) -> None:
    """Executa SQL simples com reconexão automática."""
    conn = get_conn()
    try:
        conn.ping(reconnect=True, attempts=2, delay=1)
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        cur.close()
    except Exception:
        reset_conn()
        conn = get_conn()
        conn.ping(reconnect=True, attempts=2, delay=1)
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        cur.close()


@st.cache_data(ttl=300, show_spinner=False)
def get_table_columns(table_name: str) -> set[str]:
    q = """
    SELECT UPPER(COLUMN_NAME) AS COLUMN_NAME
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND UPPER(TABLE_NAME) = UPPER(%s)
    """
    try:
        df = fetch_df(q, (table_name,))
        if df.empty:
            return set()
        return set(df["COLUMN_NAME"].astype(str).tolist())
    except Exception:
        return set()


@st.cache_data(ttl=300, show_spinner=False)
def table_exists(table_name: str) -> bool:
    q = """
    SELECT COUNT(*) AS QTD
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE()
      AND UPPER(TABLE_NAME) = UPPER(%s)
    """
    try:
        df = fetch_df(q, (table_name,))
        return bool(int(df.iloc[0]["QTD"])) if not df.empty else False
    except Exception:
        return False


def get_col_expr(table_name: str, alias: str, candidates: list[str], fallback: str = "''") -> str:
    cols = get_table_columns(table_name)
    for col in candidates:
        if col.upper() in cols:
            return f"{alias}.{col}"
    return fallback


def export_csv(df: pd.DataFrame) -> bytes:
    sio = StringIO()
    df.to_csv(sio, index=False, sep=";")
    return sio.getvalue().encode("utf-8-sig")


# =========================================================
# BLOCO 2 — FUNÇÕES AUXILIARES / NORMALIZAÇÃO
# =========================================================
PQR_ORDER = {"P": 1, "Q": 2, "R": 3}


def norm_str(x) -> str:
    return "" if x is None else str(x).strip()


def safe_int(x) -> int:
    try:
        if x is None or str(x).strip() == "" or str(x).strip().upper() == "NONE":
            return 0
        return int(float(str(x).replace(",", ".")))
    except Exception:
        return 0


def safe_float(x) -> float:
    try:
        if x is None or str(x).strip() == "" or str(x).strip().upper() == "NONE":
            return 0.0
        return float(str(x).replace(".", "").replace(",", ".")) if (isinstance(x, str) and x.count(",") == 1 and x.count(".") > 0) else float(str(x).replace(",", "."))
    except Exception:
        return 0.0


def zfill_if_digit(x: str, width: int) -> str:
    s = norm_str(x)
    return s.zfill(width) if s.isdigit() else s


def pqr_rank(x: str) -> int:
    return PQR_ORDER.get(norm_str(x).upper(), 99)


def badge_prioridade(score: float) -> str:
    if score >= 85:
        return "🔴 CRÍTICA"
    if score >= 60:
        return "🟠 ALTA"
    if score >= 35:
        return "🟡 MÉDIA"
    return "⚪ BAIXA"


def badge_outlier(fator: float, dias_ativos: int = 0, volume_total: float = 0.0) -> str:
    """
    OUTLIER logístico real = saída muito concentrada em poucos dias.

    Regras desta versão:
    - 🔴 OUTLIER: fator alto + poucos dias ativos + volume relevante
    - 🟡 ATENÇÃO: concentração moderada, mas ainda fora do padrão
    - 🟢 NORMAL: comportamento distribuído ao longo da janela
    """
    f = safe_float(fator)
    d = safe_int(dias_ativos)
    v = safe_float(volume_total)

    if f >= 5 and d > 0 and d <= 3 and v >= 200:
        return "🔴 OUTLIER"
    if f >= 3 and d > 0 and d <= 7 and v >= 100:
        return "🟡 ATENÇÃO"
    return "🟢 NORMAL"


def grupo_linha(linha: str) -> str:
    """
    Agrupamento logístico da linha do produto.
    Mantém semântica operacional para ampliar pool de destino.
    """
    s = norm_str(linha).upper()
    if "LIQUID" in s or "BEBIDA" in s:
        return "LIQUIDA"
    if "LATIC" in s:
        return "LATICINIOS"
    if "ALIMENT" in s or "MERCEARIA" in s or "COMPLEMENTAR" in s:
        return "ALIMENTAR"
    if "BAZAR" in s or "UTIL" in s:
        return "BAZAR"
    if "PERFUM" in s or "COSMET" in s or "HIGIENE" in s:
        return "PERFUMARIA"
    if "LIMPEZA" in s:
        return "LIMPEZA"
    if "FRIO" in s or "PEREC" in s or "RESFRI" in s or "CONGEL" in s:
        return "PERECIVEIS"
    return "OUTROS"


# =========================================================
# BLOCO 3 — REGRA OPERACIONAL DE CURVA / SLOT
# =========================================================
def classificar_curva_reposicao(dias_cobertura: float) -> tuple[str, str]:
    """
    Regra oficial aprovada pelo negócio:
    P = até 2 dias
    Q = acima de 2 até 10 dias
    R = acima de 10 dias
    """
    d = safe_float(dias_cobertura)
    if d <= 0:
        return ("SEM INFO", "SEM INFO")
    if d <= 2:
        return ("P", "P - até 2 dias")
    if d <= 10:
        return ("Q", "Q - acima de 2 até 10 dias")
    return ("R", "R - acima de 10 dias")


def subcurva_slot_por_sala(nrosala: str) -> str:
    """
    Regra física do slot conforme alinhamento do usuário:
    P1 / P2 = curva P
    P4      = curva Q
    P6+     = curva R
    """
    sl = safe_int(nrosala)
    if sl <= 0:
        return "SEM INFO"
    if sl == 1:
        return "P1"
    if sl == 2:
        return "P2"
    if sl in (3, 4):
        return "P4"
    if sl in (5, 6):
        return "P6"
    return "P12"


def macrocurva_operacional(subcurva: str) -> str:
    """
    Mapeamento correto da aderência:
    P1/P2 -> P
    P4    -> Q
    P6+   -> R
    Q     -> Q
    R     -> R
    """
    sc = norm_str(subcurva).upper()
    if sc in ("P", "P1", "P2"):
        return "P"
    if sc in ("Q", "P4"):
        return "Q"
    if sc in ("R", "P6", "P12", "P8", "P10"):
        return "R"
    if sc.startswith("P"):
        # fallback conservador para qualquer P-numérico
        num = safe_int(sc[1:])
        if num <= 2:
            return "P"
        if num <= 4:
            return "Q"
        return "R"
    return "SEM INFO"


def curva_final_item(curva_reposicao: str, curva_visita: str) -> str:
    cr = norm_str(curva_reposicao).upper()
    cv = norm_str(curva_visita).upper()
    if cr not in PQR_ORDER or cv not in PQR_ORDER:
        return "SEM INFO"
    return cr if PQR_ORDER[cr] < PQR_ORDER[cv] else cv


def target_subcurve(curva_final: str) -> str:
    """
    Define o slot objetivo.
    Não é um teto fixo, mas o alvo preferencial do motor.
    """
    cf = norm_str(curva_final).upper()
    if cf == "P":
        return "P2"
    if cf == "Q":
        return "P4"
    if cf == "R":
        return "P6"
    return "SEM INFO"


def calcular_gap_curva(curva_prod: str, curva_end: str) -> int:
    rp = pqr_rank(curva_prod)
    re = pqr_rank(curva_end)
    if rp >= 90 or re >= 90:
        return 0
    return abs(re - rp)


def calcular_score(visitas_90d: int, gap: int, dias_cobertura: float, curva_visita: str, curva_final: str, curva_endereco_macro: str) -> float:
    peso_repos = 0
    if dias_cobertura > 0:
        if dias_cobertura <= 2:
            peso_repos = 40
        elif dias_cobertura <= 10:
            peso_repos = 20
        else:
            peso_repos = 5

    peso_visita = {"P": 25, "Q": 12, "R": 4}.get(norm_str(curva_visita).upper(), 0)
    score = peso_repos + peso_visita + (gap * 25) + (safe_int(visitas_90d) * 0.25)

    cf = norm_str(curva_final).upper()
    ce = norm_str(curva_endereco_macro).upper()
    if cf == "P" and ce == "R":
        score += 25
    elif cf == "P" and ce == "Q":
        score += 12
    elif cf == "Q" and ce == "R":
        score += 10
    elif cf == "R" and ce in ("P", "Q"):
        score += 7

    return round(score, 1)


def prioridade_swap(curva_final: str, curva_endereco_macro: str, score: float) -> str:
    cf = norm_str(curva_final).upper()
    ce = norm_str(curva_endereco_macro).upper()
    if cf == "P" and ce == "R":
        return "🔴 CRÍTICA"
    if cf == "P" and ce == "Q":
        return "🟠 ALTA"
    if cf == "Q" and ce == "R":
        return "🟡 MÉDIA"
    if cf == "R" and ce in ("P", "Q"):
        return "🟠 EXPULSAR"
    return badge_prioridade(score)


def classificar_risco_ruptura(dias_cobertura: float) -> tuple[str, Optional[float]]:
    d = safe_float(dias_cobertura)
    if d <= 0:
        return ("SEM INFO", None)
    if d <= 1:
        return ("ALTO", d)
    if d <= 3:
        return ("MEDIO", d)
    return ("BAIXO", d)


def frequencia_reabastecimento(media_dia_90d: float, estoque_cd: float) -> str:
    md = safe_float(media_dia_90d)
    est = safe_float(estoque_cd)
    if md <= 0 or est <= 0:
        return "SEM INFO"
    cob = est / md
    if cob <= 2:
        return "DIARIO"
    if cob <= 5:
        return "2-3 DIAS"
    if cob <= 10:
        return "SEMANAL"
    return "EVENTUAL"


# =========================================================
# BLOCO 4 — INSPEÇÃO DE TABELAS AUXILIARES
# =========================================================
def build_plan06_query(cd: str) -> Optional[str]:
    if not table_exists("app_plan06"):
        return None

    cols = get_table_columns("app_plan06")
    code_col = next((c for c in ["CODIGO", "SEQPRODUTO", "CODPRODUTO"] if c in cols), None)
    emp_col = next((c for c in ["NROEMPRESA", "CD"] if c in cols), None)
    desc_col = next((c for c in ["DESCRICAO", "DESC_COMPLETA", "DESCCOMPLETA", "PRODUTO"] if c in cols), None)
    status_prod_col = next((c for c in ["STATUS_PRODUTO", "STATUS", "STATUSBF"] if c in cols), None)
    enderecado_col = next((c for c in ["ENDERECADO", "FLAG_ENDERECADO"] if c in cols), None)
    tem_estoque_col = next((c for c in ["TEM_ESTOQUE", "ESTOQUE_CD", "QTDATUAL"] if c in cols), None)
    estoque_col = next((c for c in ["ESTOQUE_CD", "QTDATUAL", "ESTOQUE"] if c in cols), None)
    modalidade_col = next((c for c in ["MODALIDADE_CD", "MODALIDADE"] if c in cols), None)

    if not code_col or not emp_col:
        return None

    return f"""
        SELECT
            CAST(p.{code_col} AS CHAR) AS CODIGO,
            CAST(p.{emp_col} AS CHAR) AS CD,
            {f"CAST(p.{desc_col} AS CHAR)" if desc_col else "''"} AS DESCRICAO_PLAN06,
            {f"CAST(p.{status_prod_col} AS CHAR)" if status_prod_col else "'ATIVO'"} AS STATUS_PRODUTO,
            {f"CAST(p.{enderecado_col} AS CHAR)" if enderecado_col else "''"} AS ENDERECADO,
            {f"CAST(p.{tem_estoque_col} AS CHAR)" if tem_estoque_col else "''"} AS TEM_ESTOQUE,
            {f"COALESCE(p.{estoque_col}, 0)" if estoque_col else "0"} AS ESTOQUE_CD,
            {f"CAST(p.{modalidade_col} AS CHAR)" if modalidade_col else "''"} AS MODALIDADE_CD
        FROM app_plan06 p
        WHERE CAST(p.{emp_col} AS CHAR) = '{str(cd)}'
    """


def load_plan06(cd: str) -> pd.DataFrame:
    q = build_plan06_query(cd)
    if not q:
        return pd.DataFrame(columns=["CODIGO", "CD", "DESCRICAO_PLAN06", "STATUS_PRODUTO", "ENDERECADO", "TEM_ESTOQUE", "ESTOQUE_CD", "MODALIDADE_CD"])
    try:
        df = fetch_df(q)
        if df.empty:
            return pd.DataFrame(columns=["CODIGO", "CD", "DESCRICAO_PLAN06", "STATUS_PRODUTO", "ENDERECADO", "TEM_ESTOQUE", "ESTOQUE_CD", "MODALIDADE_CD"])
        for c in df.columns:
            if c == "ESTOQUE_CD":
                df[c] = df[c].apply(safe_float)
            else:
                df[c] = df[c].astype(str).str.strip()
        return df
    except Exception:
        return pd.DataFrame(columns=["CODIGO", "CD", "DESCRICAO_PLAN06", "STATUS_PRODUTO", "ENDERECADO", "TEM_ESTOQUE", "ESTOQUE_CD", "MODALIDADE_CD"])


def load_endereco_validado(cd: str) -> pd.DataFrame:
    """
    Valida o endereço do item usando a melhor fonte disponível:
    1) app_ocupacao_atual (se existir e tiver colunas mínimas)
    2) qv99_ocupacao + qv00_layout_visitas
    """
    frames = []

    # Fonte 1 — app_ocupacao_atual
    if table_exists("app_ocupacao_atual"):
        cols = get_table_columns("app_ocupacao_atual")
        code_col = next((c for c in ["CODIGO", "SEQPRODUTO", "CODPRODUTO"] if c in cols), None)
        emp_col = next((c for c in ["CD", "NROEMPRESA"] if c in cols), None)

        rua_col = next((c for c in ["RUA", "CODRUA"] if c in cols), None)
        pred_col = next((c for c in ["PRED", "NROPREDIO", "PREDIO"] if c in cols), None)
        ap_col = next((c for c in ["AP", "NROAPARTAMENTO", "APTO"] if c in cols), None)
        sl_col = next((c for c in ["SL", "NROSALA", "SALA"] if c in cols), None)
        dep_col = next((c for c in ["DEP"] if c in cols), None)
        endereco_col = next((c for c in ["ENDERECO"] if c in cols), None)
        status_col = next((c for c in ["STATUS", "STATUS_ENDERECO"] if c in cols), None)

        if code_col and emp_col:
            endereco_expr = (
                f"CAST(a.{endereco_col} AS CHAR)" if endereco_col
                else f"CONCAT(COALESCE(a.{rua_col},''), '-', COALESCE(a.{pred_col},''), '-', COALESCE(a.{ap_col},''), '-', COALESCE(a.{sl_col},''))"
                if rua_col and pred_col and ap_col and sl_col
                else "''"
            )
            q = f"""
                SELECT
                    CAST(a.{emp_col} AS CHAR) AS CD,
                    {f"CAST(a.{dep_col} AS CHAR)" if dep_col else "''"} AS DEP,
                    CAST(a.{code_col} AS CHAR) AS CODIGO,
                    {endereco_expr} AS ENDERECO_ATUAL_VALIDADO,
                    {f"CAST(a.{status_col} AS CHAR)" if status_col else "''"} AS STATUS_ENDERECO,
                    'APP_OCUPACAO_ATUAL' AS FONTE_ENDERECO
                FROM app_ocupacao_atual a
                WHERE CAST(a.{emp_col} AS CHAR) = %s
            """
            try:
                df = fetch_df(q, (str(cd),))
                if not df.empty:
                    frames.append(df)
            except Exception:
                pass

    # Fonte 2 — qv99_ocupacao + qv00_layout_visitas
    if table_exists("qv99_ocupacao") and table_exists("qv00_layout_visitas"):
        q = """
            SELECT
                CAST(q.NROEMPRESA AS CHAR) AS CD,
                CAST(q.DEP AS CHAR) AS DEP,
                CAST(q.SEQPRODUTO AS CHAR) AS CODIGO,
                CONCAT(COALESCE(o.RUA, ''), '-', COALESCE(o.PRED, ''), '-', COALESCE(o.AP, ''), '-', COALESCE(o.SL, '')) AS ENDERECO_ATUAL_VALIDADO,
                CAST(o.STATUS AS CHAR) AS STATUS_ENDERECO,
                'QV99_OCUPACAO' AS FONTE_ENDERECO
            FROM qv00_layout_visitas q
            INNER JOIN qv99_ocupacao o
                ON o.CD COLLATE utf8mb4_unicode_ci = q.NROEMPRESA COLLATE utf8mb4_unicode_ci
               AND o.DEP COLLATE utf8mb4_unicode_ci = q.DEP COLLATE utf8mb4_unicode_ci
               AND o.RUA COLLATE utf8mb4_unicode_ci = q.CODRUA COLLATE utf8mb4_unicode_ci
               AND o.PRED COLLATE utf8mb4_unicode_ci = q.NROPREDIO COLLATE utf8mb4_unicode_ci
               AND o.AP COLLATE utf8mb4_unicode_ci = q.NROAPARTAMENTO COLLATE utf8mb4_unicode_ci
               AND o.SL COLLATE utf8mb4_unicode_ci = q.NROSALA COLLATE utf8mb4_unicode_ci
            WHERE q.NROEMPRESA COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci
        """
        try:
            df = fetch_df(q, (str(cd),))
            if not df.empty:
                frames.append(df)
        except Exception:
            pass

    if not frames:
        return pd.DataFrame(columns=["CD", "DEP", "CODIGO", "ENDERECO_ATUAL_VALIDADO", "STATUS_ENDERECO", "FONTE_ENDERECO"])

    out = pd.concat(frames, ignore_index=True)
    for c in out.columns:
        out[c] = out[c].astype(str).str.strip()

    # prioriza APP_OCUPACAO_ATUAL
    out["ORDEM_FONTE"] = out["FONTE_ENDERECO"].map({"APP_OCUPACAO_ATUAL": 1, "QV99_OCUPACAO": 2}).fillna(9)
    out = out.sort_values(["CODIGO", "ORDEM_FONTE"]).drop_duplicates(subset=["CODIGO"], keep="first").drop(columns=["ORDEM_FONTE"])
    return out


# =========================================================
# BLOCO 5 — CARGA HISTÓRICA PRINCIPAL
# =========================================================
@st.cache_data(ttl=120, show_spinner=False)
def load_base_historica(cd: str, dep: Optional[str], rua: Optional[str], dias_analitico: int, limit: int) -> pd.DataFrame:
    """
    Prioridade da base histórica:
    1) app_layout_visitas_atual com DT_REF / data
    2) qv00_layout_visitas como fallback (snapshot)
    """
    # Tentativa 1 — app_layout_visitas_atual
    if table_exists("app_layout_visitas_atual"):
        cols = get_table_columns("app_layout_visitas_atual")
        dt_col = next((c for c in ["DT_REF", "DATA_REF", "REF_DATE", "DTBASE"] if c in cols), None)
        emp_col = next((c for c in ["CD", "NROEMPRESA"] if c in cols), None)
        dep_col = next((c for c in ["DEP"] if c in cols), None)
        rua_col = next((c for c in ["RUA", "CODRUA"] if c in cols), None)
        pred_col = next((c for c in ["PRED", "NROPREDIO"] if c in cols), None)
        ap_col = next((c for c in ["AP", "NROAPARTAMENTO"] if c in cols), None)
        sl_col = next((c for c in ["SL", "NROSALA"] if c in cols), None)
        seq_col = next((c for c in ["SEQENDERECO"] if c in cols), None)
        code_col = next((c for c in ["CODIGO", "SEQPRODUTO", "CODPRODUTO"] if c in cols), None)
        desc_col = next((c for c in ["DESCRICAO", "DESCCOMPLETA", "DESC_COMPLETA"] if c in cols), None)
        linha_col = next((c for c in ["LINHA"] if c in cols), None)
        visitas_col = next((c for c in ["VISITAS"] if c in cols), None)
        volumes_col = next((c for c in ["VOLUMES", "QTD_SAIDA", "UNIDADES"] if c in cols), None)

        if dt_col and emp_col and dep_col and rua_col and pred_col and ap_col and sl_col and code_col and linha_col and visitas_col and volumes_col:
            where = [f"CAST(a.{emp_col} AS CHAR) = %s"]
            params: List = [str(cd)]
            if dep:
                where.append(f"CAST(a.{dep_col} AS CHAR) = %s")
                params.append(dep)
            if rua:
                where.append(f"CAST(a.{rua_col} AS CHAR) = %s")
                params.append(rua)

            where.append(f"a.{dt_col} >= CURDATE() - INTERVAL {int(dias_analitico)} DAY")
            q = f"""
                SELECT
                    CAST(a.{emp_col} AS CHAR) AS CD,
                    CAST(a.{dep_col} AS CHAR) AS DEP,
                    CAST(a.{rua_col} AS CHAR) AS RUA,
                    CAST(a.{pred_col} AS CHAR) AS NROPREDIO,
                    CAST(a.{ap_col} AS CHAR) AS NROAPARTAMENTO,
                    CAST(a.{sl_col} AS CHAR) AS NROSALA,
                    {f"CAST(a.{seq_col} AS CHAR)" if seq_col else "''"} AS SEQENDERECO,
                    CONCAT(COALESCE(a.{rua_col}, ''), '-', COALESCE(a.{pred_col}, ''), '-', COALESCE(a.{ap_col}, ''), '-', COALESCE(a.{sl_col}, '')) AS ENDERECO,
                    CAST(a.{code_col} AS CHAR) AS CODIGO,
                    {f"CAST(a.{desc_col} AS CHAR)" if desc_col else "''"} AS DESCRICAO,
                    CAST(a.{linha_col} AS CHAR) AS LINHA,
                    SUM(COALESCE(a.{visitas_col}, 0)) AS VISITAS_PERIODO,
                    SUM(COALESCE(a.{volumes_col}, 0)) AS UNIDADES_90D,
                    COUNT(DISTINCT a.{dt_col}) AS DIAS_ATIVOS
                FROM app_layout_visitas_atual a
                WHERE {" AND ".join(where)}
                GROUP BY 1,2,3,4,5,6,7,8,9,10,11
                LIMIT {int(limit)}
            """
            try:
                df = fetch_df(q, tuple(params))
                if not df.empty:
                    return normalizar_base(df)
            except Exception:
                pass

    # Fallback — qv00_layout_visitas
    q = """
        SELECT
            CAST(q.NROEMPRESA AS CHAR) AS CD,
            CAST(q.DEP AS CHAR) AS DEP,
            CAST(q.CODRUA AS CHAR) AS RUA,
            CAST(q.NROPREDIO AS CHAR) AS NROPREDIO,
            CAST(q.NROAPARTAMENTO AS CHAR) AS NROAPARTAMENTO,
            CAST(q.NROSALA AS CHAR) AS NROSALA,
            CAST(q.SEQENDERECO AS CHAR) AS SEQENDERECO,
            CONCAT(COALESCE(q.CODRUA, ''), '-', COALESCE(q.NROPREDIO, ''), '-', COALESCE(q.NROAPARTAMENTO, ''), '-', COALESCE(q.NROSALA, '')) AS ENDERECO,
            CAST(q.SEQPRODUTO AS CHAR) AS CODIGO,
            CAST(q.DESCCOMPLETA AS CHAR) AS DESCRICAO,
            CAST(q.LINHA AS CHAR) AS LINHA,
            SUM(COALESCE(q.VISITAS, 0)) AS VISITAS_PERIODO,
            SUM(COALESCE(q.VOLUMES, 0)) AS UNIDADES_90D,
            MAX(CASE WHEN COALESCE(q.VISITAS, 0) > 0 OR COALESCE(q.VOLUMES, 0) > 0 THEN 1 ELSE 0 END) AS DIAS_ATIVOS
        FROM qv00_layout_visitas q
        WHERE q.NROEMPRESA COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci
        {dep_filter}
        {rua_filter}
        GROUP BY 1,2,3,4,5,6,7,8,9,10,11
        LIMIT {limit}
    """
    dep_filter = ""
    rua_filter = ""
    params = [str(cd)]
    if dep:
        dep_filter += " AND q.DEP COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci"
        params.append(dep)
    if rua:
        rua_filter += " AND q.CODRUA COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci"
        params.append(rua)
    df = fetch_df(q.format(dep_filter=dep_filter, rua_filter=rua_filter, limit=int(limit)), tuple(params))
    return normalizar_base(df)


def normalizar_base(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for c in ["CD", "DEP", "RUA", "NROPREDIO", "NROAPARTAMENTO", "NROSALA", "SEQENDERECO", "ENDERECO", "CODIGO", "DESCRICAO", "LINHA"]:
        if c in out.columns:
            out[c] = out[c].fillna("").astype(str).str.strip()
    out["DEP"] = out["DEP"].apply(lambda x: zfill_if_digit(x, 2))
    out["RUA"] = out["RUA"].apply(lambda x: zfill_if_digit(x, 3))
    out["VISITAS_PERIODO"] = out["VISITAS_PERIODO"].apply(safe_int)
    out["UNIDADES_90D"] = out["UNIDADES_90D"].apply(safe_float)
    out["DIAS_ATIVOS"] = out["DIAS_ATIVOS"].apply(safe_int)
    out["GRUPO_LINHA"] = out["LINHA"].apply(grupo_linha)
    return out


# =========================================================
# BLOCO 6 — PARETO / MÉDIAS / OUTLIER
# ---------------------------------------------------------
# Pareto detalhado por linha agrupada:
# - P20  -> até 20% acumulado
# - P50  -> de 20,01% até 50%
# - P70  -> de 50,01% até 70%
# - P100 -> de 70,01% até 100%
#
# Macrocurva logística derivada do Pareto detalhado:
# - P20  -> P
# - P50  -> P
# - P70  -> Q
# - P100 -> R
# =========================================================
def classificar_faixa_pareto(perc_acum: float) -> str:
    p = safe_float(perc_acum)
    if p <= 0.20:
        return "P20"
    if p <= 0.50:
        return "P50"
    if p <= 0.70:
        return "P70"
    return "P100"


def macrocurva_pareto(faixa_pareto: str) -> str:
    faixa = norm_str(faixa_pareto).upper()
    mapa = {"P20": "P", "P50": "P", "P70": "Q", "P100": "R"}
    return mapa.get(faixa, "R")

def label_faixa_pareto_pct(faixa_pareto: str) -> str:
    """
    Converte a faixa técnica em rótulo visual de percentual para a operação.
    Regras visuais solicitadas pelo negócio:
    - até 20% = vermelho
    - 20% a 70% = amarelo
    - acima de 70% = vermelho
    """
    faixa = norm_str(faixa_pareto).upper()
    if faixa == "P20":
        return "🔴 20%"
    if faixa == "P50":
        return "🟡 50%"
    if faixa == "P70":
        return "🟡 70%"
    if faixa == "P100":
        return "🔴 100%"
    return "⚪ SEM FAIXA"


def format_percent_value(v: float) -> str:
    try:
        return f"{safe_float(v):.2f}%"
    except Exception:
        return ""


def unidade_saida_label() -> str:
    """
    A unidade depende da coluna de movimentação disponível na base histórica.
    Como o ambiente pode variar entre UN / CX / PT, a tela assume a unidade-base
    da coluna de saída consultada. Quando não for possível identificar, exibe 'BASE'.
    """
    return "BASE"



def aplicar_pareto_por_grupo(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out = out.sort_values(["GRUPO_LINHA", "VISITAS_PERIODO", "CODIGO"], ascending=[True, False, True]).reset_index(drop=True)
    out["TOTAL_VISITAS_GRUPO"] = out.groupby("GRUPO_LINHA")["VISITAS_PERIODO"].transform("sum")
    out["VISITAS_ACUM_GRUPO"] = out.groupby("GRUPO_LINHA")["VISITAS_PERIODO"].cumsum()
    out["PERC_ACUM_GRUPO"] = np.where(
        out["TOTAL_VISITAS_GRUPO"] > 0,
        out["VISITAS_ACUM_GRUPO"] / out["TOTAL_VISITAS_GRUPO"],
        0.0
    )
    out["FAIXA_PARETO"] = out["PERC_ACUM_GRUPO"].apply(classificar_faixa_pareto)
    out["CURVA_VISITA"] = out["FAIXA_PARETO"].apply(macrocurva_pareto)
    out["PERC_ACUM_PARETO"] = (out["PERC_ACUM_GRUPO"] * 100).round(2)
    return out


def adicionar_metricas_saida(df: pd.DataFrame, dias_analitico: int) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    dias = max(int(dias_analitico), 1)

    # Média diária distribuída pela janela inteira
    out["SAIDA_MEDIA_DIA_90D"] = (out["UNIDADES_90D"] / dias).round(4)

    # Intensidade média apenas nos dias em que houve saída
    out["PICO_SAIDA_DIA"] = out.apply(
        lambda r: round(r["UNIDADES_90D"] / r["DIAS_ATIVOS"], 4) if safe_int(r["DIAS_ATIVOS"]) > 0 else 0.0,
        axis=1
    )

    # Fator de concentração: quanto o pico dos dias ativos destoa da média da janela
    out["OUTLIER_FATOR"] = out.apply(
        lambda r: round(r["PICO_SAIDA_DIA"] / r["SAIDA_MEDIA_DIA_90D"], 2) if safe_float(r["SAIDA_MEDIA_DIA_90D"]) > 0 else np.nan,
        axis=1
    )

    # Sinal logístico de outlier: só é outlier de verdade quando o volume ficou muito concentrado em poucos dias
    out["SINAL_OUTLIER"] = out.apply(
        lambda r: badge_outlier(
            r.get("OUTLIER_FATOR", 0),
            r.get("DIAS_ATIVOS", 0),
            r.get("UNIDADES_90D", 0),
        ),
        axis=1
    )

    out["JUSTIFICATIVA_OUTLIER"] = out.apply(
        lambda r: (
            f"{safe_int(r.get('UNIDADES_90D', 0))} na janela | {safe_int(r.get('DIAS_ATIVOS', 0))} dias ativos | "
            f"pico {safe_float(r.get('PICO_SAIDA_DIA', 0)):.1f}/dia ativo"
        ) if str(r.get("SINAL_OUTLIER", "")).startswith("🔴") else "",
        axis=1
    )
    return out


# =========================================================
# BLOCO 6.1 — EXCLUSÃO DE COMPONENTES LOGÍSTICOS
# ---------------------------------------------------------
# Alguns códigos e descrições representam componentes para
# montar a carga (pallet, caixa HB, rollteiner etc.).
# Eles não podem disputar endereço no motor DE -> PARA.
#
# Regras desta versão:
# 1) excluir por código fixo informado pela operação
# 2) excluir também por descrição, para proteger o motor
#    contra novos códigos futuros da mesma família
# 3) registrar motivo de exclusão para aparecer na aba de
#    inconsistências / saneamento
# =========================================================
CODIGOS_EXCLUIDOS_SWAP = {
    496685,   # PALLET MAD.PADRAO PBR
    1456180,  # (ADM)-CAIXA PLASTICA HB DOBR.623
    2640619,  # (ADM)-CAIXA PLASTICA HB DOBR.618 / componente logístico
}

PALAVRAS_EXCLUSAO_SWAP = [
    "PALLET",
    "PBR",
    "ROLLTEINER",
    "ROLLTAINER",
    "CAIXA HB",
    "CAIXA PLASTICA",
    "CAIXA PLÁSTICA",
    "CONTENTOR",
    "ENGRADADO",
    "SUPORTE LOGISTICO",
    "SUPORTE LOGÍSTICO",
    "MONTAGEM DE CARGA",
]


def item_excluido_do_swap(row: pd.Series) -> Tuple[bool, str]:
    """
    Detecta itens que não fazem parte do universo de picking.
    Retorna:
    - flag de exclusão
    - motivo da exclusão
    """
    codigo_bruto = row.get("CODIGO", 0)
    try:
        codigo = int(float(codigo_bruto))
    except Exception:
        codigo = 0

    descricao = norm_str(row.get("DESCRICAO", "")).upper()
    descricao_plan06 = norm_str(row.get("DESCRICAO_PLAN06", "")).upper()
    texto = f"{descricao} {descricao_plan06}".strip()

    if codigo in CODIGOS_EXCLUIDOS_SWAP:
        return True, "COMPONENTE_LOGISTICO_CODIGO"

    for termo in PALAVRAS_EXCLUSAO_SWAP:
        if termo in texto:
            return True, "COMPONENTE_LOGISTICO_DESCRICAO"

    return False, ""


# =========================================================
# BLOCO 7 — VALIDAÇÃO PLAN06 E ENDEREÇO
# =========================================================
def classificar_status_item(row: pd.Series) -> Tuple[str, str]:
    """
    Retorna:
    - STATUS_ITEM_PLAN06
    - MOTIVO_EXCLUSAO
    """
    excluir_swap = bool(row.get("EXCLUIR_SWAP", False))
    motivo_excluir = norm_str(row.get("MOTIVO_EXCLUIR_SWAP", ""))
    enderecado = norm_str(row.get("ENDERECADO", "")).upper()
    status_prod = norm_str(row.get("STATUS_PRODUTO", "ATIVO")).upper()
    tem_estoque = norm_str(row.get("TEM_ESTOQUE", "")).upper()
    endereco_validado = norm_str(row.get("ENDERECO_ATUAL_VALIDADO", ""))
    modalidade = norm_str(row.get("MODALIDADE_CD", "")).upper()

    if excluir_swap:
        return "EXCLUIDO", motivo_excluir or "COMPONENTE_LOGISTICO"
    if endereco_validado == "":
        return "INCONSISTENTE", "SEM_ENDERECO_VALIDADO"
    if enderecado not in ("", "S", "SIM", "1", "ENDEREÇADO", "ENDERECADO"):
        return "INCONSISTENTE", "NAO_ENDERECADO_PLAN06"
    if status_prod not in ("", "ATIVO", "A", "OK"):
        return "INCONSISTENTE", "ITEM_INATIVO_PLAN06"
    if modalidade and "CROSS" in modalidade:
        return "INCONSISTENTE", "CROSS_DOCKING"
    if tem_estoque in ("N", "NAO", "NÃO", "0"):
        return "INCONSISTENTE", "SEM_ESTOQUE_CD"
    return "APTO", ""


def consolidar_base(base_raw: pd.DataFrame, plan06: pd.DataFrame, end_valid: pd.DataFrame) -> pd.DataFrame:
    if base_raw.empty:
        return base_raw

    out = base_raw.copy()

    if not end_valid.empty:
        out = out.merge(
            end_valid[["CODIGO", "ENDERECO_ATUAL_VALIDADO", "STATUS_ENDERECO", "FONTE_ENDERECO"]],
            on="CODIGO",
            how="left"
        )
    else:
        out["ENDERECO_ATUAL_VALIDADO"] = ""
        out["STATUS_ENDERECO"] = ""
        out["FONTE_ENDERECO"] = ""

    if not plan06.empty:
        out = out.merge(
            plan06[["CODIGO", "DESCRICAO_PLAN06", "STATUS_PRODUTO", "ENDERECADO", "TEM_ESTOQUE", "ESTOQUE_CD", "MODALIDADE_CD"]],
            on="CODIGO",
            how="left"
        )
    else:
        out["DESCRICAO_PLAN06"] = ""
        out["STATUS_PRODUTO"] = "ATIVO"
        out["ENDERECADO"] = ""
        out["TEM_ESTOQUE"] = ""
        out["ESTOQUE_CD"] = 0.0
        out["MODALIDADE_CD"] = ""

    out["ESTOQUE_CD"] = out["ESTOQUE_CD"].apply(safe_float)
    out["DESCRICAO"] = np.where(out["DESCRICAO"].astype(str).str.strip() == "", out["DESCRICAO_PLAN06"], out["DESCRICAO"])

    # -----------------------------------------------------
    # Validação de componentes logísticos
    # Se for pallet / caixa HB / rollteiner ou similar,
    # o item sai do universo de swap e vai para diagnóstico.
    # -----------------------------------------------------
    exc = out.apply(item_excluido_do_swap, axis=1)
    out["EXCLUIR_SWAP"] = exc.apply(lambda x: x[0])
    out["MOTIVO_EXCLUIR_SWAP"] = exc.apply(lambda x: x[1])

    out["STATUS_ITEM_PLAN06"], out["MOTIVO_EXCLUSAO"] = zip(*out.apply(classificar_status_item, axis=1))
    out["ELEGIVEL_SWAP"] = out["STATUS_ITEM_PLAN06"].eq("APTO")

    # Curva física do endereço atual validado
    out["SUBCURVA_ENDERECO_ATUAL"] = out["NROSALA"].apply(subcurva_slot_por_sala)
    out["CURVA_ENDERECO_MACRO"] = out["SUBCURVA_ENDERECO_ATUAL"].apply(macrocurva_operacional)

    # Métricas de reposição
    out["DIAS_COBERTURA"] = out.apply(
        lambda r: round(r["ESTOQUE_CD"] / r["SAIDA_MEDIA_DIA_90D"], 2) if safe_float(r["SAIDA_MEDIA_DIA_90D"]) > 0 and safe_float(r["ESTOQUE_CD"]) > 0 else np.nan,
        axis=1
    )
    rep = out["DIAS_COBERTURA"].apply(classificar_curva_reposicao)
    out["CURVA_REPOSICAO"] = rep.apply(lambda x: x[0])
    out["FAIXA_REPOSICAO"] = rep.apply(lambda x: x[1])

    out["CURVA_FINAL_PRODUTO"] = out.apply(lambda r: curva_final_item(r["CURVA_REPOSICAO"], r["CURVA_VISITA"]), axis=1)
    out["SUBCURVA_DESTINO_FUTURO"] = out["CURVA_FINAL_PRODUTO"].apply(target_subcurve)
    out["CURVA_ENDERECO_FUTURO"] = out["SUBCURVA_DESTINO_FUTURO"].apply(macrocurva_operacional)

    out["GAP_CURVA"] = out.apply(lambda r: calcular_gap_curva(r["CURVA_FINAL_PRODUTO"], r["CURVA_ENDERECO_MACRO"]), axis=1)
    out["SCORE"] = out.apply(
        lambda r: calcular_score(r["VISITAS_PERIODO"], r["GAP_CURVA"], r["DIAS_COBERTURA"], r["CURVA_VISITA"], r["CURVA_FINAL_PRODUTO"], r["CURVA_ENDERECO_MACRO"]),
        axis=1
    )
    out["PRIORIDADE_SWAP"] = out.apply(lambda r: prioridade_swap(r["CURVA_FINAL_PRODUTO"], r["CURVA_ENDERECO_MACRO"], r["SCORE"]), axis=1)
    out["RISCO_RUPTURA"], out["DIAS_PARA_RUPTURA"] = zip(*out["DIAS_COBERTURA"].apply(classificar_risco_ruptura))
    out["FREQ_REABAST"] = out.apply(lambda r: frequencia_reabastecimento(r["SAIDA_MEDIA_DIA_90D"], r["ESTOQUE_CD"]), axis=1)

    out["JUSTIFICATIVA_SWAP"] = out.apply(montar_justificativa, axis=1)
    return out


def montar_justificativa(row: pd.Series) -> str:
    if not bool(row.get("ELEGIVEL_SWAP", False)):
        return f"Inconsistência: {row.get('MOTIVO_EXCLUSAO', 'SEM MOTIVO')}"

    return (
        f"Pareto {row.get('FAIXA_PARETO','')} -> macro {row.get('CURVA_VISITA','')} | "
        f"cobertura {safe_float(row.get('DIAS_COBERTURA',0)):.1f} dias = reposição {row.get('CURVA_REPOSICAO','')} | "
        f"curva final {row.get('CURVA_FINAL_PRODUTO','')} | "
        f"slot atual {row.get('SUBCURVA_ENDERECO_ATUAL','')} ({row.get('CURVA_ENDERECO_MACRO','')}) | "
        f"destino sugerido {row.get('SUBCURVA_DESTINO_FUTURO','')}"
    )


# =========================================================
# BLOCO 8 — BASE DE DESTINOS / OCUPAÇÃO
# =========================================================
@st.cache_data(ttl=120, show_spinner=False)
def load_base_para(cd: str, dep: Optional[str], rua: Optional[str]) -> pd.DataFrame:
    """
    Base de destinos usando qv99_ocupacao.
    Tenta recuperar linha de referência do endereço para ranquear melhor.
    """
    if not table_exists("qv99_ocupacao"):
        return pd.DataFrame()

    where = ["o.CD COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci"]
    params: List = [str(cd)]
    if dep:
        where.append("o.DEP COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci")
        params.append(dep)
    if rua:
        where.append("o.RUA COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci")
        params.append(rua)

    tipoendereco_expr = get_col_expr("qv99_ocupacao", "o", ["TIPOENDERECO", "TIPO_ENDERECO", "TIPOEND"])

    q = f"""
        SELECT
            CAST(o.CD AS CHAR) AS CD,
            CAST(o.DEP AS CHAR) AS DEP,
            CAST(o.RUA AS CHAR) AS RUA,
            CAST(o.PRED AS CHAR) AS PRED,
            CAST(o.AP AS CHAR) AS AP,
            CAST(o.SL AS CHAR) AS SL,
            CAST(o.SEQENDERECO AS CHAR) AS SEQENDERECO,
            CAST(o.STATUS AS CHAR) AS STATUS,
            {tipoendereco_expr} AS TIPOENDERECO,
            CONCAT(COALESCE(o.RUA, ''), '-', COALESCE(o.PRED, ''), '-', COALESCE(o.AP, ''), '-', COALESCE(o.SL, '')) AS ENDERECO,
            CAST(lr.LINHA_REF AS CHAR) AS LINHA,
            CAST(lr.GRUPO_LINHA AS CHAR) AS GRUPO_LINHA,
            CAST(qcur.SEQPRODUTO AS CHAR) AS CODIGO_OCUPANTE,
            CAST(qcur.DESCCOMPLETA AS CHAR) AS DESC_OCUPANTE
        FROM qv99_ocupacao o
        LEFT JOIN (
            SELECT
                q.NROEMPRESA,
                q.DEP,
                q.CODRUA,
                q.NROPREDIO,
                q.NROAPARTAMENTO,
                q.NROSALA,
                MAX(q.LINHA) AS LINHA_REF,
                MAX(q.LINHA) AS GRUPO_LINHA
            FROM qv00_layout_visitas q
            WHERE q.NROEMPRESA COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci
            GROUP BY q.NROEMPRESA, q.DEP, q.CODRUA, q.NROPREDIO, q.NROAPARTAMENTO, q.NROSALA
        ) lr
          ON lr.NROEMPRESA COLLATE utf8mb4_unicode_ci = o.CD COLLATE utf8mb4_unicode_ci
         AND lr.DEP COLLATE utf8mb4_unicode_ci = o.DEP COLLATE utf8mb4_unicode_ci
         AND lr.CODRUA COLLATE utf8mb4_unicode_ci = o.RUA COLLATE utf8mb4_unicode_ci
         AND lr.NROPREDIO COLLATE utf8mb4_unicode_ci = o.PRED COLLATE utf8mb4_unicode_ci
         AND lr.NROAPARTAMENTO COLLATE utf8mb4_unicode_ci = o.AP COLLATE utf8mb4_unicode_ci
         AND lr.NROSALA COLLATE utf8mb4_unicode_ci = o.SL COLLATE utf8mb4_unicode_ci
        LEFT JOIN qv00_layout_visitas qcur
          ON qcur.NROEMPRESA COLLATE utf8mb4_unicode_ci = o.CD COLLATE utf8mb4_unicode_ci
         AND qcur.DEP COLLATE utf8mb4_unicode_ci = o.DEP COLLATE utf8mb4_unicode_ci
         AND qcur.CODRUA COLLATE utf8mb4_unicode_ci = o.RUA COLLATE utf8mb4_unicode_ci
         AND qcur.NROPREDIO COLLATE utf8mb4_unicode_ci = o.PRED COLLATE utf8mb4_unicode_ci
         AND qcur.NROAPARTAMENTO COLLATE utf8mb4_unicode_ci = o.AP COLLATE utf8mb4_unicode_ci
         AND qcur.NROSALA COLLATE utf8mb4_unicode_ci = o.SL COLLATE utf8mb4_unicode_ci
        WHERE {" AND ".join(where)}
    """
    full_params = [str(cd)] + params
    df = fetch_df(q, tuple(full_params))
    if df.empty:
        return df

    for c in df.columns:
        df[c] = df[c].fillna("").astype(str).str.strip()
    df["DEP"] = df["DEP"].apply(lambda x: zfill_if_digit(x, 2))
    df["RUA"] = df["RUA"].apply(lambda x: zfill_if_digit(x, 3))
    df["GRUPO_LINHA"] = df["LINHA"].apply(grupo_linha)
    df["SUBCURVA_ENDERECO_FUTURO"] = df["SL"].apply(subcurva_slot_por_sala)
    df["CURVA_ENDERECO_FUTURO"] = df["SUBCURVA_ENDERECO_FUTURO"].apply(macrocurva_operacional)

    disp = df.apply(lambda r: disponibilidade_endereco(r["STATUS"], r["CODIGO_OCUPANTE"]), axis=1)
    df["DISPONIBILIDADE_DESTINO"] = disp.apply(lambda x: x[0])
    df["DISP_RANK"] = disp.apply(lambda x: x[1])
    df["ENDERECO_VAZIO"] = df["DISPONIBILIDADE_DESTINO"].isin(["LIVRE", "SEM_OCUPANTE"])
    return df


def disponibilidade_endereco(status: str, codigo_ocupante: str) -> Tuple[str, int]:
    s = norm_str(status).upper()
    ocupado = norm_str(codigo_ocupante) != ""
    if any(k in s for k in ["LIVRE", "VAZIO", "DISP", "EMPTY"]) and not ocupado:
        return ("LIVRE", 1)
    if not ocupado:
        return ("SEM_OCUPANTE", 2)
    if any(k in s for k in ["MOV", "TRANSF", "PEND"]):
        return ("PREV_MOV", 3)
    return ("OCUPADO", 4)


# =========================================================
# BLOCO 9 — GERAÇÃO DE PROPOSTAS
# =========================================================
def montar_universos(base_all: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if base_all.empty:
        return pd.DataFrame(), pd.DataFrame()
    base_apta = base_all[base_all["ELEGIVEL_SWAP"]].copy()
    inconsist = base_all[~base_all["ELEGIVEL_SWAP"]].copy()

    # fora de aderência
    base_de = base_apta[
        base_apta["CURVA_FINAL_PRODUTO"].isin(["P", "Q", "R"]) &
        base_apta["CURVA_ENDERECO_MACRO"].isin(["P", "Q", "R"]) &
        (base_apta["CURVA_FINAL_PRODUTO"] != base_apta["CURVA_ENDERECO_MACRO"])
    ].copy()

    base_de = base_de.sort_values(
        by=["SCORE", "GAP_CURVA", "VISITAS_PERIODO", "DIAS_COBERTURA"],
        ascending=[False, False, False, True]
    ).reset_index(drop=True)
    return base_de, inconsist


def rankear_candidatos(candidatos: pd.DataFrame, origem: pd.Series) -> pd.DataFrame:
    if candidatos.empty:
        return candidatos

    out = candidatos.copy()
    out["MESMO_GRUPO"] = out["GRUPO_LINHA"].astype(str).eq(str(origem.get("GRUPO_LINHA", "")))
    out["MESMO_DEP"] = out["DEP"].astype(str).eq(str(origem.get("DEP", "")))
    out["MESMA_RUA"] = out["RUA"].astype(str).eq(str(origem.get("RUA", "")))
    out["CURVA_EXATA_RANK"] = np.where(out["CURVA_ENDERECO_FUTURO"] == origem["CURVA_FINAL_PRODUTO"], 1, 2)
    out["MESMO_GRUPO_RANK"] = np.where(out["MESMO_GRUPO"], 1, 2)
    out["MESMO_DEP_RANK"] = np.where(out["MESMO_DEP"], 1, 2)
    out["MESMA_RUA_RANK"] = np.where(out["MESMA_RUA"], 1, 2)
    out["SL_NUM"] = out["SL"].apply(safe_int)

    out = out.sort_values(
        by=[
            "CURVA_EXATA_RANK",
            "DISP_RANK",
            "MESMO_GRUPO_RANK",
            "MESMO_DEP_RANK",
            "MESMA_RUA_RANK",
            "SL_NUM",
            "ENDERECO",
        ],
        ascending=[True, True, True, True, True, True, True]
    ).reset_index(drop=True)
    return out


def gerar_propostas_de_para(base_de: pd.DataFrame, base_para: pd.DataFrame, somente_destino_vazio: bool) -> pd.DataFrame:
    if base_de.empty or base_para.empty:
        return pd.DataFrame()

    propostas = []
    destinos_usados = set()
    codigos_movidos = set()

    for _, origem in base_de.iterrows():
        codigo = origem["CODIGO"]
        if codigo in codigos_movidos:
            continue

        curva_alvo = origem["CURVA_FINAL_PRODUTO"]
        endereco_atual = origem["ENDERECO_ATUAL_VALIDADO"] or origem["ENDERECO"]
        grupo_l = origem["GRUPO_LINHA"]

        pool = base_para[
            (base_para["CD"] == origem["CD"]) &
            (base_para["ENDERECO"] != endereco_atual) &
            (~base_para["ENDERECO"].isin(destinos_usados)) &
            (base_para["CURVA_ENDERECO_FUTURO"] == curva_alvo)
        ].copy()

        if pool.empty:
            continue

        # não jogar produto para o próprio endereço ou para endereço já ocupado pelo mesmo SKU
        pool = pool[pool["CODIGO_OCUPANTE"].astype(str) != str(codigo)].copy()
        if pool.empty:
            continue

        # preferir mesmo grupo, mas não obrigar
        pool = rankear_candidatos(pool, origem)

        if somente_destino_vazio:
            pool_vazio = pool[pool["ENDERECO_VAZIO"]].copy()
            if pool_vazio.empty:
                continue
            pool = pool_vazio

        if pool.empty:
            continue

        destino = pool.iloc[0]
        destinos_usados.add(destino["ENDERECO"])
        codigos_movidos.add(codigo)

        tipo_mov = "REALOCAÇÃO PARA VAZIO" if bool(destino.get("ENDERECO_VAZIO", False)) else "TROCA ASSISTIDA"

        propostas.append({
            "CD": origem["CD"],
            "DEP": origem["DEP"],
            "RUA_ORIGEM": origem["RUA"],
            "GRUPO_LINHA": origem["GRUPO_LINHA"],
            "LINHA": origem["LINHA"],
            "CODIGO": origem["CODIGO"],
            "DESCRICAO": origem["DESCRICAO"],
            "VISITAS_PERIODO": origem["VISITAS_PERIODO"],
            "PERC_ACUM_PARETO": origem.get("PERC_ACUM_PARETO", np.nan),
            "FAIXA_PARETO": origem.get("FAIXA_PARETO", ""),
            "CURVA_VISITA": origem["CURVA_VISITA"],
            "SAIDA_MEDIA_DIA_90D": origem["SAIDA_MEDIA_DIA_90D"],
            "PICO_SAIDA_DIA": origem["PICO_SAIDA_DIA"],
            "OUTLIER_FATOR": origem["OUTLIER_FATOR"],
            "SINAL_OUTLIER": origem["SINAL_OUTLIER"],
            "ESTOQUE_CD": origem["ESTOQUE_CD"],
            "DIAS_COBERTURA": origem["DIAS_COBERTURA"],
            "CURVA_REPOSICAO": origem["CURVA_REPOSICAO"],
            "CURVA_FINAL_PRODUTO": origem["CURVA_FINAL_PRODUTO"],
            "ENDERECO_ATUAL": endereco_atual,
            "SUBCURVA_ENDERECO_ATUAL": origem["SUBCURVA_ENDERECO_ATUAL"],
            "CURVA_ENDERECO_ATUAL": origem["CURVA_ENDERECO_MACRO"],
            "ENDERECO_SUGERIDO": destino["ENDERECO"],
            "SUBCURVA_ENDERECO_FUTURO": destino["SUBCURVA_ENDERECO_FUTURO"],
            "CURVA_ENDERECO_FUTURO": destino["CURVA_ENDERECO_FUTURO"],
            "DISPONIBILIDADE_DESTINO": destino["DISPONIBILIDADE_DESTINO"],
            "TIPO_MOVIMENTACAO": tipo_mov,
            "PRIORIDADE_SWAP": origem["PRIORIDADE_SWAP"],
            "RISCO_RUPTURA": origem["RISCO_RUPTURA"],
            "DIAS_PARA_RUPTURA": origem["DIAS_PARA_RUPTURA"],
            "FREQ_REABAST": origem["FREQ_REABAST"],
            "SCORE": origem["SCORE"],
            "JUSTIFICATIVA_SWAP": origem["JUSTIFICATIVA_SWAP"],
        })

    if not propostas:
        return pd.DataFrame()

    out = pd.DataFrame(propostas)
    out = out.sort_values(["SCORE", "VISITAS_PERIODO"], ascending=[False, False]).reset_index(drop=True)
    return out


# =========================================================
# BLOCO 10 — TABELA DE SALVAMENTO
# =========================================================
def ensure_melhoria_table() -> None:
    conn = get_conn()
    conn.ping(reconnect=True, attempts=2, delay=1)
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_melhoria_continua_v21 (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                dt_ref DATE,
                cd VARCHAR(10),
                dep VARCHAR(10),
                codigo VARCHAR(50),
                descricao VARCHAR(255),
                endereco_origem VARCHAR(80),
                endereco_destino VARCHAR(80),
                curva_visita CHAR(1),
                curva_reposicao CHAR(1),
                curva_final CHAR(1),
                curva_end_origem CHAR(1),
                curva_end_destino CHAR(1),
                visitas_90d INT,
                media_saida_dia DECIMAL(18,4),
                dias_cobertura DECIMAL(18,4),
                prioridade VARCHAR(30),
                motivo VARCHAR(255),
                tipo_movimentacao VARCHAR(40),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    finally:
        cur.close()


def salvar_propostas_no_banco(df: pd.DataFrame) -> Tuple[bool, str]:
    if df.empty:
        return False, "Nada para salvar."

    ensure_melhoria_table()
    conn = get_conn()
    conn.ping(reconnect=True, attempts=2, delay=1)
    cur = conn.cursor()
    try:
        sql = """
            INSERT INTO app_melhoria_continua_v21 (
                dt_ref, cd, dep, codigo, descricao,
                endereco_origem, endereco_destino,
                curva_visita, curva_reposicao, curva_final,
                curva_end_origem, curva_end_destino,
                visitas_90d, media_saida_dia, dias_cobertura,
                prioridade, motivo, tipo_movimentacao
            ) VALUES (
                CURDATE(), %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s, %s
            )
        """
        payload = []
        for _, r in df.iterrows():
            payload.append((
                r.get("CD", ""),
                r.get("DEP", ""),
                r.get("CODIGO", ""),
                r.get("DESCRICAO", ""),
                r.get("ENDERECO_ATUAL", ""),
                r.get("ENDERECO_SUGERIDO", ""),
                r.get("CURVA_VISITA", ""),
                r.get("CURVA_REPOSICAO", ""),
                r.get("CURVA_FINAL_PRODUTO", ""),
                r.get("CURVA_ENDERECO_ATUAL", ""),
                r.get("CURVA_ENDERECO_FUTURO", ""),
                safe_int(r.get("VISITAS_PERIODO", 0)),
                safe_float(r.get("SAIDA_MEDIA_DIA_90D", 0)),
                safe_float(r.get("DIAS_COBERTURA", 0)),
                r.get("PRIORIDADE_SWAP", ""),
                r.get("JUSTIFICATIVA_SWAP", ""),
                r.get("TIPO_MOVIMENTACAO", ""),
            ))
        cur.executemany(sql, payload)
        conn.commit()
        return True, f"{len(payload)} proposta(s) salva(s) com sucesso."
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao salvar propostas: {e}"
    finally:
        cur.close()


# =========================================================
# BLOCO 11 — EXECUÇÕES EXISTENTES
# =========================================================
@st.cache_data(ttl=120, show_spinner=False)
def load_execucoes_existentes(cd: str) -> pd.DataFrame:
    tabelas = [t for t in ["swap_execucoes", "swap_propostas_exec", "app_swap_execucoes_s06"] if table_exists(t)]
    frames = []
    for t in tabelas:
        try:
            cols = get_table_columns(t)
            cd_col = next((c for c in ["CD", "NROEMPRESA"] if c in cols), None)
            if not cd_col:
                continue
            q = f"SELECT * FROM {t} WHERE CAST({cd_col} AS CHAR) = %s LIMIT 500"
            df = fetch_df(q, (str(cd),))
            if not df.empty:
                df["FONTE_EXECUCAO"] = t
                frames.append(df)
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# =========================================================
# BLOCO 12 — SIMULAÇÃO ANTES / DEPOIS
# =========================================================
def simular_aderencia(base_de: pd.DataFrame, propostas: pd.DataFrame) -> Dict[str, float]:
    if base_de.empty:
        return {"antes": 100.0, "depois": 100.0, "ganho_itens": 0}
    total = len(base_de)
    antes_ok = int((base_de["CURVA_FINAL_PRODUTO"] == base_de["CURVA_ENDERECO_MACRO"]).sum())
    depois_ok = antes_ok + len(propostas)
    return {
        "antes": round((antes_ok / total) * 100, 1) if total else 100.0,
        "depois": round((min(depois_ok, total) / total) * 100, 1) if total else 100.0,
        "ganho_itens": int(len(propostas)),
    }


def ranking_ruas_problematicas(base_de: pd.DataFrame) -> pd.DataFrame:
    if base_de.empty:
        return pd.DataFrame()
    df = (
        base_de.groupby(["DEP", "RUA"], as_index=False)
        .agg(
            itens_fora_curva=("CODIGO", "count"),
            visitas=("VISITAS_PERIODO", "sum"),
            score_medio=("SCORE", "mean"),
        )
        .sort_values(["itens_fora_curva", "visitas", "score_medio"], ascending=[False, False, False])
        .reset_index(drop=True)
    )
    df["score_medio"] = df["score_medio"].round(1)
    return df


# =========================================================
# BLOCO 13 — RENDERIZAÇÃO SEGURA
# =========================================================
def style_outlier_table(df: pd.DataFrame):
    """
    Evita quebrar o Streamlit com Styler em bases gigantes.
    """
    if df.empty:
        return df

    total_cells = int(df.shape[0] * df.shape[1])
    if total_cells > 200_000:
        return df

    def row_style(row):
        sinal = str(row.get("SINAL_OUTLIER", ""))
        if "OUTLIER" in sinal:
            return ["background-color: #ffe6e6"] * len(row)
        if "ATENÇÃO" in sinal:
            return ["background-color: #fff6db"] * len(row)
        return [""] * len(row)

    try:
        return df.style.apply(row_style, axis=1)
    except Exception:
        return df



def render_outlier_table(df: pd.DataFrame, **kwargs):
    """
    Renderização segura da tabela:
    - style_outlier_table() pode retornar DataFrame normal ou Styler
    - st.dataframe() aceita ambos diretamente
    - não usamos pd.io.formats.style.Styler porque esse caminho varia entre versões do pandas
    """
    obj = style_outlier_table(df)
    st.dataframe(obj, **kwargs)


# =========================================================
# BLOCO 14 — ESTADO / SIDEBAR
# =========================================================
st.title("📦 WMS Apanha — Swap Inteligente V21.6.3.5.1")
st.caption("Versão com validação de endereço, saneamento por Plan06, macrocurva operacional correta, Pareto 20/50/70/100 e filtros por linha agrupada e faixa Pareto.")

if "swap_v2163_filters" not in st.session_state:
    st.session_state["swap_v2163_filters"] = {
        "cd": "164",
        "dep": "",
        "rua": "",
        "dias": 90,
        "limite": 20000,
        "somente_destino_vazio": False,
    }

if "swap_v2163_propostas_cache" not in st.session_state:
    st.session_state["swap_v2163_propostas_cache"] = pd.DataFrame()

with st.sidebar:
    st.header("Filtros da carga")
    with st.form("swap_v2163_form"):
        defaults = st.session_state["swap_v2163_filters"]
        cd = st.selectbox("CD", ["164", "264", "364", "464"], index=["164", "264", "364", "464"].index(defaults["cd"]))
        dep = st.text_input("DEP (opcional)", value=defaults["dep"])
        rua = st.text_input("Rua (opcional)", value=defaults["rua"])
        dias = st.selectbox("Período analítico", [30, 60, 90, 120, 180], index=[30, 60, 90, 120, 180].index(defaults["dias"]))
        limite = st.selectbox("Limite de leitura", [5000, 10000, 20000, 50000], index=[5000, 10000, 20000, 50000].index(defaults["limite"]))
        somente_destino_vazio = st.checkbox("Usar apenas destino vazio na troca", value=defaults["somente_destino_vazio"])
        aplicar = st.form_submit_button("Aplicar carga", use_container_width=True)

if aplicar:
    st.session_state["swap_v2163_filters"] = {
        "cd": cd,
        "dep": dep,
        "rua": rua,
        "dias": dias,
        "limite": limite,
        "somente_destino_vazio": somente_destino_vazio,
    }
    st.session_state["swap_v2163_propostas_cache"] = pd.DataFrame()

applied = st.session_state["swap_v2163_filters"]
cd = applied["cd"]
dep = zfill_if_digit(applied["dep"], 2) if applied["dep"].strip() else None
rua = zfill_if_digit(applied["rua"], 3) if applied["rua"].strip() else None
dias_analitico = int(applied["dias"])
limite = int(applied["limite"])
somente_destino_vazio = bool(applied["somente_destino_vazio"])


# =========================================================
# BLOCO 15 — CARGA PRINCIPAL
# =========================================================
with st.spinner("Carregando base do Swap Inteligente V21.6.3.5.1..."):
    base_raw = load_base_historica(cd=cd, dep=dep, rua=rua, dias_analitico=dias_analitico, limit=limite)
    plan06 = load_plan06(cd)
    end_valid = load_endereco_validado(cd)

    if not base_raw.empty:
        base_raw = aplicar_pareto_por_grupo(base_raw)
        base_raw = adicionar_metricas_saida(base_raw, dias_analitico)
        base_all = consolidar_base(base_raw, plan06, end_valid)
        base_de, inconsist = montar_universos(base_all)
        base_para = load_base_para(cd=cd, dep=dep, rua=rua)
    else:
        base_all = pd.DataFrame()
        base_de = pd.DataFrame()
        inconsist = pd.DataFrame()
        base_para = pd.DataFrame()

if base_all.empty:
    st.warning("Nenhum dado encontrado para os filtros selecionados.")
    st.stop()


# =========================================================
# BLOCO 16 — FILTROS FINOS E GERAÇÃO
# =========================================================
colf1, colf2, colf3, colf4, colf5, colf6 = st.columns([1.25, 1.2, 1.05, 1.0, 1.0, 1.6])
with colf1:
    grupo_opts = ["TODOS"] + sorted([x for x in base_de["GRUPO_LINHA"].dropna().astype(str).unique().tolist() if x != ""])
    grupo_sel = st.selectbox("Linha agrupada", grupo_opts, index=0)
with colf2:
    linha_opts = ["TODAS"] + sorted([x for x in base_de["LINHA"].dropna().astype(str).unique().tolist() if x != ""])
    linha_sel = st.selectbox("Linha detalhada", linha_opts, index=0)
with colf3:
    faixa_opts = ["TODAS", "P20", "P50", "P70", "P100"]
    faixa_sel = st.selectbox("Faixa Pareto", faixa_opts, index=0)
with colf4:
    prioridade_opts = ["TODAS"] + sorted([x for x in base_de["PRIORIDADE_SWAP"].dropna().astype(str).unique().tolist() if x != ""])
    prioridade_sel = st.selectbox("Prioridade", prioridade_opts, index=0)
with colf5:
    curva_opts = ["TODAS", "P", "Q", "R"]
    curva_sel = st.selectbox("Macrocurva", curva_opts, index=0)
with colf6:
    termo = st.text_input("Buscar produto / descrição / endereço")

base_view = base_de.copy()
if grupo_sel != "TODOS":
    base_view = base_view[base_view["GRUPO_LINHA"] == grupo_sel]
if linha_sel != "TODAS":
    base_view = base_view[base_view["LINHA"] == linha_sel]
if faixa_sel != "TODAS":
    base_view = base_view[base_view["FAIXA_PARETO"] == faixa_sel]
if prioridade_sel != "TODAS":
    base_view = base_view[base_view["PRIORIDADE_SWAP"] == prioridade_sel]
if curva_sel != "TODAS":
    base_view = base_view[base_view["CURVA_FINAL_PRODUTO"] == curva_sel]
if termo.strip():
    t = termo.strip().lower()
    mask = (
        base_view["CODIGO"].astype(str).str.lower().str.contains(t, na=False) |
        base_view["DESCRICAO"].astype(str).str.lower().str.contains(t, na=False) |
        base_view["ENDERECO_ATUAL_VALIDADO"].astype(str).str.lower().str.contains(t, na=False)
    )
    base_view = base_view[mask]

# KPIs
k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
k1.metric("Itens analisados", f"{len(base_all):,}".replace(",", "."))
k2.metric("Itens elegíveis swap", f"{int(base_all['ELEGIVEL_SWAP'].sum()):,}".replace(",", "."))
total_excl = int((base_all['STATUS_ITEM_PLAN06'] == 'EXCLUIDO').sum()) if ('STATUS_ITEM_PLAN06' in base_all.columns and not base_all.empty) else 0
k3.metric("Componentes excluídos", f"{total_excl:,}".replace(",", "."))
k4.metric("Itens fora da curva", f"{len(base_de):,}".replace(",", "."))
k5.metric("Inconsistências", f"{len(inconsist):,}".replace(",", "."))
k6.metric("Risco alto", f"{int((base_all['RISCO_RUPTURA'] == 'ALTO').sum()):,}".replace(",", "."))
k7.metric("Itens P20", f"{int((base_all['FAIXA_PARETO'] == 'P20').sum()):,}".replace(",", "."))

kp1, kp2, kp3 = st.columns(3)
kp1.metric("Itens P50", f"{int((base_all['FAIXA_PARETO'] == 'P50').sum()):,}".replace(",", "."))
kp2.metric("Itens P70", f"{int((base_all['FAIXA_PARETO'] == 'P70').sum()):,}".replace(",", "."))
kp3.metric("Itens P100", f"{int((base_all['FAIXA_PARETO'] == 'P100').sum()):,}".replace(",", "."))

col_gen, col_hint = st.columns([2, 3])
with col_gen:
    gerar = st.button("Gerar sugestões DE → PARA", use_container_width=True, type="primary")
with col_hint:
    st.info("Para ver o universo completo, deixe Macrocurva = TODAS e Faixa Pareto = TODAS. O motor usa endereço validado + Plan06 antes de sugerir swap.")

if gerar:
    with st.spinner("Gerando plano de movimentação..."):
        propostas = gerar_propostas_de_para(base_view, base_para, somente_destino_vazio)
        st.session_state["swap_v2163_propostas_cache"] = propostas

propostas = st.session_state.get("swap_v2163_propostas_cache", pd.DataFrame())


# =========================================================
# BLOCO 17 — RESUMO EXECUTIVO
# =========================================================
sim = simular_aderencia(base_view, propostas if not propostas.empty else pd.DataFrame())
rr = ranking_ruas_problematicas(base_view)

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Movimentações sugeridas", f"{len(propostas):,}".replace(",", "."))
c2.metric("Destinos vazios usados", f"{int((propostas['TIPO_MOVIMENTACAO'] == 'REALOCAÇÃO PARA VAZIO').sum()):,}".replace(",", ".") if not propostas.empty else "0")
c3.metric("Trocas assistidas", f"{int((propostas['TIPO_MOVIMENTACAO'] == 'TROCA ASSISTIDA').sum()):,}".replace(",", ".") if not propostas.empty else "0")
c4.metric("Aderência antes", f"{sim['antes']:.1f}%")
c5.metric("Aderência depois", f"{sim['depois']:.1f}%")
c6.metric("Ganho de itens", f"{sim['ganho_itens']:,}".replace(",", "."))


# =========================================================
# BLOCO 18 — ABAS PRINCIPAIS
# =========================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Análise básica",
    "Plano de movimentação",
    "Itens inconsistentes",
    "Simulação antes/depois",
    "Execuções existentes",
])

with tab1:
    st.subheader("Análise básica tratada")

    unidade_saida = unidade_saida_label()
    base_show = base_view.copy()

    # Bloco de nomenclatura executiva para reduzir ambiguidade na leitura do time.
    if "PERC_ACUM_PARETO" in base_show.columns:
        base_show["%_PARETO_ACUM."] = base_show["PERC_ACUM_PARETO"].apply(format_percent_value)
    if "FAIXA_PARETO" in base_show.columns:
        base_show["FAIXA_PARETO_%"] = base_show["FAIXA_PARETO"].apply(label_faixa_pareto_pct)
    if "CURVA_VISITA" in base_show.columns:
        base_show["CURVA_VISITA_ATUAL"] = base_show["CURVA_VISITA"]
    if "CURVA_FINAL_PRODUTO" in base_show.columns:
        base_show["CURVA_FINAL_SUGERIDA"] = base_show["CURVA_FINAL_PRODUTO"]
    if "VISITAS_PERIODO" in base_show.columns:
        base_show[f"VISITAS_{dias_analitico}D"] = base_show["VISITAS_PERIODO"]
    if "PICO_SAIDA_DIA" in base_show.columns:
        base_show[f"PICO_SAIDA_DIA_ATIVO_{unidade_saida}"] = base_show["PICO_SAIDA_DIA"]
    if "SAIDA_MEDIA_DIA_90D" in base_show.columns:
        base_show[f"SAIDA_MEDIA_DIA_{dias_analitico}D_{unidade_saida}"] = base_show["SAIDA_MEDIA_DIA_90D"]

    st.caption(
        f"Visitas consideradas no período analítico selecionado ({dias_analitico} dias). "
        f"'Curva visita atual' = curva por giro/Pareto. "
        f"'Curva final sugerida' = curva usada na decisão logística após combinar visita + reposição. "
        f"'Pico saída dia ativo' = intensidade média nos dias com saída, na unidade-base da fonte ({unidade_saida})."
    )

    cols_base = [
        "CD", "DEP", "RUA", "GRUPO_LINHA", "LINHA", "CODIGO", "DESCRICAO",
        "ENDERECO_ATUAL_VALIDADO",
        f"VISITAS_{dias_analitico}D", "%_PARETO_ACUM.", "FAIXA_PARETO_%",
        "CURVA_VISITA_ATUAL",
        f"SAIDA_MEDIA_DIA_{dias_analitico}D_{unidade_saida}",
        f"PICO_SAIDA_DIA_ATIVO_{unidade_saida}",
        "OUTLIER_FATOR", "SINAL_OUTLIER", "JUSTIFICATIVA_OUTLIER",
        "ESTOQUE_CD", "DIAS_COBERTURA", "CURVA_REPOSICAO",
        "SUBCURVA_ENDERECO_ATUAL", "CURVA_ENDERECO_MACRO",
        "CURVA_FINAL_SUGERIDA", "RISCO_RUPTURA", "FREQ_REABAST",
        "STATUS_ITEM_PLAN06", "MOTIVO_EXCLUSAO", "ELEGIVEL_SWAP",
    ]
    cols_base = [c for c in cols_base if c in base_show.columns]
    render_outlier_table(base_show[cols_base], use_container_width=True, hide_index=True)

with tab2:
    st.subheader("Plano de movimentação")
    if propostas.empty:
        st.warning("Nenhuma proposta gerada. Se o volume parecer baixo, valide Curva produto = TODAS e se a base de destino está populada.")
    else:
        sc1, sc2 = st.columns([1, 1.8])
        with sc1:
            if st.button("💾 Salvar propostas no banco", use_container_width=True):
                ok, msg = salvar_propostas_no_banco(propostas)
                st.success(msg) if ok else st.error(msg)
        with sc2:
            st.download_button(
                "⬇️ Baixar CSV — Plano de Movimentação",
                data=export_csv(propostas),
                file_name=f"swap_v2163_cd_{cd}.csv",
                mime="text/csv",
                use_container_width=True,
            )

        props_show = propostas.copy()
        unidade_saida = unidade_saida_label()
        if "PERC_ACUM_PARETO" in props_show.columns:
            props_show["%_PARETO_ACUM."] = props_show["PERC_ACUM_PARETO"].apply(format_percent_value)
        if "FAIXA_PARETO" in props_show.columns:
            props_show["FAIXA_PARETO_%"] = props_show["FAIXA_PARETO"].apply(label_faixa_pareto_pct)
        if "CURVA_VISITA" in props_show.columns:
            props_show["CURVA_VISITA_ATUAL"] = props_show["CURVA_VISITA"]
        if "CURVA_FINAL_PRODUTO" in props_show.columns:
            props_show["CURVA_FINAL_SUGERIDA"] = props_show["CURVA_FINAL_PRODUTO"]
        if "VISITAS_PERIODO" in props_show.columns:
            props_show[f"VISITAS_{dias_analitico}D"] = props_show["VISITAS_PERIODO"]
        if "PICO_SAIDA_DIA" in props_show.columns:
            props_show[f"PICO_SAIDA_DIA_ATIVO_{unidade_saida}"] = props_show["PICO_SAIDA_DIA"]
        if "SAIDA_MEDIA_DIA_90D" in props_show.columns:
            props_show[f"SAIDA_MEDIA_DIA_{dias_analitico}D_{unidade_saida}"] = props_show["SAIDA_MEDIA_DIA_90D"]

        cols_show = [
            "CD", "DEP", "GRUPO_LINHA", "LINHA", "CODIGO", "DESCRICAO",
            f"VISITAS_{dias_analitico}D", "%_PARETO_ACUM.", "FAIXA_PARETO_%", "CURVA_VISITA_ATUAL",
            f"SAIDA_MEDIA_DIA_{dias_analitico}D_{unidade_saida}", f"PICO_SAIDA_DIA_ATIVO_{unidade_saida}",
            "OUTLIER_FATOR", "SINAL_OUTLIER", "JUSTIFICATIVA_OUTLIER", "ESTOQUE_CD", "DIAS_COBERTURA",
            "CURVA_REPOSICAO", "CURVA_FINAL_SUGERIDA",
            "ENDERECO_ATUAL", "SUBCURVA_ENDERECO_ATUAL", "CURVA_ENDERECO_ATUAL",
            "ENDERECO_SUGERIDO", "SUBCURVA_ENDERECO_FUTURO", "CURVA_ENDERECO_FUTURO",
            "TIPO_MOVIMENTACAO", "DISPONIBILIDADE_DESTINO", "PRIORIDADE_SWAP",
            "RISCO_RUPTURA", "DIAS_PARA_RUPTURA", "FREQ_REABAST", "SCORE", "JUSTIFICATIVA_SWAP",
        ]
        cols_show = [c for c in cols_show if c in props_show.columns]
        render_outlier_table(props_show[cols_show], use_container_width=True, hide_index=True)

with tab3:
    st.subheader("Itens inconsistentes / fora do universo de swap")
    if inconsist.empty:
        st.success("Nenhuma inconsistência relevante encontrada para os filtros atuais.")
    else:
        cols_inc = [
            "CD", "DEP", "RUA", "CODIGO", "DESCRICAO", "ENDERECO_ATUAL_VALIDADO",
            "FONTE_ENDERECO", "STATUS_PRODUTO", "ENDERECADO", "TEM_ESTOQUE",
            "ESTOQUE_CD", "MODALIDADE_CD", "STATUS_ITEM_PLAN06", "MOTIVO_EXCLUSAO",
            "JUSTIFICATIVA_SWAP"
        ]
        cols_inc = [c for c in cols_inc if c in inconsist.columns]
        st.dataframe(inconsist[cols_inc], use_container_width=True, hide_index=True)

with tab4:
    st.subheader("Simulação antes / depois")
    a1, a2 = st.columns([1, 1])
    with a1:
        st.markdown("### Ranking de ruas problemáticas")
        if rr.empty:
            st.info("Sem ruas críticas para exibir.")
        else:
            st.dataframe(rr, use_container_width=True, hide_index=True)
    with a2:
        st.markdown("### Resumo da aderência")
        sim_df = pd.DataFrame([{
            "ADERENCIA_ANTES_%": sim["antes"],
            "ADERENCIA_DEPOIS_%": sim["depois"],
            "GANHO_ITENS": sim["ganho_itens"],
            "MOVIMENTACOES": len(propostas),
        }])
        st.dataframe(sim_df, use_container_width=True, hide_index=True)

with tab5:
    st.subheader("Execuções existentes")
    exec_df = load_execucoes_existentes(cd)
    if exec_df.empty:
        st.info("Nenhuma execução encontrada nas tabelas conhecidas para este CD.")
    else:
        st.dataframe(exec_df, use_container_width=True, hide_index=True)


# =========================================================
# BLOCO 19 — RODAPÉ EXECUTIVO RECOLHÍVEL
# =========================================================
st.markdown("---")
with st.expander("🟦 Rodapé executivo — regra operacional, leitura e observações", expanded=False):
    st.markdown(f"""
**Regra oficial de reposição**
- **P** = até 2 dias de cobertura
- **Q** = acima de 2 até 10 dias
- **R** = acima de 10 dias

**Regra física do slot**
- **P1 / P2** = curva **P**
- **P4** = curva **Q**
- **P6 / P12 em diante** = curva **R**

**Aderência operacional**
- O produto é classificado por:
  1. Pareto de visitas no período analítico
  2. Cobertura em dias usando **saída média por dia**
- A **curva final do produto** assume o pior caso entre visita e reposição.
- A aderência compara:
  - **curva final do produto**
  - **macrocurva do endereço atual**

**Validação antes de sugerir swap**
- O item só entra no plano principal quando:
  - possui **endereço validado**
  - está **apto** na `app_plan06`
  - não está em situação impeditiva de saneamento

**Leitura das colunas**
- `SAIDA_MEDIA_DIA_90D` = saída média distribuída na janela de {dias_analitico} dias
- `PICO_SAIDA_DIA_ATIVO_BASE` = intensidade média nos dias com saída, na unidade-base da fonte
- `OUTLIER_FATOR` = quanto o pico dos dias ativos destoa da média da janela
- `SINAL_OUTLIER` = alerta visual de concentração real de saída (volume relevante em poucos dias)

**Objetivo desta V21.6.3**
- separar problema de **layout** de problema de **cadastro / ocupação**
- reduzir falsos `SEM INFO`
- ampliar volume de sugestões sem perder aderência
    """)


