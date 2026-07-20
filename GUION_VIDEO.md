# Guion del video — Semana 8 (grabando con Swagger)

Vas a grabar usando **Swagger**, que es la documentación real y automática de tu
propio backend (nada de "demo"). Es una página donde cada función del backend se
puede ejecutar de verdad y ver el resultado.

La dirección es: **http://localhost:8000/docs**

En cada respuesta que ejecutes, fíjate en la sección **"Response headers"**: ahí
salen los números que prueban la mejora:
- **x-query-count** = cuántas consultas costó (menos es mejor)
- **x-cache** = HIT (vino del caché) o MISS (se calculó)
- **x-process-time-ms** = milisegundos que tardó

---

## ANTES DE GRABAR (una sola vez, sin cámara)

1. Abre **Docker Desktop** y espera a "Engine running".
2. Abre el navegador en **http://localhost:8000/docs**. Debe verse el título
   **"Atlas Backend API"** con una lista de funciones agrupadas.
3. **Inicia sesión una vez** (esto te autentica para todo el video):
   - Busca **POST /auth/login**, ábrelo, aprieta **"Try it out"**.
   - En el cuadro de texto deja: `{"email": "demo@atlas.app", "password": "atlas123"}`
   - Aprieta **"Execute"**. En la respuesta, **copia** el texto largo que aparece
     en `access_token` (sin las comillas).
   - Arriba a la derecha aprieta el botón **"Authorize"**, pega ese texto, aprieta
     **"Authorize"** y luego **"Close"**. Listo, ya estás autenticado.

Consejo: prueba una vez todos los pasos sin grabar. Cuando te sientas cómodo,
recarga la página (F5), vuelve a autenticarte y ya grabas en serio.

> Cómo ejecutar cualquier función en Swagger (lo harás siempre igual):
> ábrela → "Try it out" → pon el valor si lo pide → "Execute" → mira la respuesta
> y las "Response headers".

---

# GUION (con cámara)

### Parte 1 — Presentación
> "Hola, soy Andy Toala. Voy a mostrar la optimización del backend de mi proyecto
> Atlas, una app que captura ideas y con inteligencia artificial las convierte en
> publicaciones. El backend está hecho en FastAPI, usa una base de datos Postgres y
> Redis, y esto que ven es su documentación real, donde puedo ejecutar cada función."

(Muestra la página http://localhost:8000/docs y menciona que ya iniciaste sesión.)

### Parte 2 — Autenticación (que no repite consultas)
> "Primero la autenticación. Comparo dos formas de saber quién es el usuario."

Ejecuta **GET /auth/me** ("Try it out" → "Execute").
> "La forma optimizada lee la identidad del token, sin ir a la base de datos."

Mira en **Response headers**: **x-query-count: 0**.

Ejecuta **GET /auth/me-db**.
> "La forma antigua consulta la base en cada pedido: cuesta una consulta."

Mira: **x-query-count: 1**.
> "Cero contra una consulta en cada llamada. Así evito consultas redundantes."

### Parte 3 — Consulta N+1 (lo más importante)
> "Ahora el listado de ideas. Aquí está el problema N+1."

Ejecuta **GET /ideas** con el campo **optimized** en **false** → "Execute".
> "Sin optimizar, la base recibe una consulta extra por cada idea. Con mis 50 ideas,
> son más de cien consultas."

Mira: **x-query-count: 101** (y el tiempo).

Ahora cambia **optimized** a **true** → "Execute".
> "Optimizado, con eager loading, bajo a solo 3 consultas, sin importar cuántas ideas
> tenga. De 101 a 3."

Mira: **x-query-count: 3** (y el tiempo, mucho menor).

### Parte 4 — Caché del dashboard
> "El reporte del dashboard suma datos de muchas tablas. Le puse caché."

Ejecuta **GET /dashboard/metricas** (primera vez).
> "La primera vez lo calcula consultando la base."

Mira: **x-cache: MISS** y **x-query-count: 4**.

Ejecútalo **otra vez**.
> "La segunda vez viene del caché: cero consultas y mucho más rápido."

Mira: **x-cache: HIT** y **x-query-count: 0**.

Ejecuta **POST /publicaciones/{pub_id}/metricas** (pon **pub_id = 1**, deja el cuerpo
como está) → "Execute".
> "Cuando cambian los datos, borro el caché para no mostrar información vieja."

Ejecuta **GET /dashboard/metricas** de nuevo.
> "Y vuelve a calcular con datos frescos. Ese es el ciclo completo del caché."

Mira: **x-cache: MISS** otra vez.

### Parte 5 — Trabajo en segundo plano (cola con IA)
Ten a un lado la ventana de **Docker Desktop → atlas_worker → Logs**.
> "Generar la publicación con IA es lento. En vez de hacer esperar al usuario, lo
> mando a un trabajador en segundo plano."

Ejecuta **POST /ideas/{idea_id}/publicar** con **idea_id = 1** y **sync = false**.
> "La respuesta llega al instante, con estado 'queued'. El trabajo se procesa aparte."

(Señala los logs del worker procesando. Copia el `job_id` de la respuesta.)

Ejecuta **GET /jobs/{job_id}** (pega el job_id). Repite una o dos veces.
> "Consultando el estado veo cómo pasa de 'processing' a 'done'."

Ahora ejecuta **POST /ideas/{idea_id}/publicar** con **idea_id = 2** y **sync = true**.
> "En cambio, la forma bloqueante deja esperando al usuario unos tres segundos. Por
> eso conviene la cola de trabajo."

Mira: **x-process-time-ms ≈ 3000**.

### Parte 6 — Mostrar el código (opcional, suma en "explicación")
Si quieres, abre VS Code un momento y nombra un archivo por técnica:
- **app/routers/ideas.py** → "la corrección del N+1 con selectinload"
- **app/routers/dashboard.py** y **app/cache.py** → "el caché con Redis"
- **app/tasks/jobs.py** → "el trabajo en segundo plano"
- **app/deps.py** → "la autenticación que no consulta la base"
- **app/models.py** → "qué relaciones se cargan de una vez y cuáles no"

### Parte 7 — Honestidad, dificultades y cierre
> "Para ser transparente: la base de datos es mi Supabase real, y los datos son de
> verdad, así que la lentitud que vieron no está simulada. Lo único que simulo es el
> texto que escribe la IA, para no depender de una clave de pago; pero la arquitectura
> asíncrona sí es real. La mayor dificultad fue hacer medible el problema, y lo
> resolví con un contador de consultas. En resumen, apliqué caché, corregí el N+1,
> configuré la cola de trabajo, justifiqué la carga de datos y aseguré la
> autenticación sin consultas de más, todo sobre mi proyecto Atlas. Dejo el enlace del
> repositorio. Gracias."

---

## Números que vas a ver (para no confundirte)
- Autenticación: **0** (optimizada) contra **1** consulta (antigua).
- Ideas: **101** consultas (sin optimizar) contra **3** (optimizado).
- Dashboard: **MISS** con 4 consultas (calcula) contra **HIT** con 0 consultas (caché).
- Publicar: en segundo plano responde **al instante**; bloqueando tarda **~3000 ms**.
> Los milisegundos pueden variar; los conteos de consultas son fijos.

## Antes de subir a Moodle
- [ ] El video quedó con permisos para que cualquiera con el enlace lo vea.
- [ ] Probaste el enlace del video en una ventana de incógnito.
- [ ] Adjuntaste también el enlace del repositorio si el docente lo pide.

> Nota: si en algún momento Swagger se te complica, tienes la página de botones de
> respaldo en http://localhost:8000/demo (hace exactamente lo mismo, más simple).
