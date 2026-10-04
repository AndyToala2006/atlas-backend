"""Tarea asíncrona: transformar una idea en una publicación con IA.

Este es el caso real de Atlas que justifica una cola de trabajo: generar la
publicación es LENTO (llamada a un modelo de lenguaje por OpenRouter, varios
segundos). Si se hiciera dentro del request, el usuario esperaría con la app
bloqueada. En su lugar el endpoint encola el trabajo y responde al instante; el
worker lo procesa y la app consulta `GET /jobs/{id}` hasta que termina.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import ia
from ..cache import cache_invalidate, dashboard_key
from ..config import settings
from ..database import SessionLocal
from ..models import Idea, Job, Publicacion, Usuario
from .celery_app import celery_app


def procesar_publicacion(job_id: str, red_social: str = "instagram") -> None:
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

        tono = idea.usuario.perfil_tono.nombre if idea.usuario.perfil_tono else "neutral"

        # La llamada lenta: esto es exactamente lo que se sacó del request.
        generado = ia.generar_publicacion(
            titulo=idea.titulo,
            contenido=idea.contenido,
            tono=tono,
            red_social=red_social,
            api_key=settings.openrouter_api_key,
            modelo=settings.openrouter_model,
            timeout_s=settings.openrouter_timeout_s,
            latencia_simulada_ms=settings.ia_latency_ms,
        )

        publicacion = Publicacion(
            idea_id=idea.id,
            red_social=red_social,
            contenido_generado=generado.texto,
            tono=tono,
            estado="generada",
            modelo_ia=generado.modelo,
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
            # ErrorIA ya trae un mensaje para el usuario; cualquier otra
            # excepción es un fallo interno y no debe filtrar detalles.
            job.error = (str(exc) if isinstance(exc, ia.ErrorIA) else "Error interno al generar la publicación.")[:255]
            # La idea quedó en "procesando" al encolar. Si se dejara así, la app
            # la mostraría procesándose para siempre: vuelve a "publicada" si ya
            # tenía publicaciones anteriores y a "borrador" si esta era la primera.
            idea = db.get(Idea, job.idea_id)
            if idea is not None and idea.estado == "procesando":
                previas = db.scalar(
                    select(func.count()).select_from(Publicacion).where(Publicacion.idea_id == idea.id)
                )
                idea.estado = "publicada" if previas else "borrador"
            db.commit()
    finally:
        db.close()


@celery_app.task(name="atlas.generar_publicacion")
def generar_publicacion(job_id: str, red_social: str = "instagram") -> str:
    procesar_publicacion(job_id, red_social)
    return job_id
