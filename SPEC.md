# TriageBot — Especificación funcional completa (referencia, Equipo B)

> **Documento de trabajo del Equipo B.** No está en el repo: lo recibís a mitad
> del Lab 1 como referencia. Vosotros habéis empezado a escribir vuestra spec a
> partir de `BRIEF.md`; esto es el patrón con el que **reconciliarla**, no un
> reemplazo. Comparad la vuestra con esta, adoptad lo que mejore vuestra versión
> y conservad lo vuestro que ya esté bien. Las decisiones que toméis distintas a
> esta, apuntadlas en la sección 12.

---

## 1. Objetivo

TriageBot es una aplicación web interna donde un usuario crea **tickets**
(incidencias descritas en lenguaje natural) y el sistema, vía LLM, los
**clasifica automáticamente** por categoría, prioridad y tags. El usuario puede
consultar, filtrar y actualizar los tickets desde un tablero web.

El proyecto debe estar **funcionando end-to-end** al final del Día 2: backend,
base de datos, integración con LLM, frontend mínimo, tests verdes y CI verde en
GitHub Actions.

**Usuario:** el equipo de soporte de Marta Iturri. No hay login: es una
herramienta interna de un solo espacio compartido.

---

## 2. Stack técnico (innegociable)

| Capa | Tecnología |
|------|------------|
| Lenguaje | Python 3.11+ |
| Framework web | FastAPI + uvicorn |
| Modelos / validación | Pydantic (v2) |
| Persistencia | SQLite con el módulo estándar `sqlite3` (archivo local `triagebot.db`). **Sin ORM**: nada de SQLModel ni SQLAlchemy |
| Plantillas | Jinja2 (`templates/`) |
| Frontend | HTMX + Tailwind CSS por CDN (sin build tools) |
| LLM | `openai/gpt-oss-120b` vía OpenRouter, con el SDK de OpenAI (`openai`) |
| Configuración | `.env` cargado con `python-dotenv` |
| Tests | pytest + `fastapi.testclient.TestClient` |
| Lint | ruff |
| CI | GitHub Actions (`.github/workflows/ci.yml`, incluido en el repo) |

No se permite cambiar de framework ni de base de datos, añadir un ORM ni build
tools (webpack, vite…). Todas las dependencias van **fijadas** en
`requirements.txt` (`paquete==versión`); no se añade ninguna sin acordarlo en el
equipo y anotarlo en la sección 12.

---

## 3. Arquitectura y estructura de módulos

```
app/
├── __init__.py
├── main.py          # FastAPI: app, endpoints y render de plantillas. Nada de SQL ni de LLM aquí.
├── models.py        # Modelos Pydantic (entrada, salida) y enums.
├── db.py            # Toda la persistencia con sqlite3: init, insert, get, list, update.
└── classifier.py    # La ÚNICA llamada al LLM. Expone classify_ticket().
templates/
├── index.html       # Página completa (GET /)
└── _rows.html       # Fragmento: filas de la tabla (lo reutilizan GET / y las respuestas HTMX)
tests/
└── test_acceptance.py   # Obligatorio. NO se modifica.
```

Reglas de dependencia (quién puede importar a quién):

- `main.py` → `models`, `db`, `classifier`.
- `db.py` → `models`. Nunca importa FastAPI ni el clasificador.
- `classifier.py` → `openai`, `models`. **Nunca importa FastAPI** (si veis
  `from fastapi import …` en `classifier.py`, algo se ha torcido).

**Cómo llama `main.py` al clasificador (importante para los tests):** los tests
sustituyen `app.classifier.classify_ticket` con `monkeypatch`. Para que esa
sustitución surta efecto, `main.py` debe llamarlo **a través del módulo**:

```python
from app import classifier          # ✅
...
result = classifier.classify_ticket(title, description)
```

y **no** con `from app.classifier import classify_ticket` (❌: guarda una
referencia a la función original y el mock no se aplica, con lo que los tests
llamarían al LLM de verdad).

