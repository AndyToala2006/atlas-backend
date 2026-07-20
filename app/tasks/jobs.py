"""Tarea asíncrona: transformar una idea en una publicación con IA.

Este es el caso real de Atlas que justifica una cola de trabajo: generar la
publicación es LENTO (llamada a un modelo de lenguaje). Si se hiciera dentro del
request, el usuario esperaría varios segundos con la app bloqueada. En su lugar
el endpoint encola el trabajo y responde al instante; el worker lo procesa.
"""
import time

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..cache import cache_invalidate, dashboard_key
from ..config import settings
from ..database import SessionLocal
from ..models import Idea, Job, Publicacion, Usuario
from .celery_app import celery_app


def _generar_texto(contenido: str, tono: str, red_social: str) -> str:
    """Simula la generación con IA (aquí iría la llamada al modelo de lenguaje)."""
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
    return f"{cuerpo}\n\n{hashtags}"


def procesar_publicacion(job_id: str) -> None:
    """Lógica de negocio del trabajo. La usan tanto el worker como el modo síncrono."""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.estado = "processing"
        db.commit()

        # EAGER LOADING JUSTIFICADO: en un solo viaje traemos la idea, su usuario
        # y el perfil de tono, porque los tres se usan sí o sí para generar.
        idea = db.execute(
            select(Idea)
            .options(selectinload(Idea.usuario).selectinload(Usuario.perfil_tono))
            .where(Idea.id == job.idea_id)
        ).scalar_one()

        # Latencia real de la IA (simulada) — esto es lo que sacamos del request.
        time.sleep(settings.ia_latency_ms / 1000)

        tono = idea.usuario.perfil_tono.nombre if idea.usuario.perfil_tono else "neutral"
        publicacion = Publicacion(
            idea_id=idea.id,
            red_social="instagram",
            contenido_generado=_generar_texto(idea.contenido, tono, "instagram"),
            tono=tono,
            estado="generada",
            modelo_ia="atlas-sim-1",
        )
        db.add(publicacion)
        idea.estado = "publicada"
        db.commit()

        job.resultado_publicacion_id = publicacion.id
        job.estado = "done"
        db.commit()

        # INVALIDACIÓN EXPLÍCITA del caché: el dashboard del usuario cambió.
        cache_invalidate(dashboard_key(idea.usuario_id))
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        job = db.get(Job, job_id)
        if job is not None:
            job.estado = "error"
            job.error = str(exc)[:255]
            db.commit()
    finally:
        db.close()


@celery_app.task(name="atlas.generar_publicacion")
def generar_publicacion(job_id: str) -> str:
    procesar_publicacion(job_id)
    return job_id
