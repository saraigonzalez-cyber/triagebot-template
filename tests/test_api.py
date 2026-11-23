"""Tests de la API y del frontend. El clasificador siempre está mockeado."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

HTMX = {"HX-Request": "true"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    calls = []

    def fake_classify(title, description):
        calls.append((title, description))
        return {"category": "bug", "priority": "P1", "tags": ["demo"]}

    monkeypatch.setattr("app.classifier.classify_ticket", fake_classify)
    c = TestClient(app)
    c.classifier_calls = calls
    return c


def create(client, title="Título", description="Descripción"):
    response = client.post("/tickets", json={"title": title, "description": description})
    assert response.status_code == 201
    return response.json()


def test_post_strips_whitespace_and_sets_timestamps(client):
    ticket = create(client, "  Hola  ", "  Mundo  ")
    assert ticket["title"] == "Hola"
    assert ticket["description"] == "Mundo"
    assert ticket["created_at"] == ticket["updated_at"]
    assert ticket["created_at"].endswith("Z")


def test_invalid_input_does_not_call_classifier_or_save(client):
    assert client.post("/tickets", json={"title": "   ", "description": "x"}).status_code == 422
    assert client.post("/tickets", json={"description": "x"}).status_code == 422
    assert client.classifier_calls == []
    assert client.get("/tickets").json() == []


def test_duplicates_create_two_tickets(client):
    create(client, "Igual", "Igual")
    create(client, "Igual", "Igual")
    assert len(client.get("/tickets").json()) == 2


def test_list_is_ordered_newest_first(client):
    first = create(client, "Primero")
    second = create(client, "Segundo")
    ids = [t["id"] for t in client.get("/tickets").json()]
    assert ids == [second["id"], first["id"]]


def test_get_ticket_by_id(client):
    ticket = create(client)
    response = client.get(f"/tickets/{ticket['id']}")
    assert response.status_code == 200
    assert response.json() == ticket


@pytest.mark.parametrize("ticket_id", ["99999", "-1", "99999999999"])
def test_get_missing_ticket_returns_404(client, ticket_id):
    response = client.get(f"/tickets/{ticket_id}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Ticket not found"}


def test_get_non_integer_id_returns_422(client):
    assert client.get("/tickets/abc").status_code == 422


def test_invalid_filter_returns_422(client):
    assert client.get("/tickets", params={"priority": "P9"}).status_code == 422
    assert client.get("/tickets", params={"category": "URGENT"}).status_code == 422
    assert client.get("/tickets", params={"status": "done"}).status_code == 422


def test_empty_filter_means_all(client):
    create(client)
    response = client.get("/tickets", params={"category": "", "priority": "", "status": ""})
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_patch_updates_updated_at_only_allowed_fields(client):
    ticket = create(client)
    patched = client.patch(f"/tickets/{ticket['id']}", json={"status": "closed"})
    assert patched.status_code == 200
    data = patched.json()
    assert data["status"] == "closed"
    assert data["priority"] == ticket["priority"]
    assert data["created_at"] == ticket["created_at"]


@pytest.mark.parametrize(
    "body",
    [
        {"status": "inventado"},
        {"priority": "P9"},
        {},
        {"title": "nuevo"},
        {"status": "open", "category": "bug"},
    ],
)
def test_patch_invalid_returns_422_without_changes(client, body):
    ticket = create(client)
    assert client.patch(f"/tickets/{ticket['id']}", json=body).status_code == 422
    assert client.get(f"/tickets/{ticket['id']}").json() == ticket


def test_patch_missing_ticket_returns_404(client):
    assert client.patch("/tickets/99999", json={"status": "closed"}).status_code == 404


def test_sql_injection_is_stored_literally(client):
    evil = "'; DROP TABLE tickets; --"
    ticket = create(client, evil, evil)
    assert ticket["title"] == evil
    assert len(client.get("/tickets").json()) == 1


def test_unicode_and_emojis_roundtrip(client):
    text = "El sistema falla 🔥 — 日本語 — ñandú"
    ticket = create(client, text, text)
    assert client.get(f"/tickets/{ticket['id']}").json()["title"] == text


def test_index_renders_html_and_escapes_user_content(client):
    create(client, "<script>alert(1)</script>", "<b>hola</b>")
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_index_empty_state(client):
    assert "No hay tickets" in client.get("/").text


def test_htmx_requests_return_html_rows(client):
    created = client.post(
        "/tickets", json={"title": "HTMX", "description": "desde la web"}, headers=HTMX
    )
    assert created.status_code == 201
    assert created.headers["content-type"].startswith("text/html")
    assert "HTMX" in created.text and "<tr" in created.text

    listed = client.get("/tickets", params={"category": "question"}, headers=HTMX)
    assert "No hay tickets" in listed.text

    ticket_id = client.get("/tickets").json()[0]["id"]
    patched = client.patch(f"/tickets/{ticket_id}", json={"status": "closed"}, headers=HTMX)
    assert patched.status_code == 200
    assert f'id="ticket-{ticket_id}"' in patched.text


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
