# -*- coding: utf-8 -*-
import os
from datetime import date, timedelta

import mysql.connector
import pandas as pd
import streamlit as st


st.set_page_config(page_title="Dashboard ETL - Monitoramento (Nível 2)", layout="wide")


# =============================================================================
# Helpers de banco
# =============================================================================
def _secret_lookup_case_insensitive(container, key: str, default: str = "") -> str:
    try:
        if container is None:
            return default
        if isinstance(container, dict):
            if key in container and container[key] not in (None, ""):
                return str(container[key])
            lowered = {str(k).lower(): v for k, v in container.items()}
            v = lowered.get(key.lower(), default)
            return default if v in (None, "") else str(v)
        if key in container:
            v = container.get(key, default)
            return default if v in (None, "") else str(v)
    except Exception:
        pass
    return default


def _get_env_or_secret(key: str, default: str = "") -> str:
    v = os.getenv(key, "")
    if v not in (None, ""):
        return str(v)

    try:
        v = st.secrets.get(key, default)
        if v not in (None, ""):
            return str(v)
    except Exception:
        pass

    secret_sections = ["mysql", "database", "db", "connections", "wms", "default"]
    for section in secret_sections:
        try:
            block = st.secrets.get(section)
            v = _secret_lookup_case_insensitive(block, key, "")
            if v:
                return v
        except Exception:
            continue

    aliases = {
        "WMS_DB_HOST": ["host", "hostname", "server"],
        "WMS_DB_NAME": ["database", "db", "dbname", "schema"],
        "WMS_DB_USER": ["user", "username", "uid"],
        "WMS_DB_PASS": ["password", "pass", "pwd"],
        "WMS_DB_PORT": ["port"],
        "ETL_ADMIN_PASS": ["admin_pass", "etl_admin_pass"],
    }

    for alias in aliases.get(key, []):
        v = os.getenv(alias, "")
        if v:
            return str(v)
        try:
            v = st.secrets.get(alias, "")
            if v:
                return str(v)
        except Exception:
            pass
        for section in secret_sections:
            try:
                block = st.secrets.get(section)
                v = _secret_lookup_case_insensitive(block, alias, "")
                if v:
                    return v
            except Exception:
                continue

    return default


def get_db_config() -> dict:
    return {
        "host": _get_env_or_secret("WMS_DB_HOST", "localhost"),
        "database": _get_env_or_secret("WMS_DB_NAME", "wms_apanha"),
        "user": _get_env_or_secret("WMS_DB_USER", "wms_user"),
        "password": _get_env_or_secret("WMS_DB_PASS", ""),
        "port": int(_get_env_or_secret("WMS_DB_PORT", "3306") or "3306"),
    }


def db_ready():
    cfg = get_db_config()
    if not cfg["password"]:
        return (
            False,
            "WMS_DB_PASS não foi encontrado nem no ambiente nem no streamlit secrets. Configure a senha do banco para o painel conectar.",
            cfg,
        )
    return True, "", cfg


def get_conn():
    ok, msg, cfg = db_ready()
    if not ok:
        raise RuntimeError(msg)

    return mysql.connector.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        autocommit=True,
    )


@st.cache_data(ttl=15)
def read_sql(query: str, params=None) -> pd.DataFrame:
    cnx = get_conn()
    try:
        return pd.read_sql(query, cnx, params=params)
    finally:
        cnx.close()


def exec_sql(query: str, params=None) -> int:
    cnx = get_conn()
    try:
        cur = cnx.cursor()
        cur.execute(query, params or ())
        affected = cur.rowcount
        cur.close()
        cnx.commit()
        return affected
    finally:
        cnx.close()


def safe_scalar(query: str, params=None, default=""):
    try:
        df = read_sql(query, params=params)
        if df.empty:
            return default
        v = df.iloc[0, 0]
        if pd.isna(v):
            return default
        return v
    except Exception:
        return default


def table_exists(tbl: str) -> bool:
    df = read_sql(
        """
        SELECT COUNT(*) AS c
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name = %s
        """,
        (tbl,),
    )
    return int(df.iloc[0]["c"]) > 0


def column_exists(tbl: str, col: str) -> bool:
    df = read_sql(
        """
        SELECT COUNT(*) AS c
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        """,
        (tbl, col),
    )
    return int(df.iloc[0]["c"]) > 0


