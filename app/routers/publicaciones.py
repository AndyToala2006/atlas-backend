"""Rutas de publicaciones y jobs.

- POST /ideas/{id}/publicar : encola la generación con IA (tarea asíncrona).
    ?sync=true fuerza el modo bloqueante SOLO para comparar tiempos en el video.
- GET  /jobs/{id}           : consulta el estado del trabajo encolado.
- POST /publicaciones/{id}/metricas : registra una métrica e INVALIDA el caché
                                       del dashboard (cache-aside).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from ..cache import cache_invalidate, dashboard_key
from ..deps import Principal, get_current_user, get_db
from ..models import Idea, Job, MetricaPublicacion, Publicacion
from ..schemas import JobOut, MetricaCreate, PublicarOut
from ..tasks.jobs import generar_publicacion, procesar_publicacion

router = APIRouter(tags=["Publicaciones"])


@router.post("/ideas/{idea_id}/publicar", response_model=PublicarOut, status_code=status.HTTP_202_ACCEPTED)
def publicar_idea(
    idea_id: int,
    response: Response,
    sync: bool = False,
    user: Principal = Depends(get_current_user),
    db=Depends(get_db),
):
    idea = db.get(Idea, idea_id)
    if idea is None or idea.usuario_id != user.id:
        raise HTTPException(status_code=404, detail="Idea no encontrada")

    job = Job(id=str(uuid.uuid4()), idea_id=idea.id, estado="queued")
    idea.estado = "procesando"
    db.add(job)
    db.commit()

    if sync:
        # MODO SÍNCRONO (comparación): bloquea el request hasta terminar la IA.
        procesar_publicacion(job.id)
        response.status_code = status.HTTP_200_OK
        return PublicarOut(job_id=job.id, estado="done", modo="sincrono")

    # MODO ASÍNCRONO (real): se encola y el request responde al instante.
    generar_publicacion.delay(job.id)
    return PublicarOut(job_id=job.id, estado="queued", modo="asincrono")


@router.get("/jobs/{job_id}", response_model=JobOut)
def estado_job(job_id: str, user: Principal = Depends(get_current_user), db=Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return JobOut(
        id=job.id,
        idea_id=job.idea_id,
        estado=job.estado,
        resultado_publicacion_id=job.resultado_publicacion_id,
        error=job.error,
    )


@router.post("/publicaciones/{pub_id}/metricas", status_code=status.HTTP_201_CREATED)
def registrar_metrica(
    pub_id: int,
    datos: MetricaCreate,
    user: Principal = Depends(get_current_user),
    db=Depends(get_db),
):
    publicacion = db.execute(
        select(Publicacion).where(Publicacion.id == pub_id)
    ).scalar_one_or_none()
    if publicacion is None:
        raise HTTPException(status_code=404, detail="Publicación no encontrada")

    metrica = MetricaPublicacion(
        publicacion_id=pub_id,
        fuente=datos.fuente,
        likes=datos.likes,
        comentarios=datos.comentarios,
        compartidos=datos.compartidos,
        alcance=datos.alcance,
    )
    db.add(metrica)
    db.commit()

    # INVALIDACIÓN EXPLÍCITA: los números del dashboard cambiaron -> borrar caché.
    cache_invalidate(dashboard_key(user.id))
    return {"ok": True, "cache": "invalidado"}