---

## 4. Modelo de datos

Una sola entidad: `Ticket`.

| Campo | Tipo | Notas |
|-------|------|-------|
| `id` | int | Primary key, autoincremental |
| `title` | str | Obligatorio. Se aplica `strip()`; tras él, longitud 1–200 |
| `description` | str | Obligatorio. Se aplica `strip()`; tras él, longitud 1–5000 |
| `category` | str | Uno de: `bug`, `feature_request`, `question`, `urgent` |
| `priority` | str | Uno de: `P1`, `P2`, `P3` |
| `tags` | list[str] | Lista (puede estar vacía). Máx. 5 tags, cada uno máx. 30 chars, en minúscula y sin duplicados |
| `status` | str | Uno de: `open`, `in_progress`, `closed`. Default: `open` |
| `created_at` | datetime | UTC, generado en servidor |
| `updated_at` | datetime | UTC. Igual a `created_at` al crear; se actualiza en cada `PATCH` |

`category`, `priority` y `tags` los rellena el clasificador (sección 6) en el
momento de crear el ticket.

> **Los enums son vinculantes.** Si vuestro código devuelve `"URGENT"` en
> mayúsculas o un valor fuera de la lista, los tests fallan.

### Esquema SQLite

```sql
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    priority    TEXT    NOT NULL,
    tags        TEXT    NOT NULL DEFAULT '[]',   -- lista serializada con json.dumps
    status      TEXT    NOT NULL DEFAULT 'open',
    created_at  TEXT    NOT NULL,               -- ISO 8601 UTC, p. ej. 2026-06-29T09:30:00Z
    updated_at  TEXT    NOT NULL
);
```

- La tabla se crea al arrancar la app (`db.init_db()` en el arranque de FastAPI).
- La ruta del archivo se lee de la variable de entorno `TRIAGEBOT_DB`, con
  `triagebot.db` por defecto. Así los tests pueden usar una base temporal.
- **Todas** las consultas usan parámetros (`?`), nunca f-strings ni
  concatenación de SQL.
- Una conexión por operación (abrir, ejecutar, `commit`, cerrar). Nada de
  conexiones globales compartidas entre hilos.

---

## 5. Endpoints HTTP

Cinco endpoints. Todos viven en `app.main`. Los de `/tickets` devuelven **JSON**
por defecto; si la petición viene de HTMX (cabecera `HX-Request: true`),
devuelven el **fragmento HTML** `_rows.html` para refrescar la tabla. Los tests
usan siempre JSON.

### `POST /tickets`

Crea un ticket nuevo y lo clasifica. **La clasificación ocurre síncronamente
durante la petición**: la respuesta ya trae el ticket clasificado.

**Request body** (JSON):
```json
{
  "title": "La página de login no carga",
  "description": "Al pulsar el botón de login, sale un error 500 del servidor."
}
```

**Response `201 Created`**:
```json
{
  "id": 1,
  "title": "La página de login no carga",
  "description": "Al pulsar el botón de login, sale un error 500 del servidor.",
  "category": "bug",
  "priority": "P1",
  "tags": ["login", "error_500", "backend"],
  "status": "open",
  "created_at": "2026-06-29T09:30:00Z",
  "updated_at": "2026-06-29T09:30:00Z"
}
```

**Errores**:
- `422 Unprocessable Entity` si `title` o `description` faltan, son vacíos o
  solo espacios, o exceden su longitud máxima. Se usa el formato estándar de
  FastAPI (`{"detail": [...]}`).

**Orden de operaciones:** validar → clasificar → guardar → responder. Si la
validación falla, **no se llama al LLM** (no se gastan tokens en basura).

> El `POST /tickets` **nunca** devuelve `5xx` por un fallo del LLM. Si el LLM
> falla, el clasificador aplica el fallback (sección 6) y el endpoint devuelve
> `201` igualmente.