def show_db_help(cfg: dict):
    st.error(
        "Conexão com o banco não configurada. Ajuste a senha do banco no ambiente ou no `.streamlit/secrets.toml`."
    )
    st.code(
        "\n".join(
            [
                'export WMS_DB_HOST="localhost"',
                f'export WMS_DB_NAME="{cfg["database"]}"',
                f'export WMS_DB_USER="{cfg["user"]}"',
                'export WMS_DB_PASS="SUA_SENHA_AQUI"',
                f'export WMS_DB_PORT="{cfg["port"]}"',
            ]
        ),
        language="bash",
    )
    st.markdown("Ou no arquivo `.streamlit/secrets.toml`:")
    st.code(
        '\n'.join(
            [
                'WMS_DB_HOST = "localhost"',
                'WMS_DB_NAME = "wms_apanha"',
                'WMS_DB_USER = "wms_user"',
                'WMS_DB_PASS = "SUA_SENHA_AQUI"',
                'WMS_DB_PORT = "3306"',
            ]
        ),
        language="toml",
    )


# =============================================================================
# Helpers estatísticos
# =============================================================================
def clamp_lower_zero(value):
    try:
        return max(0.0, float(value))
    except Exception:
        return 0.0


def calcular_limites_controle(df: pd.DataFrame, coluna: str) -> pd.DataFrame:
    out = df.copy()
    if out.empty or coluna not in out.columns:
        return out

    serie = pd.to_numeric(out[coluna], errors="coerce").fillna(0.0)
    media = float(serie.mean()) if len(serie) else 0.0
    desvio = float(serie.std(ddof=0)) if len(serie) > 1 else 0.0
    lsc = media + 3 * desvio
    lic = clamp_lower_zero(media - 3 * desvio)

    out[f"{coluna}_media"] = media
    out[f"{coluna}_lsc"] = lsc
    out[f"{coluna}_lic"] = lic
    out[f"{coluna}_fora_controle"] = (serie > lsc) | (serie < lic)
    out[f"{coluna}_mm7"] = serie.rolling(7, min_periods=1).mean()
    return out


def detectar_tendencia(df: pd.DataFrame, coluna: str, janela: int = 7) -> pd.Series:
    if df.empty or coluna not in df.columns:
        return pd.Series([], dtype=bool)

    valores = pd.to_numeric(df[coluna], errors="coerce").fillna(0.0).tolist()
    flags = [False] * len(valores)

    for i in range(janela - 1, len(valores)):
        trecho = valores[i - janela + 1 : i + 1]
        crescente = all(trecho[j] < trecho[j + 1] for j in range(len(trecho) - 1))
        decrescente = all(trecho[j] > trecho[j + 1] for j in range(len(trecho) - 1))
        if crescente or decrescente:
            flags[i] = True

    return pd.Series(flags, index=df.index)


def semaforo_controle(qtd_fora: int, qtd_tendencia: int) -> str:
    if qtd_fora > 0:
        return "🔴 Fora de Controle"
    if qtd_tendencia > 0:
        return "🟡 Em Alerta"
    return "🟢 Estável"


def score_estabilidade(qtd_total_flags: int) -> int:
    score = 100 - (qtd_total_flags * 10)
    return max(0, min(100, score))


# =============================================================================
# Cabeçalho
# =============================================================================
st.title("📦 Dashboard ETL - Monitoramento (Nível 2 + Controle Estatístico)")

with st.expander("ℹ️ Observações", expanded=False):
    st.markdown(
        """
- **Travados**: jobs em `PROCESSING` com `started_at` acima do limite.
- **Nível 2**: mostra fila, travas, erros, saúde das tabelas e ações operacionais.
- **Controle estatístico**: acompanha duração, volume e taxa de erro por dia.
        """
    )

# =============================================================================
# Filtros
# =============================================================================
c1, c2, c3, c4 = st.columns([1.1, 1.1, 1.0, 1.0])
with c1:
    dt_ini = st.date_input("Data inicial", value=date.today() - timedelta(days=30))
with c2:
    dt_fim = st.date_input("Data final", value=date.today())
with c3:
    stuck_minutes = st.number_input("Travado se > (min)", min_value=5, max_value=720, value=30, step=5)
with c4:
    if st.button("🔄 Atualizar"):
        st.cache_data.clear()

