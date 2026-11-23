# TriageBot Template

Repo plantilla para el bootcamp **Prompt & Commit: Desarrollo de aplicaciones con IA Generativa**.

Durante el bootcamp construiréis una aplicación web interna para clasificar tickets de soporte usando IA generativa.

## Qué vais a construir

TriageBot permite:

- Crear tickets con `title` y `description`.
- Clasificarlos automáticamente con un LLM (gpt-oss-120b vía OpenRouter) en:
  - `category`: `bug`, `feature_request`, `question`, `urgent`
  - `priority`: `P1`, `P2`, `P3`
  - `tags`: lista de etiquetas cortas.
- Persistirlos en SQLite.
- Verlos en un tablero web con filtros.
- Ejecutar tests automáticos y CI en GitHub Actions.

## Stack obligatorio

| Capa | Herramienta |
|---|---|
| Lenguaje | Python 3.11+ |
| Backend | FastAPI |
| Datos | SQLite |
| Frontend | HTML + HTMX + Tailwind CDN |
| LLM | gpt-oss-120b vía OpenRouter (SDK de `openai`) |
| Tests | pytest |
| CI/CD | GitHub Actions |
| IDE + IA | VS Code + Claude Code |

## Setup local

```bash
git clone https://github.com/<tu-usuario>/triagebot-template.git
cd triagebot-template
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env
```

Edita `.env` y añade tu API key:

```bash
OPENROUTER_API_KEY=sk-or-...
```

Comprueba que `.env` está ignorado por Git:

```bash
git status
```

`.env` **no debe aparecer**.

## Ejecutar tests

```bash
pytest -v              # 5 tests de aceptación + tests propios (test_api.py, test_classifier.py)
pytest --cov=app       # cobertura (objetivo ≥ 70 %)
ruff check .           # lint
```

Ningún test llama al LLM real: el clasificador o el cliente de OpenAI se sustituyen con `monkeypatch`.

## Ejecutar la app

```bash
uvicorn app.main:app --reload
```

Abre:

```text
http://127.0.0.1:8000
```

Variables de entorno:

| Variable | Obligatoria | Uso |
|---|---|---|
| `OPENROUTER_API_KEY` | Sí (para clasificar de verdad) | Sin ella, todos los tickets reciben el fallback `question / P3 / []`. |
| `TRIAGEBOT_DB` | No | Ruta del archivo SQLite (por defecto `triagebot.db`). |
| `DATABASE_URL` | No | Alternativa con formato `sqlite:///ruta` (la usan los tests); tiene prioridad sobre `TRIAGEBOT_DB`. |

## Estructura

```
app/main.py        Endpoints FastAPI y render de plantillas (sin SQL ni LLM)
app/models.py      Modelos Pydantic y enums
app/db.py          Persistencia con sqlite3 (consultas parametrizadas)
app/classifier.py  Única llamada al LLM; reintento + fallback
templates/         index.html, _tickets_table.html, _rows.html (fragmento HTMX)
tests/             test_acceptance.py (no se toca), test_api.py, test_classifier.py
```

## Decisiones del equipo

Ver la tabla de la sección 13 de [`SPEC.md`](SPEC.md).

## Contrato mínimo del producto

Los detalles obligatorios están en:

- [`BRIEF.md`](BRIEF.md): briefing del cliente.
- [`SPEC.md`](SPEC.md): contrato funcional recomendado.
- [`CLAUDE.md`](CLAUDE.md): instrucciones del repo para Claude Code.
- [`tests/test_acceptance.py`](tests/test_acceptance.py): los 5 tests obligatorios.

## Reglas del bootcamp

1. Lo que no acaba en GitHub no existe.
2. No se commitean API keys.
3. Commit pequeño cada 20–30 minutos.
4. Leed el diff antes de aceptar cambios de la IA.
5. Si Claude Code propone una dependencia, verificad que existe antes de instalarla.
6. Los tests son la red de seguridad.

## Equipo

Nombres: Antxon, Sarai, Virginia, Federidson, prueba


Metodología: `Spec-Driven`
