"""Esquemas Pydantic (entrada/salida de la API)."""
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


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
class IdeaCreate(BaseModel):
    titulo: str = Field(min_length=2, max_length=160)
    contenido: str = Field(min_length=1)
    origen: str = Field(default="texto", pattern="^(texto|audio)$")
    etiquetas: list[str] = Field(default_factory=list)


class IdeaOut(BaseModel):
    id: int
    titulo: str
    estado: str
    origen: str
    etiquetas: list[str]
    num_publicaciones: int
    creado_en: datetime


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
