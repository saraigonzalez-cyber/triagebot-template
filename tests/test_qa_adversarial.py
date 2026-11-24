"""Pruebas adicionales de QA adversarial (SPEC §9.4; comportamiento esperado en §10).

Cada test lleva el número de la casuística. Ningún test llama al LLM real.
"""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from app import classifier
from app.main import app

HTMX = {"HX-Request": "true"}
FALLBACK = {"category": "question", "priority": "P3", "tags": []}


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'qa.db'}")


@pytest.fixture()
def client(db_url, monkeypatch):
    """Cliente con BD temporal y clasificador mockeado que registra sus llamadas."""
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


# 1. Title vacío o solo espacios -> 422, no se llama al LLM, no se guarda nada
@pytest.mark.parametrize("title", ["", "   ", "\t\n "])
def test_qa01_empty_or_blank_title(client, title):
    response = client.post("/tickets", json={"title": title, "description": "Válida"})
    assert response.status_code == 422
    assert client.classifier_calls == []
    assert client.get("/tickets").json() == []


# 2. Title de 5000 caracteres -> 422 (máx. 200)
def test_qa02_title_5000_chars(client):
    response = client.post("/tickets", json={"title": "x" * 5000, "description": "Válida"})
    assert response.status_code == 422
    assert client.classifier_calls == []
    assert client.get("/tickets").json() == []


@pytest.mark.parametrize(("length", "expected"), [(200, 201), (201, 422)])
def test_qa02_title_length_boundary(client, length, expected):
    response = client.post("/tickets", json={"title": "x" * length, "description": "d"})
    assert response.status_code == expected


# 3. Emojis y Unicode no latino -> se guarda y se muestra tal cual (UTF-8)
def test_qa03_unicode_and_emojis(client):
    title = "Fallo 🔥🚀 — 日本語 — Ελληνικά — العربية"
    description = "Описание: ñandú, 中文, emoji 👩‍💻, combinado é"
    ticket = create(client, title, description)
    assert ticket["title"] == title
    assert ticket["description"] == description

    fetched = client.get(f"/tickets/{ticket['id']}").json()
    assert fetched["title"] == title
    assert fetched["description"] == description
    assert client.get("/tickets").json()[0]["title"] == title

    page = client.get("/")
    assert page.status_code == 200
    assert title in page.text


# 4. HTML/JS en la descripción -> se guarda tal cual y se muestra escapado
@pytest.mark.parametrize(
    ("payload", "escaped"),
    [
        ("<script>alert('xss')</script>", "&lt;script&gt;"),
        ('<img src=x onerror="alert(1)">', "&lt;img src=x onerror="),
    ],
)
def test_qa04_xss_is_stored_literally_and_escaped(client, payload, escaped):
    ticket = create(client, payload, payload)
    assert ticket["title"] == payload
    assert ticket["description"] == payload

    page = client.get("/").text
    assert payload not in page
    assert escaped in page

    rows = client.get("/tickets", headers=HTMX).text
    assert payload not in rows
    assert escaped in rows


# 5. Inyección SQL básica -> texto literal; la BD no cambia
@pytest.mark.parametrize(
    "evil",
    ["'; DROP TABLE tickets; --", "' OR '1'='1", "Robert'); DELETE FROM tickets; --"],
)
def test_qa05_sql_injection_is_stored_literally(client, evil):
    ticket = create(client, evil, evil)
    assert ticket["title"] == evil
    assert client.get(f"/tickets/{ticket['id']}").json()["description"] == evil

    create(client, "Otro ticket")  # la tabla sigue existiendo
    assert len(client.get("/tickets").json()) == 2


def test_qa05_sql_injection_in_filter_does_not_match_everything(client):
    create(client, "Login roto")
    create(client, "Exportar a PDF")
    response = client.get("/tickets", params={"title": "' OR '1'='1"})
    assert response.status_code == 200
    assert response.json() == []
    assert len(client.get("/tickets").json()) == 2