**Duplicados:** dos `POST` idénticos seguidos crean **dos** tickets. Es una
decisión consciente (pueden ser dos clientes con el mismo problema). No se
deduplica.

### `GET /tickets`

Devuelve la lista de tickets, ordenada por `created_at` descendente (y por `id`
descendente a igualdad).

**Query params opcionales** (combinables, filtran por igualdad exacta):
`category`, `priority`, `status` (igualdad exacta) y `title` (subcadena, sin
distinguir mayúsculas; ver sección 7).

```text
GET /tickets?category=bug&priority=P1&status=open&title=login
```

- **Response `200 OK`**: lista JSON de tickets (lista vacía `[]` si no hay).
- **Response `422`**: si un filtro trae un valor fuera de su enum
  (p. ej. `?priority=P9`).

### `GET /tickets/{id}`

- **Response `200 OK`**: ticket JSON.
- **Response `404 Not Found`**: si no existe → `{"detail": "Ticket not found"}`.
- **Response `422`**: si el id no es un entero (`/tickets/abc`). Lo hace FastAPI
  solo al tipar el parámetro como `int`.

### `PATCH /tickets/{id}`

Actualiza **solo** `status` y/o `priority`. El resto de campos son inmutables
tras la creación.

**Request body** (cualquiera de los dos campos, o ambos):
```json
{ "status": "in_progress", "priority": "P2" }
```

- **Response `200 OK`**: ticket actualizado, con `updated_at` renovado.
- **Response `404 Not Found`**: si no existe.
- **Response `422`**: si algún valor no pertenece al enum permitido, si el body
  viene vacío (`{}`) o si trae campos no permitidos (`title`, `category`…).

### `GET /`

Renderiza `index.html` con el tablero (sección 7). **Devuelve HTML, no JSON**
— no confundir con `GET /tickets`.

---

## 6. El módulo clasificador (`app/classifier.py`)

Encapsula toda la lógica de IA. **Es el único módulo que llama al LLM.** El
resto del código depende solo de su contrato.

### Contrato público

```python
def classify_ticket(title: str, description: str) -> dict:
    """
    Clasifica un ticket usando el LLM. 

    Devuelve SIEMPRE un dict con esta forma exacta (nunca None, nunca excepción):
    {
        "category": "bug" | "feature_request" | "question" | "urgent",
        "priority": "P1" | "P2" | "P3",
        "tags": list[str]  # máx. 5 elementos, cada uno máx. 30 chars
    }
    """
```

### Llamada al modelo

- SDK de OpenAI apuntando a OpenRouter:
  `base_url="https://openrouter.ai/api/v1"`, `api_key` leída de
  `OPENROUTER_API_KEY`.
- Modelo: `openai/gpt-oss-120b` (formato `proveedor/modelo`, **sin** prefijo
  `openrouter/`), definido como constante al principio del módulo.
- La key se lee **dentro de la función** (o al crear el cliente bajo demanda),
  **no** al importar el módulo: en CI no hay key y el import no puede romper.
  Si no hay key → fallback directamente, sin llamar.
- `.env` se carga con `load_dotenv()` en `app/main.py`, antes de cualquier uso.
- Parámetros: `temperature=0`, `max_tokens=1024`, `timeout=20` segundos.
- La respuesta está en `response.choices[0].message.content`.

### Prompt

Mensaje `system` (punto de partida; podéis mejorarlo):

> Eres un sistema de clasificación de tickets de soporte técnico. Recibirás el
> título y la descripción de un ticket. Devuelve EXCLUSIVAMENTE un objeto JSON
> válido con tres campos: `category` (uno de: bug, feature_request, question,
> urgent), `priority` (uno de: P1, P2, P3) y `tags` (lista de máx. 5 strings
> cortos en minúscula). Sin markdown, sin explicaciones, sin texto antes ni
> después. No inventes categorías.
> Criterios: `urgent` = servicio caído o bloqueo total de un cliente;
> `bug` = algo no funciona como debería; `feature_request` = petición de algo
> nuevo o mejora; `question` = duda de uso. P1 = hay que atenderlo hoy;
> P2 = importante, mañana; P3 = puede esperar.

