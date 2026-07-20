# Atlas — Backend optimizado (Taller Semana 8)

Backend del proyecto integrador **Atlas** (asignatura *Aplicaciones Móviles*, UEA).
Atlas es una app Flutter que captura ideas y, con IA, las transforma en
publicaciones para redes sociales. Este servicio es la **API/BFF** que la app
consume, construida en **FastAPI + SQLAlchemy** sobre **Postgres** (la base de
datos modelada en la Semana 4) y **Redis**.

El objetivo del taller es aplicar y demostrar, sobre este backend real, cinco
técnicas de optimización: **caché-aside**, **corrección de N+1**, **cola de
trabajo asíncrona**, **lazy/eager loading justificado** y **autenticación sin
consultas redundantes**, comparando el comportamiento antes y después.

---

## 1. Stack y arquitectura

| Componente | Tecnología | Rol |
|---|---|---|
| API | FastAPI (uvicorn) | Endpoints REST, autenticación JWT |
| ORM | SQLAlchemy 2.0 | Modelos, relaciones, lazy/eager |
| Base de datos | PostgreSQL 16 | Persistencia (modelo Semana 4) |
| Caché / Broker | Redis 7 | Caché-aside + cola de trabajo |
| Worker | Celery 5 | Procesa la generación con IA fuera del request |

```
App Flutter  ──HTTP/JWT──►  FastAPI  ──►  Postgres
                              │  ▲
                     encola   │  │ caché-aside
                              ▼  │
                            Redis ◄── Celery worker (genera la publicación con IA)
```

---

## 2. Cómo ejecutar (todo con Docker)

Requisitos: Docker Desktop.

```bash
# 1. Levantar db, redis, api y worker
docker compose up -d --build

# 2. Cargar datos de prueba (usuario demo@atlas.app / atlas123, 8 ideas)
docker compose exec api python seed.py

# 3. Probar
#    - Swagger:  http://localhost:8000/docs
#    - Postman:  importar Atlas.postman_collection.json
#    - VS Code:  abrir pruebas/pruebas.http (extensión REST Client)

# Ver el worker procesando en vivo:
docker compose logs -f worker

# Apagar todo:
docker compose down          # (agregar -v para borrar también los datos)
```

> **Ejecución local (sin Docker para la API):** requiere Python 3.11/3.12. Levanta
> solo la infraestructura con `docker compose up -d db redis`, crea un entorno con
> `pip install -r requirements.txt`, copia `.env.example` a `.env` y ejecuta
> `uvicorn app.main:app --reload`. El worker en Windows necesita el pool *solo*:
> `celery -A app.tasks.celery_app.celery_app worker --pool=solo -l info`.

---

## 3. Diagnóstico (antes de optimizar)

Dos puntos críticos detectados en el backend:

1. **Operación costosa — dashboard de métricas.** El reporte del usuario agrega
   datos de `idea`, `publicacion` y `metrica_publicacion`. Es caro y se pide con
   frecuencia (cada vez que se abre la pantalla de analítica) → candidato a caché.
2. **Consulta con riesgo de N+1 — listado de ideas.** Al listar las ideas y
   mostrar sus etiquetas y su número de publicaciones, el ORM lanza, por cada
   idea, una consulta extra para las etiquetas y otra para las publicaciones.
   Con *N* ideas se ejecutan **1 + 2N** consultas.

Para hacer el costo **visible** el backend cuenta las consultas SQL por request
(incluidas las cargas perezosas) y las devuelve en la cabecera `X-Query-Count`,
junto con `X-Process-Time-ms` y `X-Cache`. Ver [app/database.py](app/database.py).

---

## 4. Técnicas aplicadas

### 4.1 Caché-aside con TTL e invalidación explícita
Archivos: [app/cache.py](app/cache.py) · [app/routers/dashboard.py](app/routers/dashboard.py)

- **Lectura:** el endpoint pregunta primero a Redis. Si hay dato (**HIT**) responde
  sin tocar la base; si no (**MISS**) consulta, guarda en Redis con **TTL de 60 s**
  (`CACHE_TTL_SECONDS`) y responde.
- **Invalidación explícita:** al registrar una métrica
  ([app/routers/publicaciones.py](app/routers/publicaciones.py)) o al generar una
  publicación en el worker, se borra la clave `atlas:dashboard:user:{id}`, de modo
  que el siguiente request recalcule con datos frescos.

### 4.2 Corrección de la consulta N+1 (eager loading)
Archivo: [app/routers/ideas.py](app/routers/ideas.py)

El endpoint `GET /ideas` acepta `?optimized`:
- `false`: comportamiento ingenuo → **1 + 2N** consultas (lazy-load por idea).
- `true`: `selectinload(Idea.etiquetas)` y `selectinload(Idea.publicaciones)`
  precargan las relaciones en **2 consultas constantes** → total **3**, sin
  importar cuántas ideas haya.

### 4.3 Cola de trabajo asíncrona (worker)
Archivos: [app/tasks/celery_app.py](app/tasks/celery_app.py) · [app/tasks/jobs.py](app/tasks/jobs.py) · [app/routers/publicaciones.py](app/routers/publicaciones.py)

