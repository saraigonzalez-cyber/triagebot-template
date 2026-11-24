"""TriageBot: app FastAPI, endpoints y render de plantillas.

Aquí no hay SQL (vive en app.db) ni llamadas al LLM (viven en app.classifier).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request, Response  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

# El clasificador se usa a través del módulo para que los tests puedan sustituirlo.
from app import classifier, db  # noqa: E402
from app.models import (  # noqa: E402
    STATUS_LABELS,
    Category,
    Priority,
    Status,
    Ticket,
    TicketCreate,
    TicketUpdate,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def format_datetime(value: str) -> str:
    """'2026-06-29T09:30:00Z' -> '29/06/2026 09:30'."""
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return value or ""


DEADLINE_DAYS = {"P1": 1, "P2": 2, "P3": 3}


def format_deadline(created_at: str, priority: str) -> str:
    """Fecha límite = created_at + 1/2/3 días según P1/P2/P3 (SPEC §7)."""
    try:
        created = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
        return (created + timedelta(days=DEADLINE_DAYS[priority])).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError, KeyError):
        return ""


templates.env.filters["fmt_date"] = format_datetime
templates.env.filters["fmt_deadline"] = format_deadline


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    yield


app = FastAPI(title="TriageBot", lifespan=lifespan)

TEMPLATE_CONTEXT = {
    "categories": [c.value for c in Category],
    "priorities": [p.value for p in Priority],
    "statuses": [s.value for s in Status],
    "status_labels": STATUS_LABELS,
}


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def render_rows(request: Request, tickets: list[dict], status_code: int = 200) -> Response:
    return templates.TemplateResponse(
        request,
        "_rows.html",
        {"tickets": tickets, **TEMPLATE_CONTEXT},
        status_code=status_code,
    )


def parse_filter(name: str, value: str | None, enum_cls: type[Enum]) -> str | None:
    """'' o None -> sin filtro. Valor fuera del enum -> 422."""
    if value is None or value == "":
        return None
    allowed = [e.value for e in enum_cls]
    if value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "type": "enum",
                    "loc": ["query", name],
                    "msg": f"Input should be one of: {', '.join(allowed)}",
                    "input": value,
                }
            ],
        )
    return value


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "index.html", {"tickets": db.list_tickets(), **TEMPLATE_CONTEXT}
    )


@app.post("/tickets", status_code=201, response_model=Ticket)
def create_ticket(payload: TicketCreate, request: Request):
    # Orden: validar (lo hace Pydantic antes de entrar) -> clasificar -> guardar -> responder.
    try:
        classification = classifier.classify_ticket(payload.title, payload.description)
    except Exception as exc:
        logger.warning("Clasificador lanzó %s; se usa el fallback", type(exc).__name__)
        classification = classifier.fallback()

    ticket = db.insert_ticket(payload.title, payload.description, classification)
    if is_htmx(request):
        return render_rows(request, db.list_tickets(), status_code=201)
    return ticket


@app.get("/tickets", response_model=list[Ticket])
def list_tickets(
    request: Request,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    title: str | None = None,
):
    tickets = db.list_tickets(
        category=parse_filter("category", category, Category),
        priority=parse_filter("priority", priority, Priority),
        status=parse_filter("status", status, Status),
        title=title,
    )
    if is_htmx(request):
        return render_rows(request, tickets)
    return tickets


@app.get("/tickets/{ticket_id}", response_model=Ticket)
def get_ticket(ticket_id: int):
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@app.patch("/tickets/{ticket_id}", response_model=Ticket)
def update_ticket(ticket_id: int, payload: TicketUpdate, request: Request):
    fields = {k: v.value for k, v in payload.model_dump(exclude_none=True).items()}
    ticket = db.update_ticket(ticket_id, fields)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if is_htmx(request):
        return render_rows(request, [ticket])
    return ticket
