import asyncio
import base64
import os
import random
import tempfile
import time
import urllib.parse

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
FAL_KEY = st.secrets.get("FAL_KEY")  # opcional (de pago)
if FAL_KEY:
    os.environ["FAL_KEY"] = FAL_KEY
CF_ACCOUNT = st.secrets.get("CLOUDFLARE_ACCOUNT_ID")  # opcional (gratis)
CF_TOKEN = st.secrets.get("CLOUDFLARE_API_TOKEN")
GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
GROQ_KEY = st.secrets.get("GROQ_API_KEY")  # opcional, muy rápido
TG_TOKEN = st.secrets["TELEGRAM_BOT_TOKEN"]
TG_CHANNEL = st.secrets["TELEGRAM_CHANNEL"]  # por ejemplo @mi_canal

VOCES = {
    "Dalia (mujer, México)": "es-MX-DaliaNeural",
    "Jorge (hombre, México)": "es-MX-JorgeNeural",
    "Elvira (mujer, España)": "es-ES-ElviraNeural",
    "Alvaro (hombre, España)": "es-ES-AlvaroNeural",
}


# ---------- Funciones ----------
def _texto_groq(prompt: str, modelo: str) -> str:
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}"},
        json={"model": modelo, "messages": [{"role": "user", "content": prompt}]},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _texto_gemini(prompt: str, modelo: str) -> str:
    client = genai.Client(api_key=GEMINI_KEY, http_options=types.HttpOptions(timeout=45000))
    resp = client.models.generate_content(model=modelo, contents=prompt)
    return resp.text.strip()


def _llm(prompt: str) -> str:
    intentos = []
    if GROQ_KEY:
        intentos += [(_texto_groq, "llama-3.3-70b-versatile"), (_texto_groq, "llama-3.1-8b-instant")]
    intentos += [(_texto_gemini, "gemini-3.5-flash-lite"), (_texto_gemini, "gemini-3.1-flash-lite")]
    ultimo_error = None
    for funcion, modelo in intentos:
        try:
            return funcion(prompt, modelo)
        except Exception as e:
            ultimo_error = e
    raise ultimo_error


def generar_texto(idea: str, tono: str) -> str:
    prompt = (
        f"Escribe una publicación para redes sociales en español con tono {tono}. "
        f"Tema o idea: {idea}. Devuelve solo el texto de la publicación, "
        "sin explicaciones, y puedes incluir algunos emojis y hashtags."
    )
    return _llm(prompt)


ESTILOS = {
    "Fotorrealista": "ultra realistic professional photograph, 35mm lens, shallow depth of field, natural lighting, sharp focus, high detail, 8k",
    "Cinematográfico": "cinematic still, dramatic lighting, golden hour, film grain, anamorphic lens, rich color grading, highly detailed",
    "Ilustración digital": "polished digital illustration, vibrant colors, clean lines, detailed, trending on artstation",
    "3D / Pixar": "3D render, soft studio lighting, smooth materials, vibrant colors, highly detailed, octane render",
    "Anime": "high quality anime artwork, detailed background, vivid colors, studio quality, sharp lineart",
    "Póster / Diseño": "modern graphic design poster, bold composition, clean layout, vibrant palette, high contrast",
}


def mejorar_prompt(descripcion: str, estilo: str) -> str:
    pedido = (
        "Convierte esta idea en un prompt detallado EN INGLÉS para un generador de imágenes. "
        "Describe sujeto, escena, composición, iluminación, colores y ambiente en una sola frase larga "
        "(máximo 60 palabras). No incluyas texto escrito dentro de la imagen. "
        f"Estilo: {estilo}. Idea: {descripcion}. Devuelve solo el prompt, sin comillas ni explicaciones."
    )
    try:
        base = _llm(pedido).strip().strip('"')
    except Exception:
        base = descripcion
    return f"{base}, {ESTILOS[estilo]}"


def _imagen_cloudflare(prompt: str) -> bytes:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}"
        "/ai/run/@cf/black-forest-labs/flux-1-schnell"
    )
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {CF_TOKEN}"},
        json={"prompt": prompt, "steps": 8},
        timeout=60,
    )
    r.raise_for_status()
    return base64.b64decode(r.json()["result"]["image"])


def _imagen_pollinations(prompt: str) -> bytes:
    q = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{q}?width=1024&height=1024&model=flux&nologo=true&enhance=false&seed={random.randint(1, 999999)}"
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    if "image" not in r.headers.get("content-type", ""):
        raise RuntimeError("Pollinations no devolvió una imagen")
    return r.content


def _imagen_fal(prompt: str) -> bytes:
    res = fal_client.subscribe(
        "fal-ai/flux/schnell", arguments={"prompt": prompt, "image_size": "square_hd"}
    )
    return requests.get(res["images"][0]["url"], timeout=60).content


def generar_imagen(prompt: str) -> bytes:
    intentos = []
    if CF_ACCOUNT and CF_TOKEN:
        intentos.append(_imagen_cloudflare)
    intentos.append(_imagen_pollinations)
    if FAL_KEY:
        intentos.append(_imagen_fal)
    ultimo_error = None
    for f in intentos:
        try:
            return f(prompt)
        except Exception as e:
            ultimo_error = e
    raise ultimo_error


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
                telegram(
                    "sendPhoto",
                    {"chat_id": TG_CHANNEL, "caption": texto},
                    {"photo": ("imagen.jpg", imagen_url, "image/jpeg")},
                )
            )
            texto = None
        else:
            resultados.append(
                telegram(
                    "sendPhoto",
                    {"chat_id": TG_CHANNEL},
                    {"photo": ("imagen.jpg", imagen_url, "image/jpeg")},
                )
            )
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
    "Describe la imagen (puedes escribirlo en español)",
    placeholder="una taza de café humeante sobre una mesa de madera junto a la ventana al amanecer",
)
estilo = st.selectbox("Estilo", list(ESTILOS.keys()))
mejorar = st.checkbox("Mejorar mi descripción automáticamente (recomendado)", value=True)
if st.button("Generar imagen", disabled=not prompt_img):
    with st.spinner("Generando imagen..."):
        try:
            final = mejorar_prompt(prompt_img, estilo) if mejorar else f"{prompt_img}, {ESTILOS[estilo]}"
            st.session_state["prompt_final"] = final
            st.session_state["imagen"] = generar_imagen(final)
        except Exception as e:
            st.error(f"Error generando imagen: {e}")
if st.session_state.get("imagen"):
    st.image(st.session_state["imagen"])
    with st.expander("Ver el prompt que se usó"):
        st.write(st.session_state.get("prompt_final", ""))

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
