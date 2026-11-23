"""Clasificador de tickets: la ÚNICA llamada al LLM de TriageBot.

No importa FastAPI. Contrato: classify_ticket() devuelve SIEMPRE un dict
{"category", "priority", "tags"} válido; nunca None, nunca excepción.
"""

import json
import logging
import os
import re

from openai import OpenAI

from app.models import Classification

logger = logging.getLogger(__name__)

MODEL = "openai/gpt-oss-120b"
BASE_URL = "https://openrouter.ai/api/v1"
TEMPERATURE = 0
MAX_TOKENS = 1024
TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 2  # una llamada + un reintento

FALLBACK_CLASSIFICATION = {"category": "question", "priority": "P3", "tags": []}

SYSTEM_PROMPT = (
    "Eres un sistema de clasificación de tickets de soporte técnico. Recibirás el "
    "título y la descripción de un ticket. Devuelve EXCLUSIVAMENTE un objeto JSON "
    "válido con tres campos: category (uno de: bug, feature_request, question, "
    "urgent), priority (uno de: P1, P2, P3) y tags (lista de máx. 5 strings "
    "cortos en minúscula). Sin markdown, sin explicaciones, sin texto antes ni "
    "después. No inventes categorías.\n"
    "Criterios: urgent = servicio caído o bloqueo total de un cliente; "
    "bug = algo no funciona como debería; feature_request = petición de algo "
    "nuevo o mejora; question = duda de uso. P1 = hay que atenderlo hoy; "
    "P2 = importante, mañana; P3 = puede esperar."
)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def fallback() -> dict:
    return {**FALLBACK_CLASSIFICATION, "tags": []}


def parse_response(text: str | None) -> dict | None:
    """Parsea y valida la salida del LLM. Devuelve None si es inválida."""
    if not text:
        return None
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start == -1:
        return None
    try:
        data, _end = json.JSONDecoder().raw_decode(text[start:])
        if not isinstance(data, dict):
            return None
        return Classification.model_validate(data).to_dict()
    except Exception:
        return None


def classify_ticket(title: str, description: str) -> dict:
    """Clasifica un ticket con el LLM; ante cualquier problema, fallback."""
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        logger.warning("Clasificador: OPENROUTER_API_KEY no configurada; se usa el fallback")
        return fallback()

    try:
        # max_retries=0: el único reintento es el de nuestro bucle (SPEC §6).
        client = OpenAI(
            base_url=BASE_URL, api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=0
        )
    except Exception as exc:
        logger.warning(
            "Clasificador: no se pudo crear el cliente (%s); fallback", type(exc).__name__
        )
        return fallback()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Título: {title}\nDescripción: {description}"},
    ]

    cause = "desconocida"
    for _attempt in range(MAX_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
            )
            content = response.choices[0].message.content
        except Exception as exc:
            cause = f"excepción {type(exc).__name__}"
            continue
        result = parse_response(content)
        if result is not None:
            return result
        cause = "salida inválida del LLM"

    logger.warning("Clasificador: fallback tras %d intentos (causa: %s)", MAX_ATTEMPTS, cause)
    return fallback()
