"""
ERP Motorcycle - HTTP health endpoints (read-only, non-blocking).

Exposed (when mounted, see dbgpt_server.mount_routers):
    GET /api/v1/erp/health  Full check: SELECT 1 / VERSION / DATABASE / USER.
                            200 when the database is reachable, 503 otherwise.
                            Never exposes credentials (password always masked).
    GET /api/v1/erp/ping    Lightweight check: SELECT 1 only. Same status rules.

Both run the blocking SQLAlchemy calls in a worker thread so the event
loop is never blocked, and both are safe to call when the database is
down (they return a categorized FAIL payload instead of raising).
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from erp.database import ping_database
from erp.health import run_health_check

logger = logging.getLogger(__name__)

router = APIRouter()

# Upper bound for a single HTTP health probe. The DB connect_timeout is 5s,
# so 15s comfortably covers connect + 4 small SELECTs while guaranteeing
# the endpoint always answers promptly even on a black-holed network.
HEALTH_HTTP_TIMEOUT = 15
PING_HTTP_TIMEOUT = 10


async def _run_blocking(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(fn, *args, **kwargs))


def _status_code(ok: bool) -> int:
    return 200 if ok else 503


@router.get("/health", summary="ERP MariaDB health (full, read-only)")
async def erp_health():
    """Full ERP database health check with failure categories.

    200: {"ok": true, "category": "success", ...}
    503: {"ok": false, "category": "<server_unavailable|timeout|...>", ...}
    """
    try:
        result = await asyncio.wait_for(
            _run_blocking(run_health_check, timeout=HEALTH_HTTP_TIMEOUT),
            timeout=HEALTH_HTTP_TIMEOUT + 5,
        )
        payload = result.to_dict()
    except asyncio.TimeoutError:
        logger.warning("ERP /health HTTP timeout after %ss", HEALTH_HTTP_TIMEOUT + 5)
        payload = {
            "ok": False,
            "category": "timeout",
            "error": f"health endpoint timed out after {HEALTH_HTTP_TIMEOUT + 5}s",
        }
    except Exception as exc:  # never leak a 500 without a categorized body
        logger.exception("ERP /health unexpected error")
        payload = {"ok": False, "category": "query_failure", "error": str(exc)[:500]}
    return JSONResponse(status_code=_status_code(payload.get("ok", False)), content=payload)


@router.get("/ping", summary="ERP MariaDB ping (SELECT 1 only)")
async def erp_ping():
    """Lightweight liveness probe. Same 200/503 contract as /health."""
    try:
        payload = await asyncio.wait_for(
            _run_blocking(ping_database, PING_HTTP_TIMEOUT),
            timeout=PING_HTTP_TIMEOUT + 5,
        )
    except asyncio.TimeoutError:
        payload = {"ok": False, "category": "timeout", "error": "ping timed out", "latency_ms": None}
    except Exception as exc:
        logger.exception("ERP /ping unexpected error")
        payload = {"ok": False, "category": "query_failure", "error": str(exc)[:500], "latency_ms": None}
    return JSONResponse(status_code=_status_code(payload.get("ok", False)), content=payload)
