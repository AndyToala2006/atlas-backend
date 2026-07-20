"""Caché-aside sobre Redis.

Estrategia cache-aside (lazy caching):
  1. La aplicación pregunta primero al caché.
  2. Si hay dato (HIT) lo devuelve y no toca la base de datos.
  3. Si no hay (MISS) consulta la base, guarda el resultado con un TTL y lo devuelve.
  4. En cada escritura que invalide el dato se elimina la clave EXPLÍCITAMENTE.
"""
import json

import redis

from .config import settings

r = redis.Redis.from_url(settings.redis_url, decode_responses=True)


def cache_get(clave: str):
    valor = r.get(clave)
    return json.loads(valor) if valor is not None else None


def cache_set(clave: str, valor, ttl: int) -> None:
    r.set(clave, json.dumps(valor, default=str), ex=ttl)


def cache_invalidate(*claves: str) -> None:
    """Invalidación explícita: borra una o varias claves del caché."""
    claves = [c for c in claves if c]
    if claves:
        r.delete(*claves)


def dashboard_key(usuario_id: int) -> str:
    return f"atlas:dashboard:user:{usuario_id}"
