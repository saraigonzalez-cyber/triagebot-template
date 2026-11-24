# Informe de QA — TriageBot

Fecha: 2026-11-24 · Alcance: checklist adversarial de SPEC §10 (10 escenarios) + revisión del código.

## Resumen

| | Antes de corregir | Después |
|---|---|---|
| Tests existentes (55) | ✅ 55 pasan | ✅ 55 pasan |
| Tests QA del checklist (`tests/test_qa_checklist.py`) | ❌ 3 fallan | ✅ todos pasan |
| Total `pytest` | 79 ✅ · 3 ❌ | **86 ✅ · 0 ❌** |
| `ruff check .` | ✅ | ✅ |
| Cobertura `app/` | — | 94 % |

## Resultado por escenario

| # | Escenario | Antes | Después |
|---|---|---|---|
| 1 | Title vacío o solo espacios | ✅ 422, sin LLM ni guardado | ✅ |
| 2 | Title de 5000 caracteres | ✅ 422 | ✅ |
| 3 | Emojis y Unicode no latino | ✅ se guardan tal cual | ✅ |
| 3b | Surrogate suelto (`\ud800`) | ❌ **500** | ✅ 422 |
| 3c | Carácter NUL (`\x00`) | ❌ 201 (se guardaba) | ✅ 422 |
| 4 | HTML/JS en la descripción | ✅ escapado al mostrarse | ✅ |
| 5 | Inyección SQL | ✅ texto literal, tabla intacta | ✅ |
| 6 | LLM caído (key falsa) | ✅ 201 con fallback + warning, sin filtrar la key | ✅ |
| 7 | Mismo ticket dos veces | ✅ dos tickets | ✅ |
| 8 | IDs `-1`, `0`, `99999999999` | ✅ 404 (`abc` → 422) | ✅ |
| 8b | ID `99999999999999999999` | ❌ **500** | ✅ 404 |
| 9 | PATCH con valores inválidos | ✅ 422 sin modificar | ✅ |
| 10 | 20 POST concurrentes | ✅ 20×201, ids únicos | ✅ |

## Bugs encontrados y corrección

### 1. ID fuera del rango de SQLite → 500 (severidad alta)
- **Síntoma:** `GET` / `PATCH /tickets/99999999999999999999` devolvía 500; SPEC §10.8 espera 404.
- **Causa:** SQLite lanza `OverflowError` al recibir un entero mayor que 2⁶³.
- **Corrección:** `app/db.py` — `get_ticket` captura `OverflowError` y `update_ticket` comprueba el rango; ambos devuelven `None` (→ 404).

### 2. Surrogate suelto en title/description → 500 (severidad alta)
- **Síntoma:** `POST /tickets` con `{"title":"a\ud800",...}` daba 500.
- **Causa (doble):** (a) el valor no es codificable en UTF-8 y fallaba al guardar; (b) tras validarlo, la propia respuesta 422 de FastAPI incluye el `input` con el surrogate y no podía serializarse.
- **Corrección:** `app/models.py` — `AfterValidator` en `Title` y `Description` (`_reject_unstorable_chars`); `app/main.py` — handler de `RequestValidationError` que mantiene el 422 estándar pero sanea el `input`.

### 3. Carácter NUL aceptado (severidad baja)
- **Síntoma:** `\x00` se guardaba en el título.
- **Corrección:** el mismo validador lo rechaza con 422. La SPEC no lo menciona; es una decisión de QA (anotar en SPEC §13 si se mantiene).

### 4. Salida inválida del clasificador no se validaba en el endpoint (severidad media)
- **Síntoma:** si `classify_ticket` devolvía `None`, `{}` o `priority:"P9"`, el `POST` daba 500 o guardaba una fila corrupta que luego rompía `GET /tickets`. Con el clasificador real no ocurría (ya valida), por lo que era una defensa que faltaba.
- **Corrección:** `app/main.py` — el resultado pasa por `Classification.model_validate(...)`; si falla, se aplica `classifier.fallback()`. Sigue llamándose vía módulo (`classifier.classify_ticket`).

## Archivos modificados

- `app/db.py`, `app/models.py`, `app/main.py`
- `tests/test_qa_checklist.py` (nuevo). `tests/test_acceptance.py` **no** se ha tocado.

## Observaciones pendientes (no corregidas)

- `GET /tickets?status=%20` devuelve 422, mientras que `?title=%20` equivale a "sin filtro" (inconsistente).
- Si el `PATCH` desde el `select` de una fila falla, el select conserva el valor nuevo y el error aparece en el cuadro del formulario de creación.
- Tras cambiar el estado con un filtro activo, la fila sigue visible aunque ya no cumpla el filtro.
- La fecha límite se calcula desde `created_at` con la prioridad actual (cambia al hacer `PATCH` de prioridad); la SPEC no lo precisa.
- Las fechas son UTC y la UI no lo indica.
- `MAX_TOKENS=1024` con `gpt-oss-120b` (modelo de razonamiento) podría truncar la salida y provocar fallbacks silenciosos; no verificado.
- La UI no se probó en navegador (htmx y Tailwind vienen de CDN).
- Nota: durante el QA el `.env` real se cargó, por lo que algunas llamadas fueron al LLM real (pocos tickets, sobre BD temporal).

## Decisiones a anotar en SPEC §13

| Fecha | Decisión | Motivo |
|---|---|---|
| 2026-11-24 | `title`/`description` rechazan NUL y surrogates sueltos (422); el 422 sanea el `input` | Evitar 500 al guardar y al serializar el error |
| 2026-11-24 | IDs fuera del rango de enteros de SQLite → 404 | SPEC §10.8 |
| 2026-11-24 | `POST /tickets` valida la salida del clasificador con `Classification` y aplica fallback si no cumple | El endpoint nunca debe dar 5xx ni guardar categorías inválidas |
