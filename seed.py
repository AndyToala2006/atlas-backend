"""Carga datos de prueba en la base de Atlas (versión con volumen real).

Ejecutar:  python seed.py

Genera miles de registros con inserciones masivas (rápidas incluso contra
Supabase). El usuario demo recibe muchas ideas para que el problema N+1 se note
de verdad, y publicaciones con métricas para que el dashboard tenga qué agregar.

    Usuario demo:  demo@atlas.app  /  atlas123

Tamaños configurables por variables de entorno (con valores por defecto):
    SEED_DEMO_IDEAS      (50)  ideas del usuario demo  -> maneja el N+1
    SEED_USERS           (30)  otros usuarios
    SEED_IDEAS_PER_USER  (40)  ideas por cada otro usuario
"""
import os
import random

from sqlalchemy import delete, insert

from app.database import Base, SessionLocal, engine, ensure_schema
from app.models import (
    Etiqueta,
    Idea,
    MetricaPublicacion,
    PerfilTono,
    Publicacion,
    Usuario,
    idea_etiqueta,
)
from app.security import hash_password

random.seed(42)  # reproducible

DEMO_IDEAS = int(os.getenv("SEED_DEMO_IDEAS", "50"))
OTROS_USUARIOS = int(os.getenv("SEED_USERS", "30"))
IDEAS_POR_USUARIO = int(os.getenv("SEED_IDEAS_PER_USER", "40"))

ETIQUETAS = [
    "productividad", "marca personal", "ia", "finanzas", "habitos",
    "emprendimiento", "salud", "aprendizaje", "marketing", "diseno",
]
TONOS = ["cercano", "profesional", "inspirador"]
REDES = ["instagram", "linkedin", "x"]
TEMAS = [
    "Rutina de captura de ideas", "Vencer la página en blanco", "IA como copiloto",
    "Presupuesto base cero", "Construir en público", "Micro-hábitos que escalan",
    "De la nota de voz al post", "Validar antes de construir", "Enfoque profundo",
    "Aprender en público", "Marca personal desde cero", "Sistemas sobre metas",
]


def reset(db) -> None:
    db.execute(delete(MetricaPublicacion))
    db.execute(delete(Publicacion))
    db.execute(idea_etiqueta.delete())
    db.execute(delete(Idea))
    db.execute(delete(PerfilTono))
    db.execute(delete(Etiqueta))
    db.execute(delete(Usuario))
    db.commit()


def crear_ideas(db, usuario_id, cantidad, etiqueta_ids, con_publicaciones):
    """Inserta ideas en bloque y devuelve totales creados (pubs, metricas)."""
    filas = []
    for i in range(cantidad):
        tema = random.choice(TEMAS)
        filas.append({
            "usuario_id": usuario_id,
            "titulo": f"{tema} #{i + 1}",
            "contenido": f"{tema}: una idea para trabajar la constancia y el contenido.",
            "origen": random.choice(["texto", "texto", "audio"]),
            "estado": "borrador",
        })
    idea_ids = list(db.execute(insert(Idea).returning(Idea.id), filas).scalars().all())

    # Etiquetas (N:M) en bloque: 2 a 3 por idea.
    enlaces = []
    for iid in idea_ids:
        for eid in random.sample(etiqueta_ids, k=random.randint(2, 3)):
            enlaces.append({"idea_id": iid, "etiqueta_id": eid})
    db.execute(insert(idea_etiqueta), enlaces)

    # Publicaciones + métricas en bloque para un subconjunto.
    total_pubs = total_metr = 0
    pub_filas = []
    for iid in idea_ids:
        n = (random.randint(1, 2) if con_publicaciones else (1 if random.random() < 0.3 else 0))
        for _ in range(n):
            pub_filas.append({
                "idea_id": iid,
                "red_social": random.choice(REDES),
                "contenido_generado": "Publicación generada de ejemplo. #atlas",
                "tono": random.choice(TONOS),
                "estado": "generada",
                "modelo_ia": "atlas-sim-1",
            })
    if pub_filas:
        pub_ids = list(db.execute(insert(Publicacion).returning(Publicacion.id), pub_filas).scalars().all())
        total_pubs = len(pub_ids)
        metr_filas = []
        for pid in pub_ids:
            for _ in range(random.randint(1, 3)):
                metr_filas.append({
                    "publicacion_id": pid,
                    "fuente": random.choice(["manual", "api"]),
                    "likes": random.randint(0, 800),
                    "comentarios": random.randint(0, 90),
                    "compartidos": random.randint(0, 60),
                    "alcance": random.randint(100, 20000),
                })
        db.execute(insert(MetricaPublicacion).returning(MetricaPublicacion.id), metr_filas)
        total_metr = len(metr_filas)
    return total_pubs, total_metr


def run() -> None:
    ensure_schema()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        reset(db)

        # Etiquetas
        etq_ids = list(
            db.execute(
                insert(Etiqueta).returning(Etiqueta.id),
                [{"nombre": n, "color": "#64748b"} for n in ETIQUETAS],
            ).scalars().all()
        )

        # Usuario demo (con muchas ideas -> N+1 notable)
        demo = Usuario(email="demo@atlas.app", nombre="Andy Toala", password_hash=hash_password("atlas123"))
        demo.perfil_tono = PerfilTono(nombre="cercano", descripcion="Tono cercano y directo")
        db.add(demo)
        db.flush()
        total_ideas = DEMO_IDEAS
        total_pubs, total_metr = crear_ideas(db, demo.id, DEMO_IDEAS, etq_ids, con_publicaciones=True)

        # Otros usuarios (volumen general)
        hash_generico = hash_password("atlas123")
        for u in range(OTROS_USUARIOS):
            usuario = Usuario(
                email=f"user{u + 1}@atlas.app",
                nombre=f"Usuario {u + 1}",
                password_hash=hash_generico,
            )
            usuario.perfil_tono = PerfilTono(nombre=random.choice(TONOS), descripcion="")
            db.add(usuario)
            db.flush()
            p, m = crear_ideas(db, usuario.id, IDEAS_POR_USUARIO, etq_ids, con_publicaciones=False)
            total_ideas += IDEAS_POR_USUARIO
            total_pubs += p
            total_metr += m

        db.commit()
        print("Datos de prueba cargados (volumen real).")
        print("  Usuario demo: demo@atlas.app / atlas123")
        print(f"  Usuarios: {OTROS_USUARIOS + 1}  |  Ideas: {total_ideas}  |  "
              f"Publicaciones: {total_pubs}  |  Metricas: {total_metr}")
        print(f"  Ideas del usuario demo (para el N+1): {DEMO_IDEAS}")
    finally:
        db.close()


if __name__ == "__main__":
    run()