# 6. LLM caído (key falsa) -> 201 con fallback; aviso en el log
def _auth_error() -> Exception:
    request = httpx.Request("POST", f"{classifier.BASE_URL}/chat/completions")
    response = httpx.Response(401, request=request)
    return openai.AuthenticationError("Invalid API key", response=response, body=None)


def _connection_error() -> Exception:
    request = httpx.Request("POST", f"{classifier.BASE_URL}/chat/completions")
    return openai.APIConnectionError(request=request)


@pytest.mark.parametrize("make_error", [_auth_error, _connection_error])
def test_qa06_llm_down_uses_fallback(db_url, monkeypatch, caplog, make_error):
    """Se ejecuta el clasificador real, con el cliente OpenAI sustituido por uno que falla."""
    fake_key = "sk-or-clave-falsa"
    monkeypatch.setenv("OPENROUTER_API_KEY", fake_key)
    calls = []

    class FailingOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kwargs):
            calls.append(kwargs)
            raise make_error()

    monkeypatch.setattr(classifier, "OpenAI", FailingOpenAI)
    client = TestClient(app)

    with caplog.at_level("WARNING"):
        response = client.post(
            "/tickets", json={"title": "Servicio caído", "description": "No responde nada"}
        )

    assert response.status_code == 201
    data = response.json()
    assert {k: data[k] for k in FALLBACK} == FALLBACK
    assert data["status"] == "open"
    assert len(calls) == 2  # una llamada + un reintento
    assert "fallback" in caplog.text
    assert fake_key not in caplog.text


# 7. Mismo ticket dos veces -> dos tickets
def test_qa07_same_ticket_twice_creates_two(client):
    first = create(client, "Duplicado", "Mismo texto")
    second = create(client, "Duplicado", "Mismo texto")
    assert first["id"] != second["id"]
    tickets = client.get("/tickets").json()
    assert len(tickets) == 2
    assert {t["id"] for t in tickets} == {first["id"], second["id"]}


# 8. IDs malformados -> 404 para enteros inexistentes; 422 para no enteros
@pytest.mark.parametrize(
    ("ticket_id", "expected"),
    [("-1", 404), ("0", 404), ("99999", 404), ("99999999999", 404), ("abc", 422), ("1.5", 422)],
)
def test_qa08_malformed_ids(client, ticket_id, expected):
    create(client)
    assert client.get(f"/tickets/{ticket_id}").status_code == expected
    patched = client.patch(f"/tickets/{ticket_id}", json={"status": "closed"})
    assert patched.status_code == expected


# 9. PATCH con valores inválidos -> 422 sin modificar el ticket
@pytest.mark.parametrize(
    "body",
    [
        {"status": "inventado"},
        {"status": "Cerrado"},
        {"status": "CLOSED"},
        {"priority": "P9"},
        {"priority": "p1"},
        {"status": "closed", "priority": "P9"},
        {},
        {"category": "urgent"},
        {"title": "Cambiado"},
    ],
)
def test_qa09_patch_invalid_values(client, body):
    ticket = create(client)
    response = client.patch(f"/tickets/{ticket['id']}", json=body)
    assert response.status_code == 422
    assert client.get(f"/tickets/{ticket['id']}").json() == ticket


# 10. 20 POSTs concurrentes -> 20 tickets, sin 500 ni "database is locked"
def test_qa10_20_concurrent_posts(client):
    def post(i):
        local = TestClient(app)
        return local.post(
            "/tickets", json={"title": f"Concurrente {i}", "description": f"Petición {i}"}
        )

    with ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(post, range(20)))

    assert [r.status_code for r in responses] == [201] * 20
    ids = {r.json()["id"] for r in responses}
    assert len(ids) == 20

    tickets = client.get("/tickets").json()
    assert len(tickets) == 20
    assert {t["title"] for t in tickets} == {f"Concurrente {i}" for i in range(20)}