dt_ini_str = dt_ini.strftime("%Y-%m-%d")
dt_fim_str = (dt_fim + timedelta(days=1)).strftime("%Y-%m-%d")

# =============================================================================
# Validação conexão
# =============================================================================
ok_db, msg_db, cfg_db = db_ready()
if not ok_db:
    show_db_help(cfg_db)
    st.stop()

# =============================================================================
# Métricas principais
# =============================================================================
df_status = read_sql(
    """
    SELECT status, COUNT(*) AS qtd
    FROM etl_requests
    WHERE requested_at >= %s AND requested_at < %s
    GROUP BY status
    """,
    (dt_ini_str, dt_fim_str),
)

status_map = {str(r["status"]): int(r["qtd"]) for _, r in df_status.iterrows()}

df_stuck = read_sql(
    f"""
    SELECT
        id, tipo, cd, filename, status,
        started_at,
        TIMESTAMPDIFF(MINUTE, started_at, NOW()) AS mins_running,
        attempts,
        LEFT(last_error, 200) AS last_error_200
    FROM etl_requests
    WHERE status='PROCESSING'
      AND started_at IS NOT NULL
      AND started_at < (NOW() - INTERVAL {int(stuck_minutes)} MINUTE)
    ORDER BY started_at ASC
    """
)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("⏳ PENDENTE", status_map.get("PENDING", 0))
m2.metric("⚙️ PROCESSING", status_map.get("PROCESSING", 0))
m3.metric("✅ DONE", status_map.get("DONE", 0))
m4.metric("❌ ERROR", status_map.get("ERROR", 0))
m5.metric("🧊 TRAVADOS", int(len(df_stuck)))

# =============================================================================
# Admin
# =============================================================================
st.sidebar.markdown("### 🛠️ Operações")
admin_on = st.sidebar.checkbox("Ativar Modo Admin", value=False)
admin_ok = False

if admin_on:
    admin_pwd = st.sidebar.text_input("Senha admin", type="password")
    expected = _get_env_or_secret("ETL_ADMIN_PASS", "")
    if expected:
        admin_ok = admin_pwd == expected
        if admin_pwd and not admin_ok:
            st.sidebar.error("Senha admin inválida.")
    else:
        admin_ok = True

st.subheader("🛠️ Ações operacionais")

a1, a2, a3, a4 = st.columns(4)

with a1:
    if st.button("🧊 Liberar travados", disabled=not admin_ok):
        affected = exec_sql(
            f"""
            UPDATE etl_requests
            SET status='PENDING',
                locked_at=NULL,
                locked_by=NULL,
                started_at=NULL,
                finished_at=NULL,
                message='RESET by Dashboard'
            WHERE status='PROCESSING'
              AND started_at IS NOT NULL
              AND started_at < (NOW() - INTERVAL {int(stuck_minutes)} MINUTE)
            """
        )
        st.success(f"Travados liberados: {affected}")
        st.cache_data.clear()

with a2:
    if st.button("🔁 Reenfileirar ERRO", disabled=not admin_ok):
        affected = exec_sql(
            """
            UPDATE etl_requests
            SET status='PENDING',
                locked_at=NULL,
                locked_by=NULL,
                started_at=NULL,
                finished_at=NULL,
                message='RETRY by Dashboard'
            WHERE status='ERROR'
              AND requested_at >= %s AND requested_at < %s
            """,
            (dt_ini_str, dt_fim_str),
        )
        st.success(f"Reenfileirados: {affected}")
        st.cache_data.clear()

with a3:
    if st.button("⛔ Cancelar PROCESSING", disabled=not admin_ok):
        affected = exec_sql(
            """
            UPDATE etl_requests
            SET status='CANCELLED',
                locked_at=NULL,
                locked_by=NULL,
                finished_at=NOW(),
                message='CANCELLED by Dashboard'
            WHERE status='PROCESSING'
              AND started_at IS NOT NULL
              AND started_at < (NOW() - INTERVAL 5 MINUTE)
            """
        )
        st.success(f"Cancelados: {affected}")
        st.cache_data.clear()

with a4:
    if st.button("🧹 Limpar PENDENTES duplicados", disabled=not admin_ok):
        affected = exec_sql(
            """
            DELETE t1
            FROM etl_requests t1
            JOIN etl_requests t2
              ON t1.tipo = t2.tipo
             AND t1.filename = t2.filename
             AND t1.id > t2.id
            WHERE t1.status='PENDING'
              AND t2.status='PENDING'
            """
        )
        st.success(f"Duplicados removidos: {affected}")
        st.cache_data.clear()

