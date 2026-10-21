import asyncio
import base64
import datetime as dt
import os
import re
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
TAVILY_KEY = st.secrets.get("TAVILY_API_KEY")  # opcional: búsqueda web gratis (1000/mes)
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


MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def hoy() -> dt.date:
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo("America/Mexico_City")).date()
    except Exception:
        return dt.date.today()


def fecha_larga() -> str:
    d = hoy()
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


JOLPICA = "https://api.jolpi.ca/ergast/f1"


def _f1_datos() -> str:
    """Datos oficiales de la temporada actual de F1 (API abierta Jolpica/Ergast)."""
    bloques = []

    r = requests.get(f"{JOLPICA}/current/driverstandings/", timeout=20)
    r.raise_for_status()
    lista = r.json()["MRData"]["StandingsTable"]["StandingsLists"][0]
    temporada, ronda = lista["season"], lista["round"]
    filas = []
    for d in lista["DriverStandings"][:10]:
        p = d["Driver"]
        equipo = d["Constructors"][-1]["name"] if d.get("Constructors") else ""
        filas.append(
            f'{d["position"]}. {p["givenName"]} {p["familyName"]} ({equipo}): '
            f'{d["points"]} pts, {d["wins"]} victorias'
        )
    bloques.append(
        f"CAMPEONATO DE PILOTOS {temporada} (después de la ronda {ronda}):\n" + "\n".join(filas)
    )

    r = requests.get(f"{JOLPICA}/current/constructorstandings/", timeout=20)
    r.raise_for_status()
    lista = r.json()["MRData"]["StandingsTable"]["StandingsLists"][0]
    filas = [
        f'{c["position"]}. {c["Constructor"]["name"]}: {c["points"]} pts, {c["wins"]} victorias'
        for c in lista["ConstructorStandings"][:6]
    ]
    bloques.append(f"CAMPEONATO DE CONSTRUCTORES {temporada}:\n" + "\n".join(filas))

    r = requests.get(f"{JOLPICA}/current/last/results/", timeout=20)
    r.raise_for_status()
    carrera = r.json()["MRData"]["RaceTable"]["Races"][0]
    filas = []
    for x in carrera["Results"][:10]:
        p = x["Driver"]
        tiempo = x.get("Time", {}).get("time") or x.get("status", "")
        vr = " (vuelta rápida)" if x.get("FastestLap", {}).get("rank") == "1" else ""
        filas.append(
            f'{x["position"]}. {p["givenName"]} {p["familyName"]} ({x["Constructor"]["name"]}), '
            f'salió {x["grid"]}, {tiempo}, {x["points"]} pts{vr}'
        )
    bloques.append(
        f'ÚLTIMA CARRERA: {carrera["raceName"]} {carrera["season"]} (ronda {carrera["round"]}), '
        f'circuito {carrera["Circuit"]["circuitName"]}, fecha {carrera["date"]}:\n' + "\n".join(filas)
    )
    return "\n\n".join(bloques)


def _buscar_tavily(consulta: str) -> list:
    resultados = []
    for tema in ("news", "general"):
        r = requests.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {TAVILY_KEY}"},
            json={"query": consulta, "topic": tema, "time_range": "month", "max_results": 6},
            timeout=30,
        )
        r.raise_for_status()
        resultados = r.json().get("results", [])
        if resultados:
            break
    return [
        f'- {i.get("title", "")} ({i.get("published_date", "sin fecha")}): {i.get("content", "")[:500]}'
        for i in resultados
    ]


def _buscar_ddgs(consulta: str) -> list:
    from ddgs import DDGS

    resultados = DDGS().news(consulta, max_results=6, timelimit="m")
    return [
        f'- {i.get("title", "")} ({i.get("date", "sin fecha")}): {i.get("body", "")[:500]}'
        for i in resultados
    ]


TIPOS = ["Deportes", "Espectáculos", "Comedia / entretenimiento", "General"]

BUSQUEDA = {
    "Deportes": "resultados clasificación campeonato estadísticas",
    "Espectáculos": "noticias estreno premios famosos",
    "Comedia / entretenimiento": "tendencias virales noticias del momento",
    "General": "últimas noticias",
}

