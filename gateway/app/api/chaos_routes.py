"""
Chaos control API. Not part of the gateway's data path -- this is an
operator/testing surface for triggering failure scenarios against the mock
upstreams on demand (used by the Phase 3 benchmark script and, ad hoc, by a
human or the dashboard).
"""
from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.core import chaos

router = APIRouter(prefix="/chaos")
settings = get_settings()


def _check_upstream(upstream: str) -> None:
    if upstream not in settings.upstreams:
        raise HTTPException(404, f"unknown upstream: {upstream}")


@router.post("/{upstream}/outage")
async def outage(upstream: str):
    _check_upstream(upstream)
    return await chaos.trigger_outage(upstream)


@router.post("/{upstream}/intermittent")
async def intermittent(upstream: str, failure_rate: float = 0.5, seed: int | None = None):
    _check_upstream(upstream)
    if not 0 <= failure_rate <= 1:
        raise HTTPException(400, "failure_rate must be between 0 and 1")
    return await chaos.trigger_intermittent(upstream, failure_rate, seed)


@router.post("/{upstream}/rate_limit_storm")
async def rate_limit_storm(upstream: str):
    _check_upstream(upstream)
    return await chaos.trigger_rate_limit_storm(upstream)


@router.post("/{upstream}/degrade")
async def degrade(
    upstream: str,
    start_ms: int = 50,
    end_ms: int = 3000,
    duration_seconds: float = 30.0,
    steps: int = 10,
):
    _check_upstream(upstream)
    return await chaos.trigger_degrade(upstream, start_ms, end_ms, duration_seconds, steps)


@router.post("/{upstream}/reset")
async def reset(upstream: str):
    _check_upstream(upstream)
    return await chaos.reset(upstream)


@router.post("/{upstream}/reset_gateway_state")
async def reset_gateway_state(upstream: str, client_id: str = "benchmark"):
    """Clear this upstream's circuit breaker + this client's rate limit
    bucket in Redis, without touching the upstream's own /control mode.
    Used by the Phase 3 benchmark to start each run from a clean slate."""
    _check_upstream(upstream)
    await chaos.reset_gateway_state(upstream, client_id)
    return {"upstream": upstream, "client_id": client_id, "reset": True}


@router.post("/reset_all")
async def reset_all():
    return {name: await chaos.reset(name) for name in settings.upstreams}


@router.get("/status")
async def status():
    return await chaos.get_status()
