"""Checklist de QA adversarial (SPEC §10): un test por escenario.

Ningún test llama al LLM real. `raise_server_exceptions=False` hace que un fallo
interno se vea como un 500 y no como una excepción en el test.
"""

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import classifier
from app.main import app

JSON_HEADERS = {"content-type": "application/json"}


@pytest.fixture()
def db_file(tmp_path, monkeypatch):
    path = tmp_path / "qa.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    return path


@pytest.fixture()
def client(db_file, monkeypatch):
    calls = []

    def fake_classify(title, description):
        calls.append((title, description))
        return {"category": "bug", "priority": "P1", "tags": ["demo"]}

    monkeypatch.setattr("app.classifier.classify_ticket", fake_classify)
    c = TestClient(app, raise_server_exceptions=False)
    c.classifier_calls = calls
    return c


def post(client, title="Título", description="Descripción"):
    return client.post("/tickets", json={"title": title, "description": description})


# 1. Title vacío o solo espacios
@pytest.mark.parametrize("title", ["", "   ", "\t\n "])
def test_qa_01_empty_title_is_422_without_llm_or_save(client, title):
    assert post(client, title).status_code == 422
    assert client.classifier_calls == []
    assert client.get("/tickets").json() == []


# 2. Title de 5000 caracteres
def test_qa_02_oversized_title_and_description_are_422(client):
    assert post(client, "a" * 5000).status_code == 422
    assert post(client, "ok", "a" * 5001).status_code == 422
    assert client.classifier_calls == []
    assert client.get("/tickets").json() == []


# 3. Emojis y Unicode no latino
@pytest.mark.parametrize("text", ["🔥 Fallo 💥", "日本語のチケット", "مرحبا بالعالم", "Ñandú ü ß"])
def test_qa_03_unicode_is_stored_as_is(client, text):
    created = post(client, text, text)
    assert created.status_code == 201
    assert client.get(f"/tickets/{created.json()['id']}").json()["title"] == text


def test_qa_03_lone_surrogate_is_rejected_not_500(client):
    body = rb'{"title": "a\ud800", "description": "x"}'
    response = client.post("/tickets", content=body, headers=JSON_HEADERS)
    assert response.status_code == 422
    assert client.get("/tickets").json() == []


def test_qa_03_nul_character_is_rejected(client):
    assert post(client, "a\x00b").status_code == 422


# 4. HTML/JS en la descripción
def test_qa_04_html_is_stored_raw_and_rendered_escaped(client):
    xss = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    created = post(client, "xss", xss)
    assert created.status_code == 201
    assert created.json()["description"] == xss
    html = client.get("/").text
    assert xss not in html
    assert "&lt;script&gt;" in html


# 5. Inyección SQL básica
def test_qa_05_sql_injection_is_literal_and_table_survives(client, db_file):
    payload = "x'); DROP TABLE tickets; --"
    assert post(client, payload, payload).status_code == 201
    assert client.get("/tickets", params={"title": payload}).json()[0]["title"] == payload
    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1


# 6. LLM caído (key falsa): clasificador real, SDK que falla
def test_qa_06_llm_down_returns_201_with_fallback_and_warning(db_file, monkeypatch, caplog):
    monkeypatch.setenv("OPENROUTER_API_KEY", "key-falsa")

    class BrokenOpenAI:
        def __init__(self, **kwargs):
            def boom(**_kw):
                raise RuntimeError("401 invalid api key")

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=boom))

    monkeypatch.setattr(classifier, "OpenAI", BrokenOpenAI)
    c = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.WARNING):
        response = post(c, "Login roto", "Error 500")
    assert response.status_code == 201
    body = response.json()
    assert (body["category"], body["priority"], body["tags"]) == ("question", "P3", [])
    assert any("fallback" in r.getMessage().lower() for r in caplog.records)
    assert "key-falsa" not in caplog.text


# 7. Mismo ticket dos veces seguidas
def test_qa_07_duplicates_create_two_tickets(client):
    a, b = post(client, "Igual", "Igual").json(), post(client, "Igual", "Igual").json()
    assert a["id"] != b["id"]
    assert len(client.get("/tickets").json()) == 2


# 8. IDs malformados
@pytest.mark.parametrize("ticket_id", ["-1", "0", "99999999999", "99999999999999999999"])
def test_qa_08_unknown_integer_ids_are_404(client, ticket_id):
    assert client.get(f"/tickets/{ticket_id}").status_code == 404
    assert client.patch(f"/tickets/{ticket_id}", json={"status": "closed"}).status_code == 404


def test_qa_08_non_integer_id_is_422(client):
    assert client.get("/tickets/abc").status_code == 422
    assert client.patch("/tickets/abc", json={"status": "closed"}).status_code == 422


# 9. PATCH con valores inválidos
@pytest.mark.parametrize(
    "body",
    [
        {"status": "inventado"},
        {"priority": "P9"},
        {"status": "Cerrado"},
        {},
        {"status": None},
        {"title": "nuevo"},
        {"status": "closed", "category": "bug"},
    ],
)
def test_qa_09_invalid_patch_is_422_and_does_not_modify(client, body):
    original = post(client, "t", "d").json()
    assert client.patch(f"/tickets/{original['id']}", json=body).status_code == 422
    assert client.get(f"/tickets/{original['id']}").json() == original


# 10. 20 POSTs concurrentes
def test_qa_10_twenty_concurrent_posts(client):
    with ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(lambda i: post(client, f"conc {i}", "d"), range(20)))
    assert [r.status_code for r in responses] == [201] * 20
    assert len({r.json()["id"] for r in responses}) == 20
    assert len(client.get("/tickets").json()) == 20



# Extra: contrato del clasificador (defensa en profundidad en el endpoint)
@pytest.mark.parametrize(
    "bad",
    [None, {}, {"category": "bug"}, {"category": "URGENT", "priority": "P1", "tags": []}],
)
def test_qa_extra_invalid_classifier_output_uses_fallback(client, monkeypatch, bad):
    monkeypatch.setattr("app.classifier.classify_ticket", lambda t, d: bad)
    response = post(client)
    assert response.status_code == 201
    assert response.json()["category"] == "question"
    assert client.get("/tickets").status_code == 200
