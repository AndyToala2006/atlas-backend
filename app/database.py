"""Motor de base de datos, sesiones y contador de consultas.

El contador de consultas es la pieza que hace VISIBLE el problema N+1: cada vez
que el ORM ejecuta una sentencia (incluidas las cargas perezosas de relaciones)
incrementamos un contador guardado en la propia sesión. Los endpoints lo leen y
lo devuelven en la cabecera `X-Query-Count`, para comparar antes vs. después.
"""
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session as SASession

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


# Si se apunta a Supabase (esquema aparte), fijamos el search_path en CADA conexión
# con un SET explícito. Es robusto incluso a través del pooler de Supabase, y así
# nuestras tablas se crean y consultan solo dentro de ese esquema, sin tocar las
# tablas de la Semana 4 que viven en "public".
if settings.db_schema:

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_conn, conn_record):
        cur = dbapi_conn.cursor()
        cur.execute(f'SET search_path TO "{settings.db_schema}"')
        cur.close()


def ensure_schema() -> None:
    """Crea el esquema aparte si se configuró uno (no toca las tablas existentes)."""
    if settings.db_schema:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.db_schema}"'))
            conn.commit()


@event.listens_for(SASession, "do_orm_execute")
def _contar_consultas(state) -> None:
    """Cuenta cada ejecución ORM (incluye lazy-loads de relaciones)."""
    sesion = state.session
    if sesion is not None:
        sesion.info["query_count"] = sesion.info.get("query_count", 0) + 1
