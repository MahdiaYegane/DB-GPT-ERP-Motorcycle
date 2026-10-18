"""API endpoint for TypeSafe Jev decisions hosted by Liara."""

import httpx
from fastapi import APIRouter, HTTPException

from dbgpt_app.openapi.api_view_model import Result

from .liara_jev import LIARA_JEV_MODEL_ID, LiaraJevDecisionRequest, evaluate_liara_jev

router = APIRouter()


@router.get("/v1/liara/jev/status", response_model=Result[dict])
async def liara_jev_status():
    """Report the Jev guard configuration (no Liara call, no cost).

    Used by the standalone JEV button in the web UI to show the live guard
    state: model id, auto-triage / verification / fallback switches and
    their timeouts. Lazy import keeps router import light and avoids any
    import cycle with the agent modules.
    """
    from .liara_jev_auto import (
        get_jev_auto_timeout,
        get_jev_report_threshold,
        get_jev_verify_timeout,
        is_jev_auto_enabled,
        is_jev_fallback_enabled,
        is_jev_verify_enabled,
    )

    return Result.succ(
        {
            "model": LIARA_JEV_MODEL_ID,
            "decisions_endpoint": "/api/v1/liara/jev/decisions",
            "auto_triage_enabled": is_jev_auto_enabled(),
            "auto_triage_timeout": get_jev_auto_timeout(),
            "verify_enabled": is_jev_verify_enabled(),
            "verify_timeout": get_jev_verify_timeout(),
            "fallback_report_enabled": is_jev_fallback_enabled(),
            "report_threshold": get_jev_report_threshold(),
        }
    )


@router.post("/v1/liara/jev/decisions", response_model=Result[dict])
async def liara_jev_decisions(request: LiaraJevDecisionRequest):
    """Run a structured Jev decision; this is separate from chat completions."""
    try:
        result = await evaluate_liara_jev(
            state=request.state,
            questions=request.questions,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Liara Jev returned HTTP {exc.response.status_code}.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="Could not reach Liara Jev."
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Result.succ(result)
