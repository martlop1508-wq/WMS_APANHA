# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import streamlit as st

from auth import require_login, sidebar_nav, topbar_user, logout_button
from app_config_db import get_config, set_config

st.set_page_config(page_title="Chamada Parâmetros", layout="wide")

require_login()
sidebar_nav()

st.title("Chamada Parâmetros")
topbar_user()
logout_button()

# Status (segredos ficam no .env / env do service)
openai_key_ok = "OK" if (os.getenv("OPENAI_API_KEY") or "").strip() else "AUSENTE"
agent_token_ok = "OK" if (os.getenv("AGENT_API_TOKEN") or "").strip() else "AUSENTE"

agent_url_ok = "OK" if (get_config("AGENT_API_URL", "").strip() != "") else "AUSENTE"
model_ok = "OK" if (get_config("OPENAI_MODEL", "").strip() != "") else "AUSENTE"

st.subheader("Status de Configuração")
c1, c2, c3 = st.columns(3)
with c1:
    st.caption("OPENAI_API_KEY")
    st.markdown(f"## {openai_key_ok}")
with c2:
    st.caption("URL_API_DO_AGENTE")
    st.markdown(f"## {agent_url_ok}")
with c3:
    st.caption("AGENT_API_TOKEN")
    st.markdown(f"## {agent_token_ok}")

st.info("SECDEVOPS: segredos ficam no .env. Nesta tela você salva apenas URL/MODEL no MySQL (app_config).")

st.subheader("Configuração (salva)")

left, right = st.columns(2)

with left:
    st.markdown("### OpenAI (modo direto)")
    st.text_input("OPENAI_API_KEY (mascarada)", value="*" * 40, disabled=True)
    openai_model = st.text_input("OPENAI_MODEL", value=get_config("OPENAI_MODEL", "gpt-5"))

with right:
    st.markdown("### Meu Agente (via API)")
    agent_url = st.text_input("URL_API_DO_AGENTE", value=get_config("AGENT_API_URL", ""))

if st.button("Salvar alterações"):
    set_config("OPENAI_MODEL", openai_model.strip())
    set_config("AGENT_API_URL", agent_url.strip())
    st.success("Salvo no MySQL (app_config).")
    st.rerun()