Mensaje `user`: `Título: {title}\nDescripción: {description}`.

### Parseo y validación de la salida

1. Si el texto viene envuelto en ```` ```json … ``` ````, quitar el envoltorio.
2. Extraer el primer objeto `{…}` del texto y parsearlo con `json.loads`.
3. Validar con un modelo Pydantic:
   - `category` y `priority` deben estar **exactamente** en sus enums
     (`"URGENT"` o `"Bug"` → inválido; no se "arreglan" a mano).
   - `tags`: si falta → `[]`. Si no es lista → inválido. Se normalizan: minúscula,
     `strip()`, recorte a 30 chars, sin vacíos ni duplicados, máximo 5.
4. Si cualquier paso falla, la respuesta se considera **inválida**.

### Reintento y fallback

- Si la llamada lanza excepción (timeout, 5xx, 429, error de autenticación) **o**
  la respuesta es inválida → **un** reintento. No más: se gastan tokens y
  latencia sin garantía.
- Si el reintento también falla → **fallback**:

```python
{"category": "question", "priority": "P3", "tags": []}
```

- Cada fallback se registra con `logging.warning` indicando la causa (excepción
  o salida inválida). **Nunca** se registra la API key.
- Nunca se propaga una excepción del SDK al endpoint.

---

## 7. Frontend mínimo

Una sola página HTML servida en `GET /` con `templates/index.html`. Debe
contener:

1. **Formulario** con dos campos (`title`, `description`) y un botón "Crear
   ticket".
   - Envío con HTMX: `hx-post="/tickets"`, `hx-ext="json-enc"` (la extensión
     json-enc de HTMX, por CDN, para enviar JSON y reutilizar el mismo
     endpoint que usan los tests), `hx-target` = cuerpo de la tabla.
   - El botón se desactiva mientras se clasifica (`hx-disabled-elt`) y se
     muestra un indicador "Clasificando…" (`hx-indicator`): la llamada al LLM
     tarda unos segundos.
   - Tras crear, el formulario se limpia.
   - Si el backend devuelve `422`, se muestra un mensaje legible junto al
     formulario, no un JSON crudo.
2. **Tablero** con la lista de tickets, mostrando: `id`, `title`, `category`
   (color según valor), `priority` (badge: P1 rojo, P2 ámbar, P3 gris), `tags`
   (chips), `status`, `created_at` (formato `dd/mm/aaaa hh:mm`).
3. **Filtros**: tres `<select>` (`category` / `priority` / `status`), cada uno
   con una opción "Todos". Al cambiar cualquiera, HTMX hace
   `GET /tickets?…` con los tres valores (`hx-include`) y reemplaza solo las
   filas.
4. **Cambio de estado** desde la tabla: un `<select>` de `status` por fila que
   hace `PATCH /tickets/{id}` y refresca la fila.
5. **Estado vacío**: si no hay tickets (o el filtro no devuelve nada), una fila
   con "No hay tickets".

Reglas:

- Todo el HTML vive en `templates/`. Nada de HTML grande como string en
  `main.py`.
- Jinja2 con **autoescape activado** (lo está por defecto con
  `Jinja2Templates`). Nunca usar `|safe` con datos del usuario: un ticket con
  `<script>` en la descripción se muestra como texto.
- No se exige diseño espectacular: la gracia es que sea funcional.

Filtros:
La tabla se podrá filtrar por
- categoria
- prioridad
- estado
- titulo: aplicando un filtro de tipo LIKE que ignore mayúsculas y minúsculas. El filtro será LIKE '%texto%'

---

## 8. Configuración y secretos

