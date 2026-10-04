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

> **Actualización (Semana 13).** El **CRUD de ideas queda completo**: a la lectura
> y la creación se suman `PATCH /ideas/{id}` y `DELETE /ideas/{id}`, que la app
> Flutter ya consume. Con ello entra una **sexta técnica de optimización**,
> el **control del tamaño de la respuesta** (§4.6), y las **tres escrituras de
> ideas** invalidan la clave de caché del dashboard (§4.1). Se cerraron además dos
> fallos de autorización por objeto (IDOR) en `/jobs/{id}` y en
> `/publicaciones/{id}/metricas`: el dueño se filtra **dentro del `WHERE`** y el
> recurso ajeno responde `404`, sin revelar que existe.

> **Actualización (Semana 15): IA real.** El worker ya no simula la redacción:
> llama a un **modelo de lenguaje a través de [OpenRouter](https://openrouter.ai)**
> (§4.7). `POST /ideas/{id}/publicar` acepta la red de destino
> (`instagram`, `linkedin` o `x`) y el nuevo `GET /ideas/{id}/publicaciones`
> devuelve los textos generados para que la app los muestre. Si el trabajo falla
> (key inválida, sin saldo, límite de peticiones), el job queda en `error` con un
> mensaje legible y la idea **vuelve a su estado anterior** en vez de quedarse en
> `procesando` para siempre. El módulo de IA tiene pruebas unitarias propias (§4.8).

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
  ([app/routers/publicaciones.py](app/routers/publicaciones.py)), al generar una
  publicación en el worker y en las **tres escrituras de ideas** —`POST /ideas`,
  `PATCH /ideas/{id}` y `DELETE /ideas/{id}`
  ([app/routers/ideas.py](app/routers/ideas.py))— se borra la clave
  `atlas:dashboard:user:{id}`, de modo que el siguiente request recalcule con
  datos frescos. Las escrituras de ideas se añadieron porque el reporte incluye
  `total_ideas`: sin invalidar, el panel seguía mostrando el conteo anterior
  hasta que venciera el TTL.

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

### 4.7 Generación con IA real (OpenRouter)
Archivos: [app/ia.py](app/ia.py) · [app/tasks/jobs.py](app/tasks/jobs.py) · [.env.example](.env.example)

El worker arma un *prompt* con el título y el contenido de la idea, el **tono del
perfil** del usuario (`cercano`, `profesional`, `inspirador`) y las reglas de
formato de la red elegida (longitud, hashtags y emojis; en X se garantiza el
límite de 280 caracteres). Luego llama a `https://openrouter.ai/api/v1/chat/completions`,
una API compatible con la de OpenAI, así que **cambiar de modelo es cambiar una
variable de entorno**, no el código.

| Variable (`.env`) | Uso |
|---|---|
| `OPENROUTER_API_KEY` | Key de https://openrouter.ai/keys. **Vive solo en el servidor**: la app nunca la recibe, así que no se puede extraer de la APK. Vacía = generador simulado (`atlas-sim-1`). |
| `OPENROUTER_MODEL` | Modelo del catálogo (por defecto `anthropic/claude-haiku-4.5`). Los terminados en `:free` no consumen saldo. |

Cada publicación guarda en `modelo_ia` qué modelo la escribió, así que en la base
queda la trazabilidad entre texto real y simulado. La llamada usa `urllib` de la
librería estándar: no se añadió ninguna dependencia.

Tras poner la key en `.env`: `docker compose up -d --force-recreate api worker`.

### 4.8 Pruebas unitarias del módulo de IA

```powershell
py -m unittest discover -s tests -t . -v      # o: python -m ...
```

Siete pruebas en [tests/test_ia.py](tests/test_ia.py), sin base de datos, sin Redis
y sin red (OpenRouter se sustituye con `unittest.mock`): el *prompt* lleva tono y
red, X nunca supera 280 caracteres, sin key no se toca la red, una respuesta vacía
es un error y no una publicación vacía, y un `402` se traduce a "sin saldo".

### 4.9 Paginación, búsqueda y ordenamiento del listado
Archivo: [app/routers/ideas.py](app/routers/ideas.py)

`GET /ideas` acepta `limit` (1–100, por defecto 20), `offset`, `q` (busca en título
y contenido sin distinguir mayúsculas; los comodines `%` y `_` se escapan) y
`estado`. El orden es `creado_en DESC, id DESC`: el `id` desempata filas creadas en
el mismo instante, sin lo cual una idea podría repetirse o perderse entre páginas.

El total de coincidencias viaja en la cabecera **`X-Total-Count`** y se obtiene con
`COUNT(*) OVER ()` **en la misma consulta** (la función de ventana se evalúa antes
del `LIMIT`): paginar no añade un viaje a la base y el listado optimizado sigue en
**3 consultas**.

### 4.10 Métricas como fotos en el tiempo
Archivos: [app/routers/publicaciones.py](app/routers/publicaciones.py) · [app/routers/dashboard.py](app/routers/dashboard.py)

Cada fila de `metrica_publicacion` es una **foto** del rendimiento en un momento
dado, no un incremento. Así se puede seguir la evolución de una publicación
(`GET /publicaciones/{id}/metricas`), pero el panel **no** puede sumar todas las
filas: una publicación medida con 100 likes el lunes y 250 el viernes tiene 250, no
350. El dashboard suma solo el registro más reciente de cada publicación (subconsulta
`MAX(id) ... GROUP BY publicacion_id`), sin consultas adicionales.
`GET /ideas/{id}/publicaciones` incluye `ultima_metrica` y `num_metricas` de cada
publicación con un `selectinload` (una consulta extra, no una por publicación), y
`MetricaCreate` rechaza valores negativos con `422`.

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

### 4.6 Control del tamaño de la respuesta (listado ligero vs. detalle)
Archivos: [app/schemas.py](app/schemas.py) · [app/routers/ideas.py](app/routers/ideas.py)

`contenido` es el campo pesado de una idea (columna `Text`: el texto completo que
escribió el usuario) y **el listado no lo necesita**, porque la pantalla de ideas
solo muestra título, estado, etiquetas y número de publicaciones. Devolverlo en la
lista sería transferir N textos largos que nadie lee: carga innecesaria de datos.
Por eso el recurso se modela con **dos esquemas** en lugar de uno:

| Esquema | Campos | Se usa en |
|---|---|---|
| `IdeaOut` | `id`, `titulo`, `estado`, `origen`, `etiquetas`, `num_publicaciones`, `creado_en` | `GET /ideas` |
| `IdeaDetalleOut(IdeaOut)` | los anteriores **+ `contenido`** | `GET /ideas/{id}`, `POST /ideas`, `PATCH /ideas/{id}` |

El detalle sí lo trae, que es cuando el usuario abre una idea concreta y el texto
es justamente lo que quiere leer. `DELETE /ideas/{id}` lleva la misma idea al
extremo: responde **204 sin cuerpo**, porque no hay nada útil que devolver.

En el cliente Flutter esta decisión se refleja en que `Idea.contenido` es
**nulable**: llega `null` desde el listado y con valor desde el detalle.

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

La columna **Token** indica si la ruta exige la cabecera
`Authorization: Bearer <jwt>`. Sin ella, o con un token manipulado, responden
**401**. Las rutas de ideas, jobs y publicaciones filtran además por el usuario del
token dentro del propio `WHERE`: si el recurso es de otro dueño la respuesta es
**404**, no 403, para no revelar que existe.

| Método | Ruta | Descripción | Token |
|---|---|---|:---:|
| GET | `/health` | Estado del servicio | — |
| POST | `/auth/register` | Registrar usuario (devuelve JWT) | — |
| POST | `/auth/login` | Iniciar sesión (devuelve JWT) | — |
| GET | `/auth/me` | Perfil desde el JWT (0 consultas) | ✔ |
| GET | `/auth/me-db` | Perfil consultando la base (comparación) | ✔ |
| GET | `/ideas?optimized=&limit=&offset=&q=&estado=` | Listar ideas del usuario, sin `contenido`, **paginado** (20 por defecto, máx. 100) y con búsqueda; total en `X-Total-Count` (N+1 vs eager con `optimized`) | ✔ |
| POST | `/ideas` | Crear idea con etiquetas → `201` con el detalle | ✔ |
| GET | `/ideas/{id}` | Obtener una idea con su `contenido` | ✔ |
| PATCH | `/ideas/{id}` | Editar parcialmente título, contenido, origen o etiquetas; devuelve el detalle actualizado (`400` si el cuerpo va vacío) | ✔ |
| DELETE | `/ideas/{id}` | Eliminar la idea y, con ella, sus publicaciones, métricas, jobs y filas de `idea_etiqueta` (las etiquetas son catálogo compartido y se conservan) → `204` sin cuerpo | ✔ |
| POST | `/ideas/{id}/publicar?sync=` | Generar publicación con IA (async → `202` / sync). Cuerpo opcional `{"red_social": "instagram\|linkedin\|x"}`; `422` si la red no es válida | ✔ |
| GET | `/jobs/{id}` | Estado del trabajo encolado (solo jobs propios): `queued`, `processing`, `done` con `resultado_publicacion_id`, o `error` con el motivo | ✔ |
| GET | `/ideas/{id}/publicaciones` | Publicaciones generadas para la idea, la más reciente primero (texto, red, tono, modelo de IA, `ultima_metrica`, `num_metricas`) | ✔ |
| GET | `/publicaciones/{id}/metricas` | Historial de rendimiento de una publicación propia, del registro más antiguo al último | ✔ |
| POST | `/publicaciones/{id}/metricas` | Registrar una métrica (foto del rendimiento) en una publicación propia; `422` si hay valores negativos; invalida caché | ✔ |
| GET | `/dashboard/metricas` | Reporte analítico (caché-aside) | ✔ |

Las tres escrituras de `/ideas` (`POST`, `PATCH`, `DELETE`) invalidan la clave del
dashboard del usuario, porque el reporte cuenta ideas (§4.1).

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
│  ├─ ia.py              # Generación con IA (OpenRouter) + respaldo simulado
│  ├─ deps.py            # Sesión de BD + usuario autenticado
│  ├─ routers/           # auth, ideas, publicaciones, dashboard
│  └─ tasks/             # Celery (celery_app.py, jobs.py)
├─ tests/                # Pruebas unitarias (python -m unittest)
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
