"""Rutas de ideas. Aquí se muestra y se corrige la consulta N+1.

GET /ideas?optimized=false  -> ingenuo: 1 consulta por la lista + 2 por CADA idea
                               (etiquetas y publicaciones cargadas de forma perezosa).
GET /ideas?optimized=true   -> corregido: eager loading con selectinload, número
                               de consultas constante sin importar cuántas ideas haya.

El costo real se lee en la cabecera de respuesta `X-Query-Count`.
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import Principal, get_current_user, get_db
from ..models import Etiqueta, Idea
from ..schemas import IdeaCreate, IdeaOut

router = APIRouter(prefix="/ideas", tags=["Ideas"])


def _a_salida(idea: Idea) -> IdeaOut:
    # Acceder a idea.etiquetas / idea.publicaciones dispara el lazy-load si la
    # consulta no las precargó: ese acceso, repetido por idea, es el N+1.
    return IdeaOut(
        id=idea.id,
        titulo=idea.titulo,
        estado=idea.estado,
        origen=idea.origen,
        etiquetas=[e.nombre for e in idea.etiquetas],
        num_publicaciones=len(idea.publicaciones),
        creado_en=idea.creado_en,
    )


@router.get("", response_model=list[IdeaOut])
def listar_ideas(
    response: Response,
    optimized: bool = True,
    user: Principal = Depends(get_current_user),
    db=Depends(get_db),
):
    consulta = (
        select(Idea).where(Idea.usuario_id == user.id).order_by(Idea.creado_en.desc())
    )
    if optimized:
        # EAGER LOADING: precarga etiquetas y publicaciones en 2 consultas extra
        # y constantes (no dependen del número de ideas). Elimina el N+1.
        consulta = consulta.options(
            selectinload(Idea.etiquetas), selectinload(Idea.publicaciones)
        )

    ideas = db.execute(consulta).scalars().all()
    salida = [_a_salida(i) for i in ideas]

    response.headers["X-Query-Count"] = str(db.info.get("query_count", 0))
    response.headers["X-Optimized"] = str(optimized).lower()
    return salida


@router.post("", response_model=IdeaOut, status_code=status.HTTP_201_CREATED)
def crear_idea(
    datos: IdeaCreate,
    user: Principal = Depends(get_current_user),
    db=Depends(get_db),
):
    idea = Idea(
        usuario_id=user.id,
        titulo=datos.titulo,
        contenido=datos.contenido,
        origen=datos.origen,
    )
    # Reutiliza etiquetas existentes o crea las nuevas (get-or-create).
    for nombre in {n.strip().lower() for n in datos.etiquetas if n.strip()}:
        etiqueta = db.execute(
            select(Etiqueta).where(Etiqueta.nombre == nombre)
        ).scalar_one_or_none()
        if etiqueta is None:
            etiqueta = Etiqueta(nombre=nombre)
            db.add(etiqueta)
        idea.etiquetas.append(etiqueta)

    db.add(idea)
    db.commit()
    db.refresh(idea)
    return _a_salida(idea)


@router.get("/{idea_id}", response_model=IdeaOut)
def obtener_idea(
    idea_id: int,
    user: Principal = Depends(get_current_user),
    db=Depends(get_db),
):
    idea = db.execute(
        select(Idea)
        .options(selectinload(Idea.etiquetas), selectinload(Idea.publicaciones))
        .where(Idea.id == idea_id, Idea.usuario_id == user.id)
    ).scalar_one_or_none()
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea no encontrada")
    return _a_salida(idea)
