"""Punto de entrada de la API de Atlas.

Incluye un middleware que mide el tiempo real de cada request y lo devuelve en
la cabecera `X-Process-Time-ms`, para comparar rendimiento antes vs. después.
"""
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse

from .database import Base, engine, ensure_schema
from .routers import auth, dashboard, ideas, publicaciones


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Crea el esquema aparte (si se configuró) y las tablas si no existen.
    ensure_schema()
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Atlas Backend API",
    version="1.0.0",
    description=(
        "Backend del proyecto integrador Atlas. Taller Semana 8: caché-aside, "
        "corrección de N+1, cola de trabajo asíncrona, lazy/eager loading y "
        "autenticación sin consultas redundantes."
    ),
    lifespan=lifespan,
)


@app.middleware("http")
async def medir_tiempo(request, call_next):
    inicio = perf_counter()
    respuesta = await call_next(request)
    respuesta.headers["X-Process-Time-ms"] = f"{(perf_counter() - inicio) * 1000:.1f}"
    return respuesta


app.include_router(auth.router)
app.include_router(ideas.router)
app.include_router(publicaciones.router)
app.include_router(dashboard.router)


@app.get("/health", tags=["Sistema"])
def health():
    return {"status": "ok", "servicio": "atlas-backend"}


@app.get("/", include_in_schema=False)
def raiz():
    # La raíz lleva a la página de demostración con botones.
    return RedirectResponse(url="/demo")


@app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
def demo():
    # Consola visual para el video: se lee el archivo en cada request, así los
    # cambios al HTML se ven sin reiniciar.
    return (Path(__file__).parent / "demo.html").read_text(encoding="utf-8")
