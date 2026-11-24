"""Persistencia de tickets con sqlite3 (sin ORM).

Una conexión por operación; todas las consultas son parametrizadas.
"""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

DEFAULT_DB_PATH = "triagebot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    priority    TEXT    NOT NULL,
    tags        TEXT    NOT NULL DEFAULT '[]',
    status      TEXT    NOT NULL DEFAULT 'open',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
"""

# Columnas por las que se permite filtrar: nombres fijos, nunca datos del usuario.
FILTER_COLUMNS = ("category", "priority", "status")
UPDATABLE_COLUMNS = ("status", "priority")


def db_path() -> str:
    """Ruta del archivo SQLite. Se resuelve en cada operación.

    Prioridad: DATABASE_URL (formato sqlite:///ruta, lo usan los tests),
    luego TRIAGEBOT_DB, luego triagebot.db.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        for prefix in ("sqlite:///", "sqlite://"):
            if url.startswith(prefix):
                return url[len(prefix) :]
        return url
    return os.getenv("TRIAGEBOT_DB", DEFAULT_DB_PATH)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    # LIKE de SQLite solo ignora mayúsculas en ASCII; esto cubre tildes y ñ.
    conn.create_function("lower_u", 1, lambda s: s.lower() if isinstance(s, str) else s)
    try:
        conn.execute(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")


def _row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["tags"] = json.loads(data["tags"] or "[]")
    return data


def insert_ticket(title: str, description: str, classification: dict) -> dict:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO tickets
                (title, description, category, priority, tags, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                title,
                description,
                classification["category"],
                classification["priority"],
                json.dumps(classification.get("tags", []), ensure_ascii=False),
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


def get_ticket(ticket_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return _row_to_dict(row) if row else None


def _like_pattern(text: str) -> str:
    """'%texto%' en minúscula, con %, _ y \\ escapados para buscarlos literalmente."""
    escaped = text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_tickets(
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    title: str | None = None,
) -> list[dict]:
    values = {"category": category, "priority": priority, "status": status}
    clauses = [f"{col} = ?" for col in FILTER_COLUMNS if values[col] is not None]
    params = [values[col] for col in FILTER_COLUMNS if values[col] is not None]
    if title is not None and title.strip():
        clauses.append("lower_u(title) LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(title.strip()))
    sql = "SELECT * FROM tickets"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC, id DESC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def ticket_stats() -> dict:
    """Conteos por category, priority y status (solo valores presentes) y total."""
    stats: dict = {}
    with _connect() as conn:
        stats["total"] = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        for col in FILTER_COLUMNS:
            # col sale de FILTER_COLUMNS (nombres fijos), nunca de datos del usuario.
            rows = conn.execute(f"SELECT {col}, COUNT(*) FROM tickets GROUP BY {col}").fetchall()
            stats[col] = {row[0]: row[1] for row in rows}
    return stats


def update_ticket(ticket_id: int, fields: dict) -> dict | None:
    updates = {k: v for k, v in fields.items() if k in UPDATABLE_COLUMNS and v is not None}
    with _connect() as conn:
        if conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone() is None:
            return None
        sets = [f"{col} = ?" for col in updates] + ["updated_at = ?"]
        params = [*updates.values(), _now(), ticket_id]
        conn.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id = ?", params)
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return _row_to_dict(row)