- `.env` (no se commitea) con `OPENROUTER_API_KEY=sk-or-...`.
- `.env.example` (sí se commitea) con la misma variable sin valor real.
- Variables opcionales: `TRIAGEBOT_DB` (ruta de la base de datos).
- Arranque: `uvicorn app.main:app --reload` → `http://localhost:8000`.

---

## 9. Tests

### 9.1 Tests de aceptación obligatorios

Están en `tests/test_acceptance.py`. **Este fichero no se modifica.** Son cinco
y todos deben estar verdes al final del Día 2:

| # | Test | Verifica |
|---|------|----------|
| 1 | `test_post_ticket_creates_ticket_with_classification` | `POST /tickets` devuelve `201` con el ticket clasificado (category, priority y tags válidos). |
| 2 | `test_created_ticket_is_persisted_and_listed` | El ticket creado se guarda y aparece en `GET /tickets`. |
| 3 | `test_post_ticket_rejects_invalid_input` | `POST /tickets` con entrada inválida devuelve `422`. |
| 4 | `test_classifier_failure_uses_safe_fallback` | Si el clasificador falla, el ticket se crea igualmente con el fallback. |
| 5 | `test_update_ticket_and_filter_by_status_priority_category` | `PATCH /tickets/{id}` actualiza el ticket y `GET /tickets` filtra por status, priority y category. |

Los tests sustituyen el clasificador con `monkeypatch` para no consumir tokens
en CI (de ahí la regla de importación de la sección 3). **Leed el fichero
entero antes de programar**: los detalles exactos (códigos, campos, mensajes)
los fijan sus `assert`.

### 9.2 Tests propios recomendados (`tests/test_classifier.py`, `tests/test_api.py`)

- El clasificador con respuesta válida del LLM (mockeada) devuelve el dict
  correcto.
- Respuesta envuelta en ```` ```json ```` → se parsea bien.
- `category: "URGENT"` o inventada → fallback.
- El SDK lanza excepción dos veces → fallback, sin excepción hacia fuera.
- Sin `OPENROUTER_API_KEY` → fallback sin llamar al SDK.
- La api key OPENROUTER_API_KEY es incorrecta → fallback, sin excepción hacia fuera.
- `GET /tickets/99999` → `404`. `GET /tickets/abc` → `422`.
- `PATCH` con `status: "inventado"` → `422`. `PATCH` a id inexistente → `404`.
- `GET /` devuelve `200` con `text/html`.
- `GET /aaaaaRRR` devuelve `404`

Ningún test propio llama al LLM real.

### 9.3 Cobertura

`pytest --cov=app` debe ejecutarse sin errores; objetivo orientativo ≥ 70 %.

---

## 10. Comportamiento esperado ante el checklist de QA adversarial (Día 2, Lab 5)

| # | Escenario | Comportamiento esperado |
|---|-----------|-------------------------|
| 1 | Title vacío o solo espacios | `422`, no se llama al LLM, no se guarda nada |
| 2 | Title de 5000 caracteres | `422` (máx. 200) |
| 3 | Emojis y Unicode no latino | Se guarda y se muestra tal cual (UTF-8) |
| 4 | HTML/JS en la descripción | Se guarda tal cual y se muestra **escapado**; el JS no se ejecuta |
| 5 | Inyección SQL básica | Se guarda como texto literal; la base de datos no cambia (consultas parametrizadas) |
| 6 | LLM caído (key falsa) | `201` con fallback; aviso en el log |
| 7 | Mismo ticket dos veces | Dos tickets (decisión de la sección 5) |
| 8 | IDs malformados (`-1`, `abc`, `99999999999`) | `404` para enteros inexistentes; `422` para no enteros |
| 9 | `PATCH` con valores inválidos | `422` sin modificar el ticket |
| 10 | 20 POSTs concurrentes | 20 tickets creados, sin `500` ni errores de base de datos bloqueada |

---

## 11. Criterios de aceptación finales (entrega del Día 2)

- [ ] Repo en GitHub con todos los commits del equipo.
- [ ] Los 5 tests obligatorios verdes (`pytest`).
- [ ] CI verde en GitHub Actions (ruff + pytest en el último commit de `main`).
- [ ] `main` protegida: solo se mergea vía PR con CI verde.
- [ ] App arranca con `uvicorn app.main:app --reload` y funciona en
      `http://localhost:8000`.
