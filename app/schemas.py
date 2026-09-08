"""Esquemas Pydantic (entrada/salida de la API)."""
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---- Autenticación ----
class RegistroIn(BaseModel):
    email: EmailStr
    nombre: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=6, max_length=72)
    tono: str = Field(default="cercano", max_length=60)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UsuarioOut(BaseModel):
    id: int
    email: EmailStr
    nombre: str


# ---- Ideas ----
def _normalizar_etiquetas(valor: list[str] | None) -> list[str] | None:
    """Deja las etiquetas en forma canónica ANTES de tocar la base de datos.

    La validación vive aquí, en el borde de la API, y no en el router: así el
    endpoint recibe una lista ya limpia y no puede colarse una etiqueta
    duplicada, vacía o desmesurada. La normalización (recorte + minúsculas) es
    la que hace que el get-or-create de `routers/ideas.py` reutilice de verdad
    la fila existente: "  IA " y "ia" tienen que ser la MISMA etiqueta.
    Se conserva el orden en el que el usuario las escribió porque es el que
    verá de vuelta en la app; por eso se deduplica con una lista y no con set.
    """
    if valor is None:
        return None

    limpias: list[str] = []
    for bruta in valor:
        nombre = bruta.strip().lower()
        if not nombre:
            continue  # una etiqueta en blanco no aporta nada, se descarta
        if len(nombre) > 40:
            raise ValueError("Cada etiqueta admite como máximo 40 caracteres")
        if nombre not in limpias:
            limpias.append(nombre)
    return limpias


class IdeaCreate(BaseModel):
    titulo: str = Field(min_length=2, max_length=160)
    contenido: str = Field(min_length=1)
    origen: str = Field(default="texto", pattern="^(texto|audio)$")
    # El tope de 5 etiquetas ya lo aplicaba el cliente Flutter; si solo vive
    # allí, cualquiera que llame a la API con curl se lo salta. El límite real
    # tiene que estar en el servidor.
    etiquetas: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("etiquetas")
    @classmethod
    def _limpiar_etiquetas(cls, valor: list[str]) -> list[str]:
        return _normalizar_etiquetas(valor)


class IdeaUpdate(BaseModel):
    """Cuerpo de un PATCH: TODO opcional, porque una edición es parcial.

    El default None distingue "no me mandaron el campo" de "me mandaron un
    valor": el router usa model_dump(exclude_unset=True) para aplicar solo lo
    que viajó de verdad y no pisar con nulos lo que el usuario no tocó.
    Las restricciones son idénticas a las de IdeaCreate: editar no puede ser
    una puerta trasera para guardar datos que crear rechaza.
    """

    titulo: str | None = Field(default=None, min_length=2, max_length=160)
    contenido: str | None = Field(default=None, min_length=1)
    origen: str | None = Field(default=None, pattern="^(texto|audio)$")
    etiquetas: list[str] | None = Field(default=None, max_length=5)

    @field_validator("etiquetas")
    @classmethod
    def _limpiar_etiquetas(cls, valor: list[str] | None) -> list[str] | None:
        return _normalizar_etiquetas(valor)


class IdeaOut(BaseModel):
    """Salida LIGERA: la del listado. A propósito NO lleva `contenido`.

    Es una técnica de optimización explícita (control del tamaño de la
    respuesta): el texto completo de cada idea puede ser de varios KB y en una
    lista de 50 ideas nadie lo lee, solo engorda la respuesta y el consumo de
    datos móviles. El contenido se paga únicamente al abrir el detalle.
    """

    id: int
    titulo: str
    estado: str
    origen: str
    etiquetas: list[str]
    num_publicaciones: int
    creado_en: datetime


class IdeaDetalleOut(IdeaOut):
    """Salida COMPLETA: lo mismo que el listado más el contenido pesado.

    Hereda de IdeaOut para que ambos contratos no puedan divergir: si mañana se
    añade un campo al listado, el detalle lo tiene gratis.
    """

    contenido: str


# ---- Publicaciones / Jobs ----
class PublicarOut(BaseModel):
    job_id: str
    estado: str
    modo: str  # asincrono | sincrono


class JobOut(BaseModel):
    id: str
    idea_id: int
    estado: str
    resultado_publicacion_id: int | None = None
    error: str | None = None


class MetricaCreate(BaseModel):
    fuente: str = Field(default="manual", pattern="^(manual|api)$")
    likes: int = 0
    comentarios: int = 0
    compartidos: int = 0
    alcance: int = 0
