"""Motor de base de datos, sesiones y contador de consultas.

El contador de consultas es la pieza que hace VISIBLE el problema N+1: cada vez
que el ORM ejecuta una sentencia (incluidas las cargas perezosas de relaciones)
incrementamos un contador guardado en la propia sesión. Los endpoints lo leen y
lo devuelven en la cabecera `X-Query-Count`, para comparar antes vs. después.
"""
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session as SASession

from .config import settings

# Si se define un esquema (caso Supabase real), la conexión trabaja dentro de él.
_connect_args = {}
if settings.db_schema:
    _connect_args["options"] = f"-csearch_path={settings.db_schema}"

engine = create_engine(
    settings.database_url, pool_pre_ping=True, future=True, connect_args=_connect_args
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


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
