"""API endpoint for TypeSafe Jev decisions hosted by Liara."""

import httpx
from fastapi import APIRouter, HTTPException

from dbgpt_app.openapi.api_view_model import Result

from .liara_jev import LiaraJevDecisionRequest, evaluate_liara_jev

router = APIRouter()


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
