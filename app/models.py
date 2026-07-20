"""Modelos ORM de Atlas (subconjunto del DER de la Semana 4).

Aquí se toman y JUSTIFICAN las decisiones de carga (lazy vs eager) por relación.
La regla general del proyecto: por defecto todo es lazy (`select`) para no traer
datos que no se van a usar; cuando un endpoint sí necesita una relación, se pide
eager de forma explícita con `selectinload(...)` en la consulta. Esto se ve en
`routers/ideas.py` (parámetro ?optimized) y en el worker.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


# Tabla puente de la relación N:M idea <-> etiqueta.
idea_etiqueta = Table(
    "idea_etiqueta",
    Base.metadata,
    Column("idea_id", ForeignKey("idea.id", ondelete="CASCADE"), primary_key=True),
    Column("etiqueta_id", ForeignKey("etiqueta.id", ondelete="CASCADE"), primary_key=True),
)


class Usuario(Base):
    __tablename__ = "usuario"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)

    # JUSTIFICACIÓN (LAZY): al autenticar solo necesitamos identidad, casi nunca
    # todas las ideas del usuario. Cargarlas siempre sería traer datos inútiles,
    # por eso esta relación es perezosa y se resuelve solo si alguien la pide.
    ideas: Mapped[list["Idea"]] = relationship(
        back_populates="usuario", lazy="select", cascade="all, delete-orphan"
    )

    # JUSTIFICACIÓN (EAGER 1:1): el perfil de tono es un único registro pequeño
    # que el worker de IA necesita siempre para generar en el tono correcto;
    # traerlo junto al usuario evita una segunda ida a la base.
    perfil_tono: Mapped["PerfilTono"] = relationship(
        back_populates="usuario",
        lazy="joined",
        uselist=False,
        cascade="all, delete-orphan",
    )


class PerfilTono(Base):
    __tablename__ = "perfil_tono"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuario.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    nombre: Mapped[str] = mapped_column(String(60), nullable=False)  # cercano, profesional...
    descripcion: Mapped[str] = mapped_column(String(255), default="")

    usuario: Mapped["Usuario"] = relationship(back_populates="perfil_tono")


class Etiqueta(Base):
    __tablename__ = "etiqueta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(60), unique=True, index=True, nullable=False)
    color: Mapped[str] = mapped_column(String(9), default="#888888")

    ideas: Mapped[list["Idea"]] = relationship(
        secondary=idea_etiqueta, back_populates="etiquetas", lazy="select"
    )


class Idea(Base):
    __tablename__ = "idea"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuario.id", ondelete="CASCADE"), index=True, nullable=False
    )
    titulo: Mapped[str] = mapped_column(String(160), nullable=False)
    contenido: Mapped[str] = mapped_column(Text, nullable=False)
    origen: Mapped[str] = mapped_column(String(10), default="texto")  # texto | audio
    estado: Mapped[str] = mapped_column(String(15), default="borrador")  # borrador|procesando|publicada
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)

    usuario: Mapped["Usuario"] = relationship(back_populates="ideas")

    # LAZY por defecto a propósito: así el listado ingenuo dispara el N+1 y el
    # listado optimizado lo corrige pidiendo selectinload en la consulta.
    etiquetas: Mapped[list["Etiqueta"]] = relationship(
        secondary=idea_etiqueta, back_populates="ideas", lazy="select"
    )
    publicaciones: Mapped[list["Publicacion"]] = relationship(
        back_populates="idea", lazy="select", cascade="all, delete-orphan"
    )


class Publicacion(Base):
    __tablename__ = "publicacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idea_id: Mapped[int] = mapped_column(
        ForeignKey("idea.id", ondelete="CASCADE"), index=True, nullable=False
    )
    red_social: Mapped[str] = mapped_column(String(30), default="instagram")
    contenido_generado: Mapped[str] = mapped_column(Text, nullable=False)
    tono: Mapped[str] = mapped_column(String(60), default="neutral")
    estado: Mapped[str] = mapped_column(String(15), default="generada")
    modelo_ia: Mapped[str] = mapped_column(String(40), default="atlas-sim-1")
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)

    idea: Mapped["Idea"] = relationship(back_populates="publicaciones")
    metricas: Mapped[list["MetricaPublicacion"]] = relationship(
        back_populates="publicacion", lazy="select", cascade="all, delete-orphan"
    )


class MetricaPublicacion(Base):
    __tablename__ = "metrica_publicacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publicacion_id: Mapped[int] = mapped_column(
        ForeignKey("publicacion.id", ondelete="CASCADE"), index=True, nullable=False
    )
    fuente: Mapped[str] = mapped_column(String(10), default="manual")  # manual | api
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comentarios: Mapped[int] = mapped_column(Integer, default=0)
    compartidos: Mapped[int] = mapped_column(Integer, default=0)
    alcance: Mapped[int] = mapped_column(Integer, default=0)
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)

    publicacion: Mapped["Publicacion"] = relationship(back_populates="metricas")


class Job(Base):
    """Seguimiento de las tareas asíncronas (idea -> publicación con IA)."""

    __tablename__ = "job"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid
    idea_id: Mapped[int] = mapped_column(ForeignKey("idea.id", ondelete="CASCADE"), nullable=False)
    estado: Mapped[str] = mapped_column(String(15), default="queued")  # queued|processing|done|error
    resultado_publicacion_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_ahora, onupdate=_ahora
    )

    __table_args__ = (UniqueConstraint("id", name="uq_job_id"),)