Generar la publicación con IA es lento (~3 s). En vez de bloquear el request,
`POST /ideas/{id}/publicar` crea un `Job`, lo **encola en Celery** y responde al
instante con `job_id` y estado `queued`. El **worker** procesa en segundo plano
(`queued → processing → done`) y el cliente consulta el avance en `GET /jobs/{id}`.
El parámetro `?sync=true` fuerza el modo bloqueante **solo para comparar tiempos**.

### 4.4 Lazy vs eager loading justificado
Archivo: [app/models.py](app/models.py)

| Relación | Estrategia | Justificación |
|---|---|---|
| `Usuario.ideas` | **lazy** | Al autenticar solo se necesita identidad, no todas las ideas: cargarlas siempre sería traer datos inútiles. |
| `Usuario.perfil_tono` | **eager (joined)** | Es un único registro pequeño que el worker de IA necesita siempre; se trae junto al usuario y evita un viaje extra. |
| `Idea.etiquetas` / `Idea.publicaciones` | **lazy por defecto, eager en el listado** | Lazy evita costo en endpoints que no las usan; el listado las pide explícitamente con `selectinload` (ver 4.2). |

### 4.5 Autenticación sin consultas redundantes
Archivos: [app/security.py](app/security.py) · [app/deps.py](app/deps.py) · [app/routers/auth.py](app/routers/auth.py)

- Contraseñas con **bcrypt**; login emite un **JWT** que lleva la identidad
  (`sub`, `email`, `nombre`) en los *claims*.
- La dependencia `get_current_user` reconstruye la identidad **desde el token**,
  sin consultar la base: las rutas protegidas **no** hacen una consulta de usuario
  por request. La versión ingenua `get_current_user_db` (endpoint `/auth/me-db`)
  se conserva solo para comparar.

---

## 5. Resultados medidos (antes → después)

Medidos con los 8 registros de la semilla (varían según la máquina y el volumen
de datos; la brecha **crece** con más datos).

| Operación | Antes | Después | Mejora |
|---|---|---|---|
| Listar ideas (N+1) | 17 consultas · 29.4 ms | **3 consultas · 17.3 ms** | −82 % consultas |
| Validar usuario (auth) | 1 consulta / request | **0 consultas / request** | elimina la consulta |
| Dashboard (caché) | MISS: 4 consultas · 380.7 ms | **HIT: 0 consultas · 2.8 ms** | ~135× más rápido |
| Generar publicación | Síncrono: 3174 ms bloqueado | **Asíncrono: responde y encola** | request no bloqueado |

Las cabeceras `X-Query-Count`, `X-Cache` y `X-Process-Time-ms` permiten reproducir
estos números desde Postman o `pruebas/pruebas.http`.

---

## 6. Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/auth/register` | Registrar usuario (devuelve JWT) |
| POST | `/auth/login` | Iniciar sesión (devuelve JWT) |
| GET | `/auth/me` | Perfil desde el JWT (0 consultas) |
| GET | `/auth/me-db` | Perfil consultando la base (comparación) |
| GET | `/ideas?optimized=` | Listar ideas (N+1 vs eager) |
| POST | `/ideas` | Crear idea con etiquetas |
| GET | `/ideas/{id}` | Obtener una idea |
| POST | `/ideas/{id}/publicar?sync=` | Generar publicación (async / sync) |
| GET | `/jobs/{id}` | Estado del trabajo encolado |
| POST | `/publicaciones/{id}/metricas` | Registrar métrica (invalida caché) |
| GET | `/dashboard/metricas` | Reporte analítico (caché-aside) |

---

## 7. Estructura del proyecto

```
atlas-backend/
├─ app/
│  ├─ main.py            # App FastAPI + middleware de tiempo
│  ├─ config.py          # Configuración por variables de entorno
│  ├─ database.py        # Engine, sesión y contador de consultas
│  ├─ models.py          # Modelos ORM (lazy/eager justificado)
│  ├─ schemas.py         # Esquemas Pydantic
│  ├─ security.py        # bcrypt + JWT
│  ├─ cache.py           # Caché-aside sobre Redis
│  ├─ deps.py            # Sesión de BD + usuario autenticado
│  ├─ routers/           # auth, ideas, publicaciones, dashboard
│  └─ tasks/             # Celery (celery_app.py, jobs.py)
├─ seed.py               # Datos de prueba
├─ docker-compose.yml    # db + redis + api + worker
├─ Dockerfile
├─ requirements.txt
├─ Atlas.postman_collection.json
└─ pruebas/pruebas.http
```

---

## 8. Mapeo con los criterios de evaluación

| Criterio (rúbrica) | Dónde se cumple |
|---|---|
| Caché y corrección de N+1 (2.5) | §4.1 dashboard cache-aside · §4.2 `/ideas?optimized` |
| Autenticación (1.5) | §4.5 JWT sin consultas redundantes (`/auth/me` vs `/auth/me-db`) |
| Cola de trabajo y carga de datos (2.0) | §4.3 Celery worker · §4.4 lazy/eager justificado |
| Funcionamiento y rendimiento (1.5) | §5 tabla antes/después + cabeceras + Postman |
| Calidad del código (0.5) | Módulos separados por responsabilidad, comentado |
| Relación con el proyecto (0.5) | Dominio real de Atlas (idea→publicación con IA) |
