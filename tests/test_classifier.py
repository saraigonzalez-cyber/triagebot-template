"""Tests del clasificador. Nunca llaman al LLM real: se sustituye el cliente OpenAI."""

import json
from types import SimpleNamespace

import pytest

from app import classifier

FALLBACK = {"category": "question", "priority": "P3", "tags": []}


class FakeOpenAI:
    """Sustituto de openai.OpenAI. `outputs` es una lista de str o excepciones."""

    instances: list["FakeOpenAI"] = []
    outputs: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        FakeOpenAI.instances.append(self)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = FakeOpenAI.outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        message = SimpleNamespace(content=item)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture()
def fake_llm(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    FakeOpenAI.instances = []
    FakeOpenAI.outputs = []
    monkeypatch.setattr(classifier, "OpenAI", FakeOpenAI)

    def set_outputs(*outputs):
        FakeOpenAI.outputs = list(outputs)

    return set_outputs


def total_calls() -> int:
    return sum(len(i.calls) for i in FakeOpenAI.instances)


def test_valid_response(fake_llm):
    fake_llm('{"category": "bug", "priority": "P1", "tags": ["login", "error_500"]}')
    result = classifier.classify_ticket("Login roto", "Error 500 al entrar")
    assert result == {"category": "bug", "priority": "P1", "tags": ["login", "error_500"]}
    assert total_calls() == 1
    call = FakeOpenAI.instances[0].calls[0]
    assert call["model"] == "openai/gpt-oss-120b"
    assert call["temperature"] == 0
    assert call["max_tokens"] == 1024
    assert "Título: Login roto" in call["messages"][1]["content"]
    assert FakeOpenAI.instances[0].kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert FakeOpenAI.instances[0].kwargs["timeout"] == 20
    assert FakeOpenAI.instances[0].kwargs["max_retries"] == 0


def test_response_wrapped_in_markdown_fence(fake_llm):
    fake_llm('```json\n{"category": "feature_request", "priority": "P2", "tags": []}\n```')
    result = classifier.classify_ticket("t", "d")
    assert result == {"category": "feature_request", "priority": "P2", "tags": []}


def test_response_with_text_around_json(fake_llm):
    fake_llm('Aquí tienes: {"category": "question", "priority": "P3"} ¡Saludos!')
    result = classifier.classify_ticket("t", "d")
    assert result == {"category": "question", "priority": "P3", "tags": []}


def test_tags_are_normalized(fake_llm):
    tags = [" Login ", "LOGIN", "", "x" * 40, "a", "b", "c", "d"]
    fake_llm(f'{{"category": "bug", "priority": "P2", "tags": {json.dumps(tags)}}}')
    result = classifier.classify_ticket("t", "d")
    assert result["tags"] == ["login", "x" * 30, "a", "b", "c"]


@pytest.mark.parametrize(
    "bad",
    [
        '{"category": "URGENT", "priority": "P1", "tags": []}',
        '{"category": "Bug", "priority": "P1", "tags": []}',
        '{"category": "incidencia", "priority": "P1", "tags": []}',
        '{"category": "bug", "priority": "P9", "tags": []}',
        '{"category": "bug", "priority": "P1", "tags": "login"}',
        "no es json",
        "",
    ],
)
def test_invalid_output_retries_once_then_fallback(fake_llm, bad):
    fake_llm(bad, bad)
    assert classifier.classify_ticket("t", "d") == FALLBACK
    assert total_calls() == 2


def test_invalid_then_valid_uses_retry(fake_llm):
    fake_llm("basura", '{"category": "urgent", "priority": "P1", "tags": ["caida"]}')
    result = classifier.classify_ticket("t", "d")
    assert result == {"category": "urgent", "priority": "P1", "tags": ["caida"]}
    assert total_calls() == 2


def test_sdk_exception_twice_returns_fallback(fake_llm, caplog):
    fake_llm(TimeoutError("timeout"), RuntimeError("500"))
    with caplog.at_level("WARNING"):
        result = classifier.classify_ticket("t", "d")
    assert result == FALLBACK
    assert total_calls() == 2
    assert "fallback" in caplog.text
    assert "test-key" not in caplog.text


def test_missing_api_key_does_not_call_sdk(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    FakeOpenAI.instances = []
    monkeypatch.setattr(classifier, "OpenAI", FakeOpenAI)
    assert classifier.classify_ticket("t", "d") == FALLBACK
    assert FakeOpenAI.instances == []


def test_fallback_is_a_fresh_copy(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    first = classifier.classify_ticket("t", "d")
    first["tags"].append("mutado")
    assert classifier.classify_ticket("t", "d") == FALLBACK
