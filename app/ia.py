"""Generación de publicaciones con un modelo de lenguaje (vía OpenRouter).

OpenRouter expone una API compatible con la de OpenAI (`/chat/completions`)
delante de muchos proveedores, así que cambiar de modelo es cambiar una
variable de entorno (OPENROUTER_MODEL), no una línea de código.

Decisiones de diseño:

* La API key vive SOLO en el backend (.env). La app móvil nunca la ve: si la
  llevara dentro, cualquiera podría extraerla de la APK y gastar a nuestra costa.
* Sin dependencias nuevas: la llamada se hace con `urllib` de la librería
  estándar. Es una sola petición POST con JSON; no justifica otro paquete.
* Sin API key configurada se usa el generador SIMULADO de siempre. Así el
  proyecto sigue funcionando en cualquier equipo (y en las pruebas) aunque no
  haya credenciales, y queda registrado en `modelo_ia` cuál de los dos generó
  cada publicación.
* Este módulo NO importa la configuración ni la base de datos: recibe todo por
  parámetros. Eso permite probar sus funciones puras sin levantar nada.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELO_SIMULADO = "atlas-sim-1"

# Redes admitidas y la guía de formato que recibe el modelo para cada una.
REDES: dict[str, str] = {
    "instagram": (
        "Instagram: entre 80 y 150 palabras, un gancho en la primera línea, "
        "saltos de línea para respirar, como máximo 2 emojis y de 3 a 5 "
        "hashtags al final."
    ),
    "linkedin": (
        "LinkedIn: entre 120 y 200 palabras, tono de aprendizaje profesional, "
        "párrafos cortos, cierre con una pregunta a la comunidad y de 2 a 3 "
        "hashtags al final. Sin emojis."
    ),
    "x": (
        "X (Twitter): UN solo post de máximo 260 caracteres en total, "
        "incluidos 1 o 2 hashtags. Directo y sin relleno."
    ),
}

# Límite duro de X. El modelo recibe 260 como objetivo para dejar margen, pero
# si aun así se pasa, se recorta aquí: publicar un texto que la red rechaza
# sería peor que uno un poco más corto.
LIMITE_X = 280

TONOS: dict[str, str] = {
    "cercano": "cercano y conversacional, tuteando al lector",
    "profesional": "profesional, claro y con autoridad, sin sonar frío",
    "inspirador": "inspirador y motivador, orientado a la acción",
}


class ErrorIA(Exception):
    """Fallo al generar. El mensaje está pensado para mostrarse al usuario."""


@dataclass(frozen=True)
class Generado:
    texto: str
    modelo: str


def construir_mensajes(titulo: str, contenido: str, tono: str, red_social: str) -> list[dict]:
    """Arma el prompt. Función pura: se prueba sin red ni base de datos."""
    guia_red = REDES.get(red_social, REDES["instagram"])
    guia_tono = TONOS.get(tono, "natural y claro")
    sistema = (
        "Eres un redactor de redes sociales. Conviertes la idea de un creador en "
        "UNA publicación lista para copiar y pegar. Escribes en español neutro. "
        f"Tono: {guia_tono}. Formato: {guia_red} "
        "Responde SOLO con el texto de la publicación: sin comillas, sin "
        "títulos, sin explicar lo que hiciste y sin ofrecer alternativas. "
        "No inventes datos, cifras ni experiencias que la idea no mencione."
    )
    usuario = f"Título de la idea: {titulo.strip()}\n\nIdea:\n{contenido.strip()}"
    return [
        {"role": "system", "content": sistema},
        {"role": "user", "content": usuario},
    ]


def ajustar_a_red(texto: str, red_social: str) -> str:
    """Limpia la salida del modelo y garantiza el límite de caracteres de X."""
    limpio = texto.strip()
    # Algunos modelos envuelven la respuesta entre comillas pese a la instrucción.
    if len(limpio) >= 2 and limpio[0] == limpio[-1] and limpio[0] in "\"'“”":
        limpio = limpio[1:-1].strip()
    if red_social == "x" and len(limpio) > LIMITE_X:
        limpio = limpio[: LIMITE_X - 1].rstrip() + "…"
    return limpio


def extraer_texto(respuesta: dict) -> tuple[str, str | None]:
    """Saca el texto y el modelo real de una respuesta de /chat/completions."""
    try:
        texto = respuesta["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ErrorIA("El modelo devolvió una respuesta sin texto.") from exc
    if not isinstance(texto, str) or not texto.strip():
        raise ErrorIA("El modelo devolvió una publicación vacía.")
    return texto, respuesta.get("model")


def mensaje_de_error_http(codigo: int, cuerpo: str) -> str:
    """Traduce los errores de OpenRouter a algo que el usuario entienda."""
    detalle = ""
    try:
        detalle = json.loads(cuerpo).get("error", {}).get("message", "")
    except (ValueError, AttributeError):
        pass
    comunes = {
        401: "La API key de OpenRouter no es válida (revisa OPENROUTER_API_KEY).",
        402: "La cuenta de OpenRouter no tiene saldo para este modelo.",
        404: "El modelo configurado en OPENROUTER_MODEL no existe en OpenRouter.",
        429: "Se alcanzó el límite de peticiones del modelo. Reintenta en un minuto.",
    }
    base = comunes.get(codigo, f"OpenRouter respondió con el código {codigo}.")
    return f"{base} {detalle}".strip()


def generar_simulado(contenido: str, tono: str, red_social: str, latencia_ms: int) -> Generado:
    """Generador de respaldo cuando no hay API key: plantillas fijas."""
    time.sleep(latencia_ms / 1000)
    base = contenido.strip().rstrip(".")
    plantillas = {
        "cercano": f"Te cuento algo: {base}. ¿Te ha pasado? Cuéntame en los comentarios.",
        "profesional": f"Reflexión del día: {base}. Un principio simple con gran impacto.",
        "inspirador": f"{base}. Da el primer paso hoy: el momento perfecto no existe.",
    }
    cuerpo = plantillas.get(tono, f"{base}.")
    hashtags = {
        "instagram": "#ideas #contenido #atlas",
        "linkedin": "#productividad #crecimiento #atlas",
        "x": "#build #atlas",
    }.get(red_social, "#atlas")
    return Generado(ajustar_a_red(f"{cuerpo}\n\n{hashtags}", red_social), MODELO_SIMULADO)


def generar_publicacion(
    *,
    titulo: str,
    contenido: str,
    tono: str,
    red_social: str,
    api_key: str | None,
    modelo: str,
    timeout_s: int,
    latencia_simulada_ms: int,
) -> Generado:
    """Punto de entrada: IA real si hay API key, simulada si no."""
    if not api_key:
        return generar_simulado(contenido, tono, red_social, latencia_simulada_ms)

    cuerpo = json.dumps(
        {
            "model": modelo,
            "messages": construir_mensajes(titulo, contenido, tono, red_social),
            "temperature": 0.8,
            "max_tokens": 700,
        }
    ).encode("utf-8")
    peticion = urllib.request.Request(
        OPENROUTER_URL,
        data=cuerpo,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Cabeceras opcionales de OpenRouter para identificar la app.
            "X-Title": "Atlas",
        },
    )
    try:
        with urllib.request.urlopen(peticion, timeout=timeout_s) as resp:
            datos = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ErrorIA(mensaje_de_error_http(exc.code, exc.read().decode("utf-8", "replace"))) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ErrorIA(f"No se pudo contactar a OpenRouter: {getattr(exc, 'reason', exc)}") from exc

    texto, modelo_real = extraer_texto(datos)
    return Generado(ajustar_a_red(texto, red_social), (modelo_real or modelo)[:40])