- [ ] Se puede crear un ticket por web y verlo en el tablero; los filtros funcionan.
- [ ] El clasificador devuelve resultados sensatos para al menos 3 tickets
      distintos del `seed_tickets.json` durante la demo.
- [ ] `README.md` actualizado con instrucciones de arranque y las decisiones
      de la sección 12.

### Fuera de alcance

Autenticación y usuarios, paginación, borrado de tickets, edición de título o
descripción, reclasificación manual, despliegue en la nube, notificaciones.

---

## 12. No negociable

- No commitear `.env`.
- No hardcodear API keys (leer de `OPENROUTER_API_KEY`).
- No propagar excepciones del SDK del LLM al endpoint.
- No modificar los tests de aceptación para hacerlos pasar.
- No introducir React, ORM ni build tools.
- No mergear a `main` con la CI en rojo.

---

## 13. Decisiones del equipo

Todo lo que la spec no cubre, o que decidáis hacer distinto, se anota aquí (y en
el README) con fecha y motivo. Ejemplo:

| Fecha | Decisión | Motivo |
|-------|----------|--------|
| 2026-11-23 | `db.py` acepta `DATABASE_URL` (`sqlite:///ruta`, prioritaria) además de `TRIAGEBOT_DB`, y resuelve la ruta en cada operación | Los tests de aceptación aíslan la BD con `DATABASE_URL`, fijada después de importar la app |
| 2026-11-23 | Cada conexión ejecuta `CREATE TABLE IF NOT EXISTS` (además de `init_db()` en el lifespan) | Los tests usan `TestClient(app)` sin `with`, así que el arranque de FastAPI no se ejecuta |
| 2026-11-23 | `main.py` envuelve `classifier.classify_ticket` en `try/except` y aplica el fallback | El test 4 sustituye el clasificador por uno que lanza excepción; el endpoint nunca debe dar 5xx |
| 2026-11-23 | `_rows.html` (solo filas) se incluye desde `_tickets_table.html`, que ya venía en la plantilla | Reutilizar el fichero existente y cumplir el fragmento `_rows.html` de la spec |
| 2026-11-23 | La respuesta HTMX a `POST /tickets` devuelve todas las filas (no solo la nueva) y los filtros se resetean | Así desaparece la fila "No hay tickets" y la tabla queda coherente sin JS extra |
| 2026-11-23 | En `GET /tickets`, un filtro vacío (`?status=`) equivale a "sin filtro"; un valor fuera del enum da `422` | Los `<select>` envían `""` para la opción "Todos" |
| 2026-11-23 | SQLite con `timeout=30` y `journal_mode=WAL` | 20 POST concurrentes sin errores de base de datos bloqueada (QA #10) |
| 2026-11-23 | Workflow movido de `workflows/ci.yml` a `.github/workflows/ci.yml` | GitHub Actions solo lee esa ruta |
| 2026-11-24 | Filtro `title` en `GET /tickets`: `LIKE '%texto%'` con `%`, `_` y `\` escapados (`ESCAPE '\'`), de modo que se buscan literalmente | Evitar que un `%` o `_` escrito por el usuario actúe como comodín |
| 2026-11-24 | El filtro `title` compara con una función SQL `lower_u` (Python `str.lower`) registrada en cada conexión | El `LIKE` de SQLite solo ignora mayúsculas en ASCII; así `página` encuentra `PÁGINA` |
| 2026-11-24 | `?title=` vacío o solo espacios equivale a "sin filtro" (nunca 422); en la UI el campo filtra al escribir con un retardo de 300 ms | Coherente con los demás filtros y evita una petición por tecla |
