"""Dashboard analítico del usuario — operación costosa con caché-aside.

Generar este reporte agrega datos de varias tablas (ideas, publicaciones y sus
métricas). Es la operación más cara del backend y se repite mucho, así que es la
candidata natural para caché-aside:

  1er request (MISS): consulta la base, arma el reporte y lo guarda en Redis (TTL).
  siguientes (HIT):   lo sirve desde Redis, 0 consultas a la base, en microsegundos.
  al registrar métrica / publicar: la clave se invalida y el próximo request recalcula.
"""
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select

from ..cache import cache_get, cache_set, dashboard_key
from ..config import settings
from ..deps import Principal, get_current_user, get_db
from ..models import Etiqueta, Idea, MetricaPublicacion, Publicacion, idea_etiqueta

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


def _calcular_dashboard(db, uid: int) -> dict:
    # Simula el costo de un reporte analítico pesado; en producción sería el
    # tiempo real de agregar millones de filas. Esto es lo que el caché evita.
    time.sleep(settings.report_latency_ms / 1000)

    total_ideas = db.scalar(select(func.count(Idea.id)).where(Idea.usuario_id == uid))
    total_pubs = db.scalar(
        select(func.count(Publicacion.id))
        .join(Idea, Publicacion.idea_id == Idea.id)
        .where(Idea.usuario_id == uid)
    )
    likes, comentarios, compartidos, alcance = db.execute(
        select(
            func.coalesce(func.sum(MetricaPublicacion.likes), 0),
            func.coalesce(func.sum(MetricaPublicacion.comentarios), 0),
            func.coalesce(func.sum(MetricaPublicacion.compartidos), 0),
            func.coalesce(func.sum(MetricaPublicacion.alcance), 0),
        )
        .join(Publicacion, MetricaPublicacion.publicacion_id == Publicacion.id)
        .join(Idea, Publicacion.idea_id == Idea.id)
        .where(Idea.usuario_id == uid)
    ).one()
    top = db.execute(
        select(Etiqueta.nombre, func.count(idea_etiqueta.c.idea_id))
        .join(idea_etiqueta, idea_etiqueta.c.etiqueta_id == Etiqueta.id)
        .join(Idea, Idea.id == idea_etiqueta.c.idea_id)
        .where(Idea.usuario_id == uid)
        .group_by(Etiqueta.nombre)
        .order_by(func.count(idea_etiqueta.c.idea_id).desc())
        .limit(5)
    ).all()

    return {
        "usuario_id": uid,
        "total_ideas": total_ideas or 0,
        "total_publicaciones": total_pubs or 0,
        "engagement": {
            "likes": int(likes),
            "comentarios": int(comentarios),
            "compartidos": int(compartidos),
            "alcance": int(alcance),
        },
        "top_etiquetas": [{"nombre": n, "usos": c} for n, c in top],
        "generado_en": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/metricas")
def dashboard_metricas(
    response: Response,
    user: Principal = Depends(get_current_user),
    db=Depends(get_db),
):
    clave = dashboard_key(user.id)

    cacheado = cache_get(clave)
    if cacheado is not None:
        response.headers["X-Cache"] = "HIT"
        response.headers["X-Query-Count"] = "0"
        return cacheado

    response.headers["X-Cache"] = "MISS"
    datos = _calcular_dashboard(db, user.id)
    cache_set(clave, datos, settings.cache_ttl_seconds)
    response.headers["X-Query-Count"] = str(db.info.get("query_count", 0))
    return datos