# =============================================================================
# 1) Fila
# =============================================================================
st.divider()
st.subheader("1) Fila de Jobs (etl_requests)")

tabs1 = st.tabs(["📌 Pendentes", "⚙️ Em processamento", "🧊 Travados", "❌ Erros", "✅ Concluídos"])

with tabs1[0]:
    df = read_sql(
        """
        SELECT id, tipo, cd, filename, status, attempts, requested_by, requested_at
        FROM etl_requests
        WHERE status='PENDING'
          AND requested_at >= %s AND requested_at < %s
        ORDER BY id DESC
        LIMIT 500
        """,
        (dt_ini_str, dt_fim_str),
    )
    st.dataframe(df, use_container_width=True)

with tabs1[1]:
    df = read_sql(
        """
        SELECT id, tipo, cd, filename, status, attempts, started_at, locked_by
        FROM etl_requests
        WHERE status='PROCESSING'
        ORDER BY started_at ASC
        LIMIT 500
        """
    )
    if df.empty:
        st.info("Nenhum job em PROCESSING.")
    else:
        st.dataframe(df, use_container_width=True)

with tabs1[2]:
    if df_stuck.empty:
        st.success("Sem travados no período atual.")
    else:
        st.warning(f"Travados detectados: {len(df_stuck)}")
        st.dataframe(df_stuck, use_container_width=True)

with tabs1[3]:
    df = read_sql(
        """
        SELECT id, tipo, cd, filename, status, attempts, started_at, finished_at,
               LEFT(last_error, 800) AS last_error_800
        FROM etl_requests
        WHERE status='ERROR'
          AND requested_at >= %s AND requested_at < %s
        ORDER BY id DESC
        LIMIT 200
        """,
        (dt_ini_str, dt_fim_str),
    )
    st.dataframe(df, use_container_width=True)

with tabs1[4]:
    df = read_sql(
        """
        SELECT id, tipo, cd, filename, status, attempts, started_at, finished_at, message
        FROM etl_requests
        WHERE status='DONE'
          AND requested_at >= %s AND requested_at < %s
        ORDER BY id DESC
        LIMIT 300
        """,
        (dt_ini_str, dt_fim_str),
    )
    st.dataframe(df, use_container_width=True)

# =============================================================================
# 2) Importações
# =============================================================================
st.divider()
st.subheader("2) Importações (import_batches)")

if table_exists("import_batches"):
    tabs2 = st.tabs(["📊 Volume por dia", "🟡 Rodando", "❌ Com erro", "🧾 Últimos"])

    with tabs2[0]:
        df = read_sql(
            """
            SELECT CAST(imported_at AS DATE) AS dia, dataset, emp, SUM(rows_loaded) AS total_rows
            FROM import_batches
            WHERE imported_at >= %s AND imported_at < %s
            GROUP BY CAST(imported_at AS DATE), dataset, emp
            ORDER BY dia DESC
            """,
            (dt_ini_str, dt_fim_str),
        )
        st.dataframe(df, use_container_width=True)
        if not df.empty:
            pivot = df.pivot_table(index="dia", values="total_rows", aggfunc="sum").sort_index()
            st.line_chart(pivot)

    with tabs2[1]:
        df = read_sql(
            """
            SELECT id, dataset, emp, periodo_ini, periodo_fim, source_file, sha1, status, rows_loaded, imported_at
            FROM import_batches
            WHERE status='RUN'
            ORDER BY imported_at DESC
            LIMIT 200
            """
        )
        st.dataframe(df, use_container_width=True)

    with tabs2[2]:
        df = read_sql(
            """
            SELECT id, dataset, emp, periodo_ini, periodo_fim, source_file, status, rows_loaded, imported_at,
                   LEFT(error, 800) AS error_800
            FROM import_batches
            WHERE (status IN ('ERROR','FAIL','FAILED') OR error IS NOT NULL)
              AND imported_at >= %s AND imported_at < %s
            ORDER BY id DESC
            LIMIT 200
            """,
            (dt_ini_str, dt_fim_str),
        )
        st.dataframe(df, use_container_width=True)

    with tabs2[3]:
        df = read_sql(
            """
            SELECT id, dataset, emp, periodo_ini, periodo_fim, source_file, sha1, status, rows_loaded, imported_at
            FROM import_batches
            WHERE imported_at >= %s AND imported_at < %s
            ORDER BY id DESC
            LIMIT 300
            """,
            (dt_ini_str, dt_fim_str),
        )
        st.dataframe(df, use_container_width=True)
