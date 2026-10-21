import asyncio
import os
import tempfile
import time

import edge_tts
import fal_client
import requests
import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(page_title="Mi Publicador", page_icon="📣")

# ---------- Contraseña de acceso ----------
password = st.secrets.get("APP_PASSWORD")
if password and not st.session_state.get("autorizado"):
    st.title("📣 Mi Publicador")
    ingresada = st.text_input("Contraseña", type="password")
    if ingresada == password:
        st.session_state["autorizado"] = True
        st.rerun()
    elif ingresada:
        st.error("Contraseña incorrecta")
    st.stop()

# ---------- Claves (se guardan en Secrets de Streamlit, nunca en el código) ----------
os.environ["FAL_KEY"] = st.secrets["FAL_KEY"]
GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
TG_TOKEN = st.secrets["TELEGRAM_BOT_TOKEN"]
TG_CHANNEL = st.secrets["TELEGRAM_CHANNEL"]  # por ejemplo @mi_canal

VOCES = {
    "Dalia (mujer, México)": "es-MX-DaliaNeural",
    "Jorge (hombre, México)": "es-MX-JorgeNeural",
    "Elvira (mujer, España)": "es-ES-ElviraNeural",
    "Alvaro (hombre, España)": "es-ES-AlvaroNeural",
}


# ---------- Funciones ----------
def generar_texto(idea: str, tono: str) -> str:
    client = genai.Client(api_key=GEMINI_KEY, http_options=types.HttpOptions(timeout=20000))
    prompt = (
        f"Escribe una publicación para redes sociales en español con tono {tono}. "
        f"Tema o idea: {idea}. Devuelve solo el texto de la publicación, "
        "sin explicaciones, y puedes incluir algunos emojis y hashtags."
    )
    modelos = ["gemini-flash-lite-latest", "gemini-flash-latest"]
    ultimo_error = None
    for modelo in modelos:
        for intento in range(2):
            try:
                resp = client.models.generate_content(model=modelo, contents=prompt)
                return resp.text.strip()
            except Exception as e:
                ultimo_error = e
                time.sleep(1)
    raise ultimo_error


def generar_imagen(prompt: str) -> str:
    resultado = fal_client.subscribe(
        "fal-ai/flux/schnell",
        arguments={"prompt": prompt, "image_size": "square_hd"},
    )
    return resultado["images"][0]["url"]


def generar_voz(texto: str, voz: str) -> bytes:
    async def _run():
        ruta = tempfile.mktemp(suffix=".mp3")
        await edge_tts.Communicate(texto, voz).save(ruta)
        return ruta

    ruta = asyncio.run(_run())
    with open(ruta, "rb") as f:
        return f.read()


def telegram(metodo: str, data=None, files=None) -> dict:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/{metodo}"
    r = requests.post(url, data=data, files=files, timeout=60)
    return r.json()


def publicar(texto: str, imagen_url, audio_bytes):
    resultados = []
    if imagen_url:
        if len(texto) <= 1000:
            resultados.append(
                telegram("sendPhoto", {"chat_id": TG_CHANNEL, "photo": imagen_url, "caption": texto})
            )
            texto = None
        else:
            resultados.append(telegram("sendPhoto", {"chat_id": TG_CHANNEL, "photo": imagen_url}))
    if texto:
        resultados.append(telegram("sendMessage", {"chat_id": TG_CHANNEL, "text": texto}))
    if audio_bytes:
        resultados.append(
            telegram(
                "sendAudio",
                {"chat_id": TG_CHANNEL, "title": "Audio"},
                {"audio": ("audio.mp3", audio_bytes, "audio/mpeg")},
            )
        )
    return resultados


# ---------- Interfaz ----------
st.title("📣 Mi Publicador")

st.header("1. Texto")
idea = st.text_area("¿De qué quieres publicar?", placeholder="Ej: lanzamiento de mi nueva tienda de café")
tono = st.selectbox("Tono", ["amigable", "profesional", "divertido", "inspirador", "urgente"])
if st.button("Generar texto", disabled=not idea):
    with st.spinner("Escribiendo..."):
        try:
            st.session_state["texto"] = generar_texto(idea, tono)
        except Exception as e:
            st.error(f"Error generando texto: {e}")

texto = st.text_area("Texto final (puedes editarlo)", key="texto", height=180)

st.header("2. Imagen")
prompt_img = st.text_input(
    "Describe la imagen (funciona mejor en inglés)",
    placeholder="a cozy coffee shop at sunrise, warm light, photorealistic",
)
if st.button("Generar imagen", disabled=not prompt_img):
    with st.spinner("Generando imagen..."):
        try:
            st.session_state["imagen"] = generar_imagen(prompt_img)
        except Exception as e:
            st.error(f"Error generando imagen: {e}")
if st.session_state.get("imagen"):
    st.image(st.session_state["imagen"])

st.header("3. Voz")
voz = st.selectbox("Voz", list(VOCES.keys()))
if st.button("Generar audio", disabled=not texto):
    with st.spinner("Generando audio..."):
        try:
            st.session_state["audio"] = generar_voz(texto, VOCES[voz])
        except Exception as e:
            st.error(f"Error generando audio: {e}")
if st.session_state.get("audio"):
    st.audio(st.session_state["audio"], format="audio/mp3")

st.header("4. Publicar en Telegram")
col1, col2 = st.columns(2)
con_imagen = col1.checkbox("Incluir imagen", value=True)
con_audio = col2.checkbox("Incluir audio", value=False)

if st.button("🚀 Publicar", type="primary", disabled=not texto):
    imagen = st.session_state.get("imagen") if con_imagen else None
    audio = st.session_state.get("audio") if con_audio else None
    with st.spinner("Publicando..."):
        respuestas = publicar(texto, imagen, audio)
    if all(r.get("ok") for r in respuestas):
        st.success("¡Publicado en tu canal!")
    else:
        st.error(f"Telegram respondió con un error: {respuestas}")
