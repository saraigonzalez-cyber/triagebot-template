# CLAUDE.md

> **Equipo B (Spec-Driven):** este archivo es vuestro. Completadlo con vuestras
> convenciones como parte del trabajo de hoy — es tan importante como el `SPEC.md`
> que vais a escribir. Claude Code lo lee automáticamente al abrir el repo.
>
> **Equipo A (Vibe):** podéis ignorarlo. No estáis obligados a tocarlo.

Este repo es una plantilla docente para construir **TriageBot**, una aplicación
FastAPI que clasifica tickets de soporte con un LLM (gpt-oss-120b vía OpenRouter).

## Stack (innegociable)

- Python 3.11+
- FastAPI
- SQLite
- HTMX + Jinja2
- Tailwind (por CDN)
- SDK de OpenAI (apuntado a OpenRouter)
- pytest
- ruff

## Reglas del taller (para todos los equipos)

Estas reglas no son metodología: son condiciones del bootcamp. Se cumplen seas
del equipo que seas.

1. No modifiques `tests/test_acceptance.py` salvo que el profesor lo indique
   expresamente.
2. Nunca hardcodees una API key en el código.
3. Lee la API key desde la variable de entorno `OPENROUTER_API_KEY`.
4. `.env` nunca se commitea. Comprueba que está en `.gitignore` antes de tu
   primer commit.

## Convenciones del Equipo B

- La fuente de verdad funcional es `SPEC.md`; si algo cambia, se anota en su sección 13.
- Dependencias entre módulos: `main` → `models`, `db`, `classifier`; `db` → `models`;
  `classifier` → `openai`, `models`. `classifier.py` y `db.py` nunca importan FastAPI.
- `main.py` llama al clasificador **vía módulo** (`from app import classifier` →
  `classifier.classify_ticket(...)`), nunca con `from app.classifier import ...`:
  si no, el `monkeypatch` de los tests no surte efecto.
- SQL solo en `app/db.py`, siempre con parámetros `?`. Una conexión por operación.
- `classify_ticket()` nunca lanza excepción: un reintento y luego fallback
  `{"category": "question", "priority": "P3", "tags": []}`. Nunca loguear la API key.
- HTML solo en `templates/`, con autoescape; nunca `|safe` con datos del usuario.
- Ningún test llama al LLM real. Antes de dar algo por terminado: `pytest` y `ruff check .` en verde.

## Comandos útiles

```bash
pytest -v
pytest --cov=app
ruff check .
uvicorn app.main:app --reload
```