REGLAS = {
    "Deportes": (
        "Usa EXCLUSIVAMENTE los datos del bloque DATOS para cifras, posiciones, resultados, "
        "estadísticas y fechas. No uses datos de memoria ni inventes nada. "
        "Si hay datos oficiales y noticias que se contradicen, prioriza los oficiales."
    ),
    "Espectáculos": (
        "Usa los datos del bloque DATOS para nombres, fechas, estrenos, premios y hechos. "
        "No inventes citas ni declaraciones de nadie. Si algo es rumor o no está confirmado, "
        "dilo con claridad (por ejemplo 'según reportes' o 'se dice') en vez de darlo por cierto."
    ),
    "Comedia / entretenimiento": (
        "Escribe con humor creativo y buen ritmo (chistes, ironía ligera, exageración). "
        "Puedes usar los datos del bloque DATOS como inspiración del tema, pero no inventes hechos "
        "reales ni citas atribuidas a personas reales. Nada de burlas basadas en estereotipos "
        "ni ataques a personas reales; el humor va hacia la situación, no hacia gente concreta."
    ),
    "General": (
        "Usa los datos del bloque DATOS para cifras, fechas y hechos. No uses datos de memoria "
        "ni inventes nada. Si un dato no aparece, omítelo."
    ),
}


def investigar(tema: str, tipo: str = "General"):
    """Devuelve (contexto, avisos). Reúne datos actuales de la web y, si es F1, datos oficiales."""
    avisos, bloques = [], []
    anio = hoy().year

    if tipo == "Deportes" and re.search(
        r"\b(f1|gp|formula 1|fórmula 1|formula uno|fórmula uno|gran premio)\b", tema.lower()
    ):
        try:
            bloques.append("=== DATOS OFICIALES DE F1 (temporada actual) ===\n" + _f1_datos())
        except Exception as e:
            avisos.append(f"No pude obtener los datos oficiales de F1: {str(e)[:120]}")

    consulta = f"{tema} {anio} {BUSQUEDA[tipo]}"
    noticias = []
    if TAVILY_KEY:
        try:
            noticias = _buscar_tavily(consulta)
        except Exception as e:
            avisos.append(f"Tavily falló: {str(e)[:120]}")
    if not noticias:
        try:
            noticias = _buscar_ddgs(consulta)
        except Exception as e:
            avisos.append(f"La búsqueda web gratuita falló: {str(e)[:120]}")
    if noticias:
        bloques.append("=== NOTICIAS RECIENTES DE LA WEB ===\n" + "\n".join(noticias))

    return "\n\n".join(bloques), avisos


LARGOS = {
    "Corta (hasta ~500 caracteres)": "unos 500 caracteres como máximo",
    "Media (~900 caracteres)": "unos 900 caracteres",
    "Larga (~1500 caracteres)": "unos 1500 caracteres, con más estadísticas y contexto",
}


