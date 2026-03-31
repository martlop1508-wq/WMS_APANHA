#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Página Streamlit - Importação ETL V4 (Refatorada com auth.py + fix DateParseError)
Upload manual + histórico + pastas + temporal. DB unificado.
"""

import os
import re
import hashlib
from pathlib import Path
from datetime import datetime, date
import pandas as pd
import streamlit as st
from auth import require_login, require_role, _db_params, _conn
import pymysql

st.set_page_config(page_title="Importação ETL V4", layout="wide")

require_login()
require_role("admin", "analista", "gestor")

# Configs
BASE_DIR = Path("/opt/wms_apanha")
IMPORT_DIR = Path(os.getenv("IMPORT_DIR", str(BASE_DIR / "import")))
ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", str(IMPORT_DIR / "archive")))
HOLD_DIR = IMPORT_DIR / "_hold"
ALLOWED_EXTS = {".csv", ".txt", ".xlsx", ".xls"}
TIPOS_ETL = [
    "AUTO", "QV99", "QV00_LAYOUT_VISITAS", "MOV_VERTICAL_DIARIA",
    "LINHAS_SEPARACAO", "QV04_PRODUTIVIDADE", "PLAN_06"
]

def ensure_dirs():
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    HOLD_DIR.mkdir(parents=True, exist_ok=True)

@st.cache_data(ttl=30)
def get_etl_request_columns():
    with _conn() as conn:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute("SHOW COLUMNS FROM etl_requests")
        return [r['Field'] for r in cur.fetchall()]

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def detect_tipo_by_filename(filename: str) -> str:
    low = filename.lower()
    if "qv99" in low and "ocup" in low:
        return "QV99"
    if "qv99" in low:
        return "QV99"
    if "qv00" in low and ("layout" in low or "visita" in low):
        return "QV00_LAYOUT_VISITAS"
    if "layout_cd" in low or "layoutcd" in low:
        return "QV00_LAYOUT_VISITAS"
    if "vertical" in low:
        return "MOV_VERTICAL_DIARIA"
    if "linhas de separacao" in low or "linhas_de_separacao" in low or "separacao geral" in low:
        return "LINHAS_SEPARACAO"
    if "qv04" in low or "produtividade" in low:
        return "QV04_PRODUTIVIDADE"
    if "plan_06" in low or "plan06" in low:
        return "PLAN_06"
    return "AUTO"

def sanitize_filename(name: str) -> str:
    name = os.path.basename(name).strip()
    name = re.sub(r"[^\w\-.() áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ]", "_", name)
    return name

def insert_etl_request(data: dict):
    cols_available = set(get_etl_request_columns())
    cols = [k for k, v in data.items() if k in cols_available]
    if not cols:
        raise RuntimeError("Nenhuma coluna compatível.")
    vals = ["%s"] * len(cols)
    params = [data[k] for k in cols]
    sql = f"INSERT INTO etl_requests ({', '.join(cols)}) VALUES ({', '.join(vals)})"
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()

@st.cache_data(ttl=15)
def load_requests(limit=500):
    with _conn() as conn:
        return pd.read_sql(f"SELECT * FROM etl_requests ORDER BY id DESC LIMIT {limit}", conn)

@st.cache_data(ttl=30)
def load_current_state(limit=300):
    with _conn() as conn:
        return pd.read_sql(
            f"SELECT tipo, cd, business_key, ref_date, updated_at FROM etl_current_state ORDER BY updated_at DESC LIMIT {limit}",
            conn
        )

@st.cache_data(ttl=30)
def load_history_state(limit=300):
    with _conn() as conn:
        return pd.read_sql(
            f"SELECT tipo, cd, business_key, ref_date, is_current, valid_from, valid_to, updated_at FROM etl_history_state ORDER BY updated_at DESC LIMIT {limit}",
            conn
        )

def save_uploaded_file(uploaded_file, target_dir: Path):
    ensure_dirs()
    safe_name = sanitize_filename(uploaded_file.name)
    data = uploaded_file.getbuffer().tobytes()
    dest = target_dir / safe_name
    if dest.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = f"{dest.stem}_{timestamp}{dest.suffix}"
        dest = target_dir / safe_name
    with open(dest, "wb") as f:
        f.write(data)
    return safe_name, len(data), sha256_bytes(data)

def _list_files(folder: Path) -> pd.DataFrame:
    ensure_dirs()
    rows = []
    if folder.exists():
        for p in sorted(folder.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file():
                rows.append({
                    "nome": p.name,
                    "tamanho_kb": round(p.stat().st_size / 1024, 2),
                    "modificado_em": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })
    return pd.DataFrame(rows)

def style_status(val):
    sval = str(val).upper()
    if sval == "DONE":
        return "background-color: #cfe9cf; color: #0b5d0b; font-weight: 700;"
    if sval == "ERROR":
        return "background-color: #f5cccc; color: #8b0000; font-weight: 700;"
    if sval == "PENDING":
        return "background-color: #eadcff; color: #5e2ca5; font-weight: 700;"
    if sval == "RUNNING":
        return "background-color: #fff0c2; color: #8a5a00; font-weight: 700;"
    if sval == "CANCELADO":
        return "background-color: #ececec; color: #444; font-weight: 700;"
    return ""

ensure_dirs()
st.title("📥 Importação ETL V4 — Histórico Temporal")
st.caption(f"Entrada: {IMPORT_DIR} | Sucesso: {ARCHIVE_DIR} | Erro: {HOLD_DIR}")

tab1, tab2, tab3, tab4 = st.tabs(["Enviar arquivo(s)", "Histórico ETL", "Arquivos / pastas", "Histórico temporal"])

# =====================================================================
# TAB 1 - UPLOAD MANUAL
# =====================================================================
with tab1:
    col_u1, col_u2 = st.columns([2, 1])
    
    with col_u1:
        st.subheader("Envio manual pela página")
        uploaded_files = st.file_uploader(
            "Selecione o(s) arquivo(s)",
            type=list(ALLOWED_EXTS),
            accept_multiple_files=True,
            key="etl_v4_upload_manual"
        )
        
        solicitante = st.text_input("Solicitante", value="manual", key="etl_v4_solicitante")
        cd_manual = st.text_input("CD (opcional)", value="", key="etl_v4_cd_manual")
        tipo_manual = st.selectbox("Tipo ETL", TIPOS_ETL, index=0, key="etl_v4_tipo_manual")
        
        if st.button("Registrar arquivo(s) na fila ETL", use_container_width=True, key="etl_v4_btn_registrar"):
            if not uploaded_files:
                st.warning("Selecione ao menos um arquivo.")
            else:
                ok = 0
                erros = []
                
                for up in uploaded_files[:10]:  # Limite 10 arquivos
                    ext = Path(up.name).suffix.lower()
                    if ext not in ALLOWED_EXTS:
                        erros.append(f"{up.name}: extensão não permitida")
                        continue
                    
                    try:
                        filename, file_size, file_sha256 = save_uploaded_file(up, IMPORT_DIR)
                        detected_tipo = detect_tipo_by_filename(filename)
                        tipo_final = tipo_manual if tipo_manual != "AUTO" else detected_tipo
                        
                        payload = {
                            "tipo": tipo_final,
                            "cd": cd_manual.strip() or None,
                            "filename": filename,
                            "status": "PENDING",
                            "status_label": "PENDENTE",
                            "requested_by": solicitante.strip() or "manual",
                            "requested_at": datetime.now(),
                            "file_sha256": file_sha256,
                            "file_size": file_size,
                            "attempts": 0,
                            "error_count": 0,
                            "rows_loaded": 0,
                            "rows_ignored": 0,
                            "rows_error": 0,
                            "rows_history_inserted": 0,
                            "rows_current_inserted": 0,
                            "rows_current_updated": 0,
                            "rows_old_loaded": 0,
                            "rows_same_period_updated": 0,
                            "worker_name": None,
                            "message": f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Upload manual | tipo={tipo_final}",
                        }
                        
                        insert_etl_request(payload)
                        ok += 1
                    except Exception as e:
                        erros.append(f"{up.name}: {e}")
                
                # Limpa caches
                load_requests.clear()
                load_current_state.clear()
                load_history_state.clear()
                
                if ok:
                    st.success(f"✅ {ok} arquivo(s) registrado(s) com sucesso.")
                if erros:
                    st.error("❌ Ocorreram erros:\n\n" + "\n".join(erros))
    
    with col_u2:
        st.subheader("Arquivos em import/")
        dfi = _list_files(IMPORT_DIR)
        if not dfi.empty:
            st.dataframe(dfi, use_container_width=True, hide_index=True)
        else:
            st.info("Sem arquivos em import/.")

# =====================================================================
# TAB 2 - HISTÓRICO ETL (COM FIX DATEPARSE)
# =====================================================================
with tab2:
    st.subheader("Histórico operacional da fila ETL")
    
    try:
        df = load_requests(limit=500)
    except Exception as e:
        st.error(f"Erro ao carregar histórico: {e}")
        df = pd.DataFrame()
    
    if not df.empty:
        f1, f2, f3, f4 = st.columns([1, 1, 1, 1])
        
        with f1:
            status_opts = ["Todos"] + sorted(df["status"].dropna().astype(str).unique().tolist()) if "status" in df.columns else ["Todos"]
            status_sel = st.selectbox("Status", status_opts, key="etl_v4_filtro_status")
        
        with f2:
            tipo_opts = ["Todos"] + sorted(df["tipo"].dropna().astype(str).unique().tolist()) if "tipo" in df.columns else ["Todos"]
            tipo_sel = st.selectbox("Tipo", tipo_opts, key="etl_v4_filtro_tipo")
        
        with f3:
            data_inicio = st.date_input("Data Início", value=date.today(), key="etl_v4_data_inicio")
        
        with f4:
            data_fim = st.date_input("Data Fim", value=date.today(), key="etl_v4_data_fim")
        
        # FIX DATEPARSE: errors='coerce' para tratar NULL/inválidos
        df_view = df.copy()
        
        if "requested_at" in df_view.columns:
            df_view['requested_at_dt'] = pd.to_datetime(df_view['requested_at'], errors='coerce')
            df_view = df_view[df_view['requested_at_dt'].notna()]
            df_view = df_view[(df_view['requested_at_dt'].dt.date >= data_inicio) & 
                              (df_view['requested_at_dt'].dt.date <= data_fim)].copy()
            df_view.drop(columns=['requested_at_dt'], inplace=True)
        
        if status_sel != "Todos" and "status" in df_view.columns:
            df_view = df_view[df_view["status"].astype(str) == status_sel]
        
        if tipo_sel != "Todos" and "tipo" in df_view.columns:
            df_view = df_view[df_view["tipo"].astype(str) == tipo_sel]
        
        # Export CSV
        csv = df_view.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Export CSV", csv, "etl_history.csv", "text/csv", use_container_width=True)
        
        # Exibir tabela
        preferred_cols = [
            "id", "tipo", "cd", "filename", "status", "requested_by",
            "rows_loaded", "rows_ignored", "rows_error",
            "rows_history_inserted", "rows_current_inserted", "rows_current_updated",
            "rows_old_loaded", "rows_same_period_updated",
            "ref_date_min", "ref_date_max",
            "requested_at", "started_at", "processed_at", "finished_at",
            "worker_name", "message"
        ]
        
        cols_show = [c for c in preferred_cols if c in df_view.columns] + [c for c in df_view.columns if c not in preferred_cols]
        df_show = df_view[cols_show]
        
        if "status" in df_show.columns:
            st.dataframe(df_show.style.map(style_status, subset=["status"]), use_container_width=True, hide_index=True)
        else:
            st.dataframe(df_show, use_container_width=True, hide_index=True)
    else:
        st.info("Nenhum registro encontrado em etl_requests.")

# =====================================================================
# TAB 3 - PASTAS
# =====================================================================
with tab3:
    c1, c2, c3 = st.columns(3)
    
    with c1:
        st.subheader("import/")
        dfi = _list_files(IMPORT_DIR)
        st.dataframe(dfi, use_container_width=True, hide_index=True) if not dfi.empty else st.info("Sem arquivos.")
    
    with c2:
        st.subheader("import/archive")
        dfa = _list_files(ARCHIVE_DIR)
        st.dataframe(dfa, use_container_width=True, hide_index=True) if not dfa.empty else st.info("Sem arquivos.")
    
    with c3:
        st.subheader("import/_hold")
        dfh = _list_files(HOLD_DIR)
        st.dataframe(dfh, use_container_width=True, hide_index=True) if not dfh.empty else st.info("Sem arquivos.")

# =====================================================================
# TAB 4 - HISTÓRICO TEMPORAL
# =====================================================================
with tab4:
    st.subheader("Camada temporal")
    
    t1, t2 = st.columns(2)
    
    with t1:
        st.markdown("### Snapshot atual (etl_current_state)")
        try:
            df_current = load_current_state(limit=300)
            if not df_current.empty:
                st.dataframe(df_current, use_container_width=True, hide_index=True)
            else:
                st.info("Sem registros em etl_current_state.")
        except Exception as e:
            st.error(f"Erro ao ler snapshot atual: {e}")
    
    with t2:
        st.markdown("### Histórico (etl_history_state)")
        try:
            df_hist = load_history_state(limit=300)
            if not df_hist.empty:
                st.dataframe(df_hist, use_container_width=True, hide_index=True)
            else:
                st.info("Sem registros em etl_history_state.")
        except Exception as e:
            st.error(f"Erro ao ler histórico: {e}")

st.caption("💡 Cache: 15min (requests) | 30min (temporal). Refresh automático ao registrar arquivo.")
