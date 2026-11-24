"""Modelos Pydantic (entrada, salida) y enums de TriageBot."""

from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator, model_validator


class Category(str, Enum):
    bug = "bug"
    feature_request = "feature_request"
    question = "question"
    urgent = "urgent"


class Priority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class Status(str, Enum):
    open = "open"
    in_progress = "in_progress"
    waiting_response = "waiting_response"
    closed = "closed"


# Etiquetas que ve el usuario (BRIEF, "Ciclo de vida"). La API usa siempre los valores.
STATUS_LABELS = {
    Status.open.value: "Abierto",
    Status.in_progress.value: "En curso",
    Status.waiting_response.value: "A la espera de respuesta",
    Status.closed.value: "Cerrado",
}


ALLOWED_CATEGORIES = {c.value for c in Category}
ALLOWED_PRIORITIES = {p.value for p in Priority}
ALLOWED_STATUSES = {s.value for s in Status}

MAX_TAGS = 5
MAX_TAG_LENGTH = 30

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)
]


class TicketCreate(BaseModel):
    title: Title
    description: Description


class TicketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status | None = None
    priority: Priority | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "TicketUpdate":
        if self.status is None and self.priority is None:
            raise ValueError("Debe indicarse al menos 'status' o 'priority'")
        return self


class Ticket(BaseModel):
    id: int
    title: str
    description: str
    category: Category
    priority: Priority
    tags: list[str]
    status: Status
    created_at: str
    updated_at: str


class TicketStats(BaseModel):
    """Salida de GET /tickets/stats. Claves = valores del enum, con 0 si no hay tickets."""

    total: int
    by_category: dict[str, int]
    by_priority: dict[str, int]
    by_status: dict[str, int]


def normalize_tags(raw: list[Any]) -> list[str]:
    """Minúscula, strip, recorte a 30 chars, sin vacíos ni duplicados, máx. 5."""
    tags: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        tag = item.strip().lower()[:MAX_TAG_LENGTH].strip()
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) == MAX_TAGS:
            break
    return tags


class Classification(BaseModel):
    """Salida validada del clasificador. Los enums son estrictos (sin 'arreglos')."""

    category: Category
    priority: Priority
    tags: list[str] = []

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("tags debe ser una lista")
        return normalize_tags(value)

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "priority": self.priority.value,
            "tags": list(self.tags),
        }
