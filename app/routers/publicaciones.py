"""Rutas de publicaciones y jobs.

- POST /ideas/{id}/publicar : encola la generación con IA (tarea asíncrona).
    ?sync=true fuerza el modo bloqueante SOLO para comparar tiempos en el video.
- GET  /jobs/{id}           : consulta el estado del trabajo encolado.
- POST /publicaciones/{id}/metricas : registra una métrica e INVALIDA el caché
                                       del dashboard (cache-aside).

AUTORIZACIÓN POR OBJETO: ni el job ni la publicación llevan `usuario_id`, así que
el dueño se demuestra subiendo por la relación hasta la idea (Job -> Idea y
Publicacion -> Idea). Todas las rutas de este módulo atan el recurso a
`user.id` y responden 404 —nunca 403— cuando el recurso es de otro: un 403
confirmaría que el id existe y permitiría enumerar recursos ajenos.
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
    # El id del job es un uuid, pero un uuid NO es un control de acceso: quien lo
    # ve una vez (logs, capturas, otro cliente) podría consultarlo para siempre.
    # El JOIN con Idea es lo que prueba la propiedad, y el filtro por dueño va
    # DENTRO del WHERE y no en un `if` posterior: así la base devuelve cero filas
    # para un job ajeno y el endpoint no llega a tener nunca en memoria datos de
    # otro usuario que se puedan filtrar por descuido en una respuesta o un log.
    job = db.execute(
        select(Job)
        .join(Idea, Job.idea_id == Idea.id)
        .where(Job.id == job_id, Idea.usuario_id == user.id)
    ).scalar_one_or_none()
    if job is None:
        # 404 y no 403: un job de otro usuario debe ser indistinguible de uno
        # que no existe, para no confirmar qué uuids son válidos.
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
    # Esta es una ESCRITURA sobre un recurso ajeno si no se comprueba el dueño:
    # el id de publicación es un entero correlativo, así que basta con probar
    # 1, 2, 3... para ensuciar las métricas de cualquiera. La publicación no
    # guarda `usuario_id`, por eso se sube por la relación Publicacion -> Idea.
    # El filtro por dueño va DENTRO del WHERE y no en un `if` posterior porque
    # así solo existe un camino posible: si la fila no es del usuario, la
    # consulta no la devuelve y es imposible olvidarse de la comprobación al
    # tocar el código más adelante.
    publicacion = db.execute(
        select(Publicacion)
        .join(Idea, Publicacion.idea_id == Idea.id)
        .where(Publicacion.id == pub_id, Idea.usuario_id == user.id)
    ).scalar_one_or_none()
    if publicacion is None:
        # 404 en vez de 403: no se revela la existencia de la publicación ajena.
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
