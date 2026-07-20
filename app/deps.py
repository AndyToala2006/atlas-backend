"""Dependencias reutilizables: sesión de BD y usuario autenticado.

Punto clave del taller (autenticación sin consultas redundantes):

  * `get_current_user`  -> reconstruye la identidad desde los claims del JWT.
                           NO consulta la base de datos. Es la que usan las rutas.
  * `get_current_user_db` -> versión "ingenua" que sí golpea la base en cada
                           request. Solo existe para comparar en el video.
"""
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .database import SessionLocal
from .models import Usuario
from .security import decode_token

bearer = HTTPBearer(auto_error=True)


def get_db():
    db = SessionLocal()
    db.info["query_count"] = 0  # reinicia el contador por request
    try:
        yield db
    finally:
        db.close()


@dataclass
class Principal:
    """Identidad ligera del usuario, tomada del token (sin tocar la base)."""

    id: int
    email: str
    nombre: str | None = None


def get_current_user(
    cred: HTTPAuthorizationCredentials = Depends(bearer),
) -> Principal:
    try:
        payload = decode_token(cred.credentials)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
        )
    return Principal(
        id=int(payload["sub"]),
        email=payload.get("email"),
        nombre=payload.get("nombre"),
    )


def get_current_user_db(
    cred: HTTPAuthorizationCredentials = Depends(bearer),
    db=Depends(get_db),
) -> Usuario:
    """Versión ineficiente: valida el token y ADEMÁS consulta la base cada vez."""
    try:
        payload = decode_token(cred.credentials)
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    usuario = db.get(Usuario, int(payload["sub"]))  # <-- consulta redundante
    if usuario is None:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    return usuario