else:
    st.info("Tabela `import_batches` não encontrada.")

# =============================================================================
# 3) Saúde das tabelas
# =============================================================================
st.divider()
st.subheader("3) Saúde das tabelas")

TABLES_TO_CHECK = [
    "qv99_ocupacao",
    "qv00_layout_visitas",
    "qv04_produtividade",
    "qv10_cadastro_produto",
    "qv10_cadastro_produto_emp",
    "app_plan06",
    "raw_plan_06",
    "app_mov_vertical_diaria",
    "app_consulta_norma",
    "app_consulta_norma_end_qtd",
    "app_norma_end_qtd",
]

rows = []
for t in TABLES_TO_CHECK:
    if not table_exists(t):
        rows.append(
            {
                "tabela": t,
                "existe": "NÃO",
                "qtd": None,
                "ult_imported_at": None,
                "ult_batch_id": None,
                "status": "INEXISTENTE",
            }
        )
        continue

    qtd = safe_scalar(f"SELECT COUNT(*) FROM {t}", default=0)
    has_imported_at = column_exists(t, "imported_at")
    has_batch_id = column_exists(t, "batch_id")
    ult_dt = safe_scalar(f"SELECT MAX(imported_at) FROM {t}", default=None) if has_imported_at else None
    ult_batch = safe_scalar(f"SELECT MAX(batch_id) FROM {t}", default=None) if has_batch_id else None

    try:
        qtd_int = int(qtd)
    except Exception:
        qtd_int = 0

    rows.append(
        {
            "tabela": t,
            "existe": "SIM",
            "qtd": qtd_int,
            "ult_imported_at": ult_dt,
            "ult_batch_id": ult_batch,
            "status": "POPULADA" if qtd_int > 0 else "VAZIA",
        }
    )

df_health = pd.DataFrame(rows)
st.dataframe(df_health, use_container_width=True)

hc1, hc2 = st.columns(2)
with hc1:
    st.markdown("**Populadas**")
    st.dataframe(df_health[df_health["status"] == "POPULADA"], use_container_width=True)

with hc2:
    st.markdown("**Vazias / Inexistentes**")
    st.dataframe(df_health[df_health["status"].isin(["VAZIA", "INEXISTENTE"])], use_container_width=True)

# =============================================================================
# 4) Controle estatístico ETL
# =============================================================================
st.divider()
st.subheader("4) Gráfico de Controle ETL")

df_ctrl = read_sql(
    """
    SELECT
        DATE(requested_at) AS dia,
        COUNT(*) AS qtd_execucoes,
        SUM(CASE WHEN status = 'DONE' THEN 1 ELSE 0 END) AS qtd_done,
        SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) AS qtd_error,
        AVG(
            CASE
                WHEN started_at IS NOT NULL AND finished_at IS NOT NULL
                THEN TIMESTAMPDIFF(SECOND, started_at, finished_at)
                ELSE NULL
            END
        ) AS duracao_media_seg
    FROM etl_requests
    WHERE requested_at >= %s
      AND requested_at < %s
    GROUP BY DATE(requested_at)
    ORDER BY dia
    """,
    (dt_ini_str, dt_fim_str),
)

if df_ctrl.empty:
    st.info("Sem dados suficientes para o gráfico de controle no período.")
