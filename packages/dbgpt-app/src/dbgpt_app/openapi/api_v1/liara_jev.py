"""Liara-hosted TypeSafe Jev decision API client and request schema."""

import asyncio
import os
from typing import Any, Dict, List, Optional, Union

import httpx

from dbgpt._private.pydantic import BaseModel, Field, field_validator

LIARA_DEFAULT_WORKSPACE_ID = "6aba44bb5d4e25a451926448"
LIARA_JEV_MODEL_ID = "typesafe/jev-1.13"
LIARA_DEFAULT_API_BASE = (
    f"https://ai.liara.ir/api/{LIARA_DEFAULT_WORKSPACE_ID}/v1"
)

# P0 latency win: one shared keepalive client instead of a fresh TCP+TLS
# handshake per Jev call. Created lazily; only used when the caller does not
# pass a custom ``transport`` (tests / custom transports keep the ephemeral
# per-call client so per-request transport injection keeps working).
_JEV_SHARED_CLIENT: Optional["httpx.AsyncClient"] = None
_JEV_CLIENT_LOCK: Optional[asyncio.Lock] = None


async def _get_shared_jev_client() -> "httpx.AsyncClient":
    """Return the shared keepalive Jev HTTP client (never raises on reuse)."""
    global _JEV_SHARED_CLIENT, _JEV_CLIENT_LOCK
    client = _JEV_SHARED_CLIENT
    if client is not None and not client.is_closed:
        return client
    if _JEV_CLIENT_LOCK is None:
        _JEV_CLIENT_LOCK = asyncio.Lock()
    async with _JEV_CLIENT_LOCK:
        client = _JEV_SHARED_CLIENT
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(
                    max_connections=20, max_keepalive_connections=10
                ),
            )
            _JEV_SHARED_CLIENT = client
        return client


async def close_jev_shared_client() -> None:
    """Close the shared Jev client (best-effort; safe to call anytime)."""
    global _JEV_SHARED_CLIENT
    client = _JEV_SHARED_CLIENT
    _JEV_SHARED_CLIENT = None
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            pass


class LiaraJevDecisionRequest(BaseModel):
    """A TypeSafe System One evaluation request sent through Liara."""

    state: Union[str, Dict[str, Any], List[Any]] = Field(
        ..., description="Text or structured state Jev should evaluate."
    )
    questions: Dict[str, Dict[str, Any]] = Field(
        ...,
        min_length=1,
        description="Named TypeSafe noul, choice, or score questions.",
    )

    @field_validator("questions")
    @classmethod
    def validate_questions(cls, questions: Dict[str, Dict[str, Any]]):
        for name, question in questions.items():
            if not name.strip():
                raise ValueError("Question names must not be empty.")
            if not isinstance(question, dict):
                raise ValueError(f"Question '{name}' must be an object.")
            if question.get("type") not in {"noul", "choice", "score"}:
                raise ValueError(
                    f"Question '{name}' type must be 'noul', 'choice', or 'score'."
                )
            if "instructions" not in question:
                raise ValueError(f"Question '{name}' requires instructions.")
            if question["type"] == "choice" and not isinstance(
                question.get("criteria"), dict
            ):
                raise ValueError(
                    f"Choice question '{name}' requires criteria as an object."
                )
            if question["type"] == "score" and (
                not isinstance(question.get("criteria"), list)
                or len(question["criteria"]) < 2
            ):
                raise ValueError(
                    f"Score question '{name}' requires at least two criteria levels."
                )
        return questions


async def evaluate_liara_jev(
    state: Union[str, Dict[str, Any], List[Any]],
    questions: Dict[str, Dict[str, Any]],
    *,
    api_key: str = None,
    api_base: str = None,
    transport: httpx.AsyncBaseTransport = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """Evaluate typed questions with Jev through Liara's decisions endpoint.

    ``timeout`` bounds the HTTP call itself; callers (triage/verify) enforce
    their own tighter end-to-end budgets with ``asyncio.wait_for``.
    """
    request = LiaraJevDecisionRequest(state=state, questions=questions)
    api_key = api_key or os.getenv("LIARA_API_KEY")
    if not api_key:
        raise RuntimeError("Set LIARA_API_KEY to enable TypeSafe Jev through Liara.")

    base = (api_base or os.getenv("LIARA_API_BASE") or LIARA_DEFAULT_API_BASE).rstrip(
        "/"
    )
    payload = {
        "model": LIARA_JEV_MODEL_ID,
        "state": request.state,
        "questions": request.questions,
    }
    url = f"{base}/decisions"
    headers = {"Authorization": f"Bearer {api_key}"}
    if transport is not None:
        # Custom transport (tests): ephemeral client, unchanged behavior.
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
    else:
        # Hot path: shared keepalive client, no per-call handshake.
        client = await _get_shared_jev_client()
        response = await client.post(
            url, headers=headers, json=payload, timeout=timeout
        )
        response.raise_for_status()
        result = response.json()

    if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
        raise ValueError("Liara Jev returned an invalid decision response.")
    return result
