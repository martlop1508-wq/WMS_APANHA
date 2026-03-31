# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import requests
import streamlit as st

from auth import require_login, sidebar_nav, topbar_user, logout_button
from app_config_db import get_config

st.set_page_config(page_title="Agente (ChatGPT)", layout="wide")

require_login()
sidebar_nav()

st.title("Agente (ChatGPT)")
topbar_user()
logout_button()

# CD (sempre string pro Agent API)
cd = st.selectbox("CD", options=["164", "364", "464"], index=0)

mode = st.selectbox("Modo", ["B) Meu agente (via API)", "A) OpenAI direto (API de respostas)"], index=0)

if "messages" not in st.session_state:
    st.session_state["messages"] = []

for m in st.session_state["messages"]:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

prompt = st.chat_input("Escreva sua mensagem...")

if prompt:
    st.session_state["messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Processando..."):
            try:
                if mode.startswith("A)"):
                    st.markdown("O modo OpenAI direto ainda não está habilitado nesta tela. Use 'Meu agente (via API)'!")
                    answer = "O modo OpenAI direto ainda não está habilitado nesta tela. Use 'Meu agente (via API)'!"
                else:
                    # ---------- PATCH DEFINITIVO (COLE NO LUGAR DO requests.post) ----------
                    agent_url = (get_config("AGENT_API_URL", "") or "").strip()
                    token = (os.getenv("AGENT_API_TOKEN") or "").strip()
                    timeout = int(os.getenv("AGENT_API_TIMEOUT_SEC", "60"))

                    if not agent_url:
                        raise RuntimeError("AGENT_API_URL não definido no MySQL (app_config).")

                    payload = {
                        "cd": str(cd),  # API exige string
                        "message": prompt,
                        "history": st.session_state["messages"],
                    }

                    headers = {"Content-Type": "application/json"}
                    if token:
                        headers["Authorization"] = f"Bearer {token}"

                    r = requests.post(
                        agent_url,
                        json=payload,
                        headers=headers,
                        timeout=timeout,
                    )

                    # Se for erro HTTP, joga o corpo no erro pra debugar
                    if not r.ok:
                        body = r.text[:2000]
                        raise requests.HTTPError(f"{r.status_code} {r.reason} for url: {agent_url}\n{body}", response=r)

                    # Tenta entender a resposta (json ou texto)
                    ctype = (r.headers.get("content-type") or "").lower()
                    if "application/json" in ctype:
                        data = r.json()
                        # Ajuste aqui conforme o seu agent_api responde:
                        # - se vier {"answer":"..."} usa answer
                        # - se vier qualquer outra estrutura, mostra json bonitinho
                        if isinstance(data, dict) and "answer" in data:
                            answer = str(data["answer"])
                        else:
                            answer = "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```"
                    else:
                        answer = r.text
                    # ---------- /PATCH DEFINITIVO ----------

                st.session_state["messages"].append({"role": "assistant", "content": answer})
                st.markdown(answer)

            except Exception as e:
                st.error(f"Falha ao consultar o agente: {e}")

