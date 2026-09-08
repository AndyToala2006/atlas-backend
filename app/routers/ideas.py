"""Rutas de ideas: CRUD completo y demostración de la consulta N+1.

CRUD (todo exige token; una idea solo la ve y la toca su dueño):
  GET    /ideas        -> listado LIGERO      (list[IdeaOut], sin `contenido`)
  POST   /ideas        -> crear               (IdeaDetalleOut, 201)
  GET    /ideas/{id}   -> detalle COMPLETO    (IdeaDetalleOut)
  PATCH  /ideas/{id}   -> edición parcial     (IdeaDetalleOut)
  DELETE /ideas/{id}   -> borrado             (204 sin cuerpo)

Optimizaciones que se defienden en este módulo:

1) N+1 vs eager loading (interruptor de demostración, no tocar):
   GET /ideas?optimized=false -> ingenuo: 1 consulta por la lista + 2 por CADA idea
                                 (etiquetas y publicaciones cargadas de forma perezosa).
   GET /ideas?optimized=true  -> corregido: eager loading con selectinload, número
                                 de consultas constante sin importar cuántas ideas haya.
   El costo real se lee en la cabecera de respuesta `X-Query-Count`.

2) Control del tamaño de la respuesta: el listado devuelve IdeaOut (sin el campo
   pesado `contenido`) y solo el detalle devuelve IdeaDetalleOut. Se evita
   arrastrar KB de texto que la pantalla de lista ni siquiera muestra.

3) Caché-aside: las TRES escrituras (POST, PATCH, DELETE) invalidan la clave del
   dashboard, porque ese reporte cuenta ideas y quedaría rancio si no se borra.
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from ..cache import cache_invalidate, dashboard_key
from ..deps import Principal, get_current_user, get_db
from ..models import Etiqueta, Idea, Job
from ..schemas import IdeaCreate, IdeaDetalleOut, IdeaOut, IdeaUpdate

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


def _a_detalle(idea: Idea) -> IdeaDetalleOut:
    # Misma forma que el listado más el contenido: el detalle es el ÚNICO sitio
    # donde ese texto pesado viaja por la red.
    return IdeaDetalleOut(**_a_salida(idea).model_dump(), contenido=idea.contenido)


def _resolver_etiquetas(db, nombres: list[str]) -> list[Etiqueta]:
    """Get-or-create de etiquetas: reutiliza la fila si el nombre ya existe.

    Vive aquí, extraído del endpoint de creación, porque el PATCH necesita
    exactamente la misma lógica; duplicarla acabaría dando dos reglas distintas
    para la misma tabla. Los nombres llegan ya normalizados (recortados, en
    minúsculas y sin duplicados) desde el validador del esquema, así que aquí no
    hace falta volver a limpiarlos.
    """
    etiquetas: list[Etiqueta] = []
    for nombre in nombres:
        etiqueta = db.execute(
            select(Etiqueta).where(Etiqueta.nombre == nombre)
        ).scalar_one_or_none()
        if etiqueta is None:
            etiqueta = Etiqueta(nombre=nombre)
            db.add(etiqueta)
        etiquetas.append(etiqueta)
    return etiquetas


def _buscar_idea(db, idea_id: int, usuario_id: int, con_relaciones: bool = True) -> Idea:
    """Recupera UNA idea del usuario o corta el request con un 404.

    El filtro por dueño va DENTRO del WHERE y no en un `if` posterior: así la
    base de datos nunca llega a entregarnos la fila ajena y no hay manera de
    olvidarse la comprobación al añadir una ruta nueva.

    Se responde 404 y no 403 a propósito: un 403 confirmaría que ese id existe y
    es de otra persona (fuga de información por el código de estado). Para quien
    no es su dueño, la idea sencillamente no existe.
    """
    consulta = select(Idea).where(Idea.id == idea_id, Idea.usuario_id == usuario_id)
    if con_relaciones:
        # El detalle siempre pinta etiquetas y cuenta publicaciones: se piden
        # eager para no pagar dos lazy-loads extra al construir la respuesta.
        consulta = consulta.options(
            selectinload(Idea.etiquetas), selectinload(Idea.publicaciones)
        )

    idea = db.execute(consulta).scalar_one_or_none()
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea no encontrada")
    return idea


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


@router.post("", response_model=IdeaDetalleOut, status_code=status.HTTP_201_CREATED)
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
    idea.etiquetas = _resolver_etiquetas(db, datos.etiquetas)

    db.add(idea)
    db.commit()
    db.refresh(idea)

    # El dashboard cachea `total_ideas`: si no se borra la clave, el usuario crea
    # una idea y el reporte le sigue enseñando el número viejo hasta que venza el
    # TTL. La invalidación explícita es la otra mitad del cache-aside.
    cache_invalidate(dashboard_key(user.id))
    return _a_detalle(idea)


@router.get("/{idea_id}", response_model=IdeaDetalleOut)
def obtener_idea(
    idea_id: int,
    user: Principal = Depends(get_current_user),
    db=Depends(get_db),
):
    return _a_detalle(_buscar_idea(db, idea_id, user.id))


@router.patch("/{idea_id}", response_model=IdeaDetalleOut)
def actualizar_idea(
    idea_id: int,
    datos: IdeaUpdate,
    user: Principal = Depends(get_current_user),
    db=Depends(get_db),
):
    idea = _buscar_idea(db, idea_id, user.id)

    # exclude_unset separa "no me mandaron el campo" de "me mandaron un valor":
    # un PATCH solo debe tocar lo que viajó de verdad en el cuerpo.
    cambios = datos.model_dump(exclude_unset=True)
    if not cambios:
        raise HTTPException(
            status_code=400, detail="No se envió ningún campo que actualizar"
        )

    if cambios.get("etiquetas") is not None:
        # Una relación N:M no se parchea elemento a elemento: el cliente manda la
        # lista final, así que se REEMPLAZA la colección entera y el ORM decide
        # qué filas de idea_etiqueta insertar y cuáles borrar.
        idea.etiquetas = _resolver_etiquetas(db, cambios["etiquetas"])

    for campo in ("titulo", "contenido", "origen"):
        valor = cambios.get(campo)
        # Un null explícito se ignora: estas columnas son NOT NULL en la base, o
        # sea que "borrar el título" no es una operación posible.
        if campo in cambios and valor is not None:
            setattr(idea, campo, valor)

    db.commit()
    db.refresh(idea)

    # Editar también mueve los números del dashboard (por ejemplo el reparto por
    # etiqueta), así que se invalida igual que al crear.
    cache_invalidate(dashboard_key(user.id))
    return _a_detalle(idea)


@router.delete(
    "/{idea_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
def eliminar_idea(
    idea_id: int,
    user: Principal = Depends(get_current_user),
    db=Depends(get_db),
):
    idea = _buscar_idea(db, idea_id, user.id)

    # BORRADO EN CASCADA - revisión camino por camino sobre app/models.py:
    #   * publicacion         -> Idea.publicaciones lleva cascade="all, delete-orphan",
    #                            el ORM las borra antes que la idea.
    #   * metrica_publicacion -> Publicacion.metricas repite ese cascade, así que
    #                            cada métrica cae detrás de su publicación.
    #   * idea_etiqueta       -> es la tabla puente (secondary) de la N:M; SQLAlchemy
    #                            borra esas filas al borrar la idea y la FK además
    #                            declara ON DELETE CASCADE. La etiqueta en sí NO se
    #                            borra: es un catálogo compartido con otras ideas.
    #   * job                 -> ÚNICO camino sin relación ORM (Job apunta a idea_id,
    #                            pero Idea no tiene `jobs`). Quedaría colgando de la
    #                            sola FK ON DELETE CASCADE de la base; para no depender
    #                            de eso lo borramos explícitamente aquí, en la MISMA
    #                            transacción y antes que la idea, que si no la FK
    #                            rechazaría el DELETE.
    db.execute(delete(Job).where(Job.idea_id == idea.id))
    db.delete(idea)
    db.commit()

    cache_invalidate(dashboard_key(user.id))

    # 204 significa "hecho, y no hay nada que devolver": el cuerpo va vacío.
    return Response(status_code=status.HTTP_204_NO_CONTENT)
