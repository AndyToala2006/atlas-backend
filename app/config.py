"""Configuración central del backend.

Toda la configuración se lee de variables de entorno (archivo .env) para no
mezclar secretos ni cadenas de conexión con el código. Ver .env.example.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Conexión a Postgres (la base de datos de Atlas definida en la Semana 4).
    database_url: str = "postgresql+psycopg2://atlas:atlas@localhost:5432/atlas"

    # Esquema donde viven las tablas. Si se apunta a Supabase real, se usa un
    # esquema aparte (p. ej. "atlas_app") para NO tocar tus tablas de la Semana 4.
    # Vacío = esquema por defecto (public), como en el Postgres local.
    db_schema: str | None = None

    # Redis: se usa como almacén del caché-aside y como broker/back-end del worker.
    redis_url: str = "redis://localhost:6379/0"

    # Autenticación con JWT.
    jwt_secret: str = "cambia-esta-clave-super-secreta-en-produccion"
    jwt_alg: str = "HS256"
    jwt_exp_minutes: int = 120

    # Tiempo de vida (TTL) del caché del dashboard, en segundos.
    cache_ttl_seconds: int = 60

    # Latencia extra del reporte del dashboard (ms). Con datos reales y/o base
    # remota (Supabase) la lentitud es genuina, así que por defecto es 0 (sin
    # simular nada). Se puede subir solo si se quisiera exagerar el efecto.
    report_latency_ms: int = 0

    # Latencia simulada de la generación con IA (ms) en el worker asíncrono.
    ia_latency_ms: int = 3000


settings = Settings()
