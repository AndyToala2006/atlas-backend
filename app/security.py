"""Seguridad: hash de contraseñas (bcrypt) y emisión/validación de JWT."""
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from .config import settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(clave: str) -> str:
    return _pwd.hash(clave)


def verify_password(clave: str, hash_guardado: str) -> bool:
    return _pwd.verify(clave, hash_guardado)


def create_token(usuario) -> str:
    """Emite un JWT que lleva la identidad del usuario en los claims.

    Al viajar la identidad dentro del token firmado, las rutas protegidas NO
    necesitan volver a consultar la tabla de usuarios en cada petición.
    """
    ahora = datetime.now(timezone.utc)
    payload = {
        "sub": str(usuario.id),
        "email": usuario.email,
        "nombre": usuario.nombre,
        "iat": ahora,
        "exp": ahora + timedelta(minutes=settings.jwt_exp_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])