else:
    df_ctrl["dia"] = pd.to_datetime(df_ctrl["dia"])
    df_ctrl["qtd_execucoes"] = pd.to_numeric(df_ctrl["qtd_execucoes"], errors="coerce").fillna(0)
    df_ctrl["qtd_done"] = pd.to_numeric(df_ctrl["qtd_done"], errors="coerce").fillna(0)
    df_ctrl["qtd_error"] = pd.to_numeric(df_ctrl["qtd_error"], errors="coerce").fillna(0)
    df_ctrl["duracao_media_seg"] = pd.to_numeric(df_ctrl["duracao_media_seg"], errors="coerce").fillna(0.0)

    df_ctrl["taxa_erro_pct"] = (
        (df_ctrl["qtd_error"] / df_ctrl["qtd_execucoes"].replace(0, pd.NA)) * 100
    ).fillna(0.0)

    df_ctrl = calcular_limites_controle(df_ctrl, "duracao_media_seg")
    df_ctrl = calcular_limites_controle(df_ctrl, "qtd_execucoes")
    df_ctrl = calcular_limites_controle(df_ctrl, "taxa_erro_pct")

    df_ctrl["tend_duracao"] = detectar_tendencia(df_ctrl, "duracao_media_seg")
    df_ctrl["tend_volume"] = detectar_tendencia(df_ctrl, "qtd_execucoes")
    df_ctrl["tend_erro"] = detectar_tendencia(df_ctrl, "taxa_erro_pct")

    df_ctrl["fora_algum"] = (
        df_ctrl["duracao_media_seg_fora_controle"]
        | df_ctrl["qtd_execucoes_fora_controle"]
        | df_ctrl["taxa_erro_pct_fora_controle"]
    )
    df_ctrl["tend_alguma"] = df_ctrl["tend_duracao"] | df_ctrl["tend_volume"] | df_ctrl["tend_erro"]

    qtd_fora = int(df_ctrl["fora_algum"].sum())
    qtd_tend = int(df_ctrl["tend_alguma"].sum())
    semaforo = semaforo_controle(qtd_fora, qtd_tend)
    score = score_estabilidade(qtd_fora + qtd_tend)

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Semáforo", semaforo)
    s2.metric("Score de estabilidade", f"{score}%")
    s3.metric("Pontos fora de controle", qtd_fora)
    s4.metric("Tendências detectadas", qtd_tend)

    st.markdown("### Duração média por dia (segundos)")
    chart_dur = df_ctrl.set_index("dia")[
        ["duracao_media_seg", "duracao_media_seg_media", "duracao_media_seg_lsc", "duracao_media_seg_lic", "duracao_media_seg_mm7"]
    ]
    st.line_chart(chart_dur)

    st.markdown("### Volume de execuções por dia")
    chart_vol = df_ctrl.set_index("dia")[
        ["qtd_execucoes", "qtd_execucoes_media", "qtd_execucoes_lsc", "qtd_execucoes_lic", "qtd_execucoes_mm7"]
    ]
    st.line_chart(chart_vol)

    st.markdown("### Taxa de erro por dia (%)")
    chart_err = df_ctrl.set_index("dia")[
        ["taxa_erro_pct", "taxa_erro_pct_media", "taxa_erro_pct_lsc", "taxa_erro_pct_lic", "taxa_erro_pct_mm7"]
    ]
    st.line_chart(chart_err)

    def classificar_linha(row):
        if row["fora_algum"]:
            return "🔴 Fora de Controle"
        if row["tend_alguma"]:
            return "🟡 Alerta"
        return "🟢 Estável"

    df_ctrl["status_controle"] = df_ctrl.apply(classificar_linha, axis=1)

    st.markdown("### Consolidado diário")
    st.dataframe(
        df_ctrl[
            [
                "dia",
                "qtd_execucoes",
                "qtd_done",
                "qtd_error",
                "duracao_media_seg",
                "taxa_erro_pct",
                "status_controle",
            ]
        ],
        use_container_width=True,
    )

    # Diagnóstico automático
    msg_diag = []
    ult = df_ctrl.iloc[-1]

    if ult["duracao_media_seg_fora_controle"]:
        msg_diag.append("a duração média do ETL ficou fora do limite de controle")
    elif ult["tend_duracao"]:
        msg_diag.append("a duração média do ETL mostrou tendência anormal")

    if ult["qtd_execucoes_fora_controle"]:
        msg_diag.append("o volume de execuções ficou fora do padrão histórico")
    elif ult["tend_volume"]:
        msg_diag.append("o volume de execuções mostrou tendência")

    if ult["taxa_erro_pct_fora_controle"]:
        msg_diag.append("a taxa de erro ficou fora do limite de controle")
    elif ult["tend_erro"]:
        msg_diag.append("a taxa de erro mostrou tendência de deterioração")

    st.markdown("### Diagnóstico automático")
    if msg_diag:
        st.warning("No último dia analisado, " + "; ".join(msg_diag) + ".")
    else:
        st.success("No último dia analisado, o processo permaneceu estável dentro dos limites calculados.")