def generar_texto(idea: str, tono: str, largo: str = "", contexto: str = "", tipo: str = "General") -> str:
    instruccion_largo = LARGOS.get(largo, "una extensión moderada")
    if contexto:
        datos = (
            f"{REGLAS[tipo]} Los datos son de la fecha actual ({hoy().year}); "
            "menciónalo cuando aporte. Si un dato no aparece, omítelo.\n\n"
            f"DATOS:\n{contexto}\n\n"
        )
    elif tipo == "Comedia / entretenimiento":
        datos = (
            f"{REGLAS[tipo]}\n\n"
        )
    else:
        datos = (
            "No tienes datos actualizados: no inventes cifras, resultados ni estadísticas; "
            "mantente en información general y segura.\n\n"
        )
    if tipo == "Comedia / entretenimiento":
        estructura = (
            "Estructura: una primera línea que enganche con humor, el desarrollo con ritmo, "
            "un remate gracioso al final y de 3 a 5 hashtags."
        )
    else:
        estructura = (
            "Estructura: un gancho llamativo en la primera línea, los datos más importantes en "
            "líneas cortas con emojis, un cierre que invite a comentar y de 3 a 5 hashtags."
        )
    prompt = (
        f"Hoy es {fecha_larga()}. Eres redactor experto de redes sociales. "
        f"Escribe una publicación en español con tono {tono} y extensión de {instruccion_largo}. "
        f"Tipo de contenido: {tipo}. Tema: {idea}.\n"
        f"{estructura} Devuelve solo el texto de la publicación, sin explicaciones.\n\n" + datos
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


def _imagen_gemini(prompt: str) -> bytes:
    client = genai.Client(api_key=GEMINI_KEY, http_options=types.HttpOptions(timeout=90000))
    ultimo_error = None
    for modelo in ["gemini-3.1-flash-image", "gemini-3.1-flash-image-preview"]:
        try:
            resp = client.models.generate_content(
                model=modelo,
                contents=prompt,
                config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
            )
            for parte in resp.candidates[0].content.parts:
                if getattr(parte, "inline_data", None) and parte.inline_data.data:
                    return parte.inline_data.data
            raise RuntimeError("Gemini no devolvió ninguna imagen")
        except Exception as e:
            ultimo_error = e
    raise ultimo_error


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
    intentos = [("Gemini", _imagen_gemini)]
    if CF_ACCOUNT and CF_TOKEN:
        intentos.append(("Cloudflare", _imagen_cloudflare))
    intentos.append(("Pollinations", _imagen_pollinations))
    if FAL_KEY:
        intentos.append(("fal.ai", _imagen_fal))
    errores = []
    for nombre, f in intentos:
        try:
            datos = f(prompt)
            st.session_state["proveedor"] = nombre
            st.session_state["errores_img"] = errores
            return datos
        except Exception as e:
            errores.append(f"{nombre}: {str(e)[:200]}")
    raise RuntimeError(" | ".join(errores))


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


def publicar(texto: str, imagen, audio_bytes, video_bytes=None):
    resultados = []
    texto = (texto or "").strip()
    corto = 0 < len(texto) <= 1000
    if video_bytes:
        resultados.append(
            telegram(
                "sendVideo",
                {"chat_id": TG_CHANNEL, "caption": texto if corto else ""},
                {"video": ("video.mp4", video_bytes, "video/mp4")},
            )
        )
        if corto:
            texto = ""
        imagen = None
    if imagen:
        datos = {"chat_id": TG_CHANNEL}
        if corto and texto:
            datos["caption"] = texto
            texto = ""
        resultados.append(
            telegram("sendPhoto", datos, {"photo": ("imagen.jpg", imagen, "image/jpeg")})
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
idea = st.text_area(
    "¿De qué quieres publicar?",
    placeholder="Ej: F1 GP Austin, resumen de la carrera y cómo queda el campeonato",
)
tipo = st.selectbox("Tipo de contenido", TIPOS)
c1, c2 = st.columns(2)
tono = c1.selectbox("Tono", ["amigable", "profesional", "divertido", "inspirador", "urgente"])
largo = c2.selectbox("Longitud", list(LARGOS.keys()), index=1)
investigar_web = st.checkbox("🔎 Buscar información actual en internet (recomendado)", value=True)

if st.button("Generar texto", disabled=not idea):
    with st.spinner("Buscando datos actuales y redactando..."):
        try:
            contexto, avisos = ("", [])
            if investigar_web:
                contexto, avisos = investigar(idea, tipo)
            for a in avisos:
                st.warning(a)
            if investigar_web and not contexto and tipo != "Comedia / entretenimiento":
                st.warning("No encontré datos actuales; el texto será general y sin cifras.")
            st.session_state["contexto"] = contexto
            st.session_state["texto"] = generar_texto(idea, tono, largo, contexto, tipo)
        except Exception as e:
            st.error(f"Error generando texto: {e}")

if st.session_state.get("contexto"):
    with st.expander("Datos encontrados (revísalos antes de publicar)"):
        st.text(st.session_state["contexto"])

texto = st.text_area("Texto final (puedes editarlo)", key="texto", height=220)

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
    st.caption(f"Generada con: {st.session_state.get('proveedor', '?')}")
    with st.expander("Ver el prompt que se usó"):
        st.write(st.session_state.get("prompt_final", ""))
        if st.session_state.get("errores_img"):
            st.write("Proveedores que fallaron antes:", st.session_state["errores_img"])

st.subheader("O usa una imagen o video que ya hiciste")
st.caption("Créalo gratis con tu cuenta de Gemini (gemini.google.com) o ChatGPT, descárgalo y súbelo aquí.")
img_sub = st.file_uploader("Subir imagen", type=["png", "jpg", "jpeg", "webp"], key="up_img")
if img_sub is not None:
    clave = f"{img_sub.name}-{img_sub.size}"
    if st.session_state.get("img_subida") != clave:
        st.session_state["img_subida"] = clave
        st.session_state["imagen"] = img_sub.getvalue()
        st.session_state["proveedor"] = "subida por ti"
        st.session_state["errores_img"] = []
        st.session_state["prompt_final"] = ""
        st.rerun()
vid_sub = st.file_uploader("Subir video (MP4, máximo 50 MB)", type=["mp4", "mov"], key="up_vid")
if vid_sub is not None:
    st.video(vid_sub.getvalue())

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
col1, col2, col3 = st.columns(3)
con_imagen = col1.checkbox("Incluir imagen", value=True)
con_audio = col2.checkbox("Incluir audio", value=False)
con_video = col3.checkbox("Incluir video subido", value=False)

if st.button("🚀 Publicar", type="primary", disabled=not texto):
    imagen = st.session_state.get("imagen") if con_imagen else None
    audio = st.session_state.get("audio") if con_audio else None
    with st.spinner("Publicando..."):
        video = vid_sub.getvalue() if (con_video and vid_sub is not None) else None
        respuestas = publicar(texto, imagen, audio, video)
    if all(r.get("ok") for r in respuestas):
        st.success("¡Publicado en tu canal!")
    else:
        st.error(f"Telegram respondió con un error: {respuestas}")
