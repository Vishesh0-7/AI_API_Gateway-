"""
Chaos control: drive the mock upstreams' /control endpoint to simulate
failure scenarios on demand.

Scenarios:
  outage              - upstream returns 500 for everything (mode=error)
  intermittent        - upstream fails a fraction of requests (mode=flaky)
  rate_limit_storm    - upstream returns 429 for everything (mode=rate_limited)
  degrade             - latency ramps linearly from start_ms to end_ms over
                         duration_seconds, in discrete steps, via a
                         background asyncio task (mode=slow, slow_latency_ms
                         updated on each step)
  reset               - back to mode=normal, and cancels any in-flight
                         degrade task for that upstream

Why a background task for "degrade" specifically: it's the only scenario
that isn't a single state flip -- it's a *trajectory* over time. Modeling
it as a task that owns its own sleep/step loop (rather than, say, the
caller polling) keeps the ramp's timing accurate and lets `reset` cancel it
cleanly via asyncio.Task.cancel().
"""
import asyncio

import httpx

from app.config import get_settings
from app.redis_client import get_redis

_degrade_tasks: dict[str, asyncio.Task] = {}


def _cancel_degrade_task(upstream: str) -> None:
    task = _degrade_tasks.pop(upstream, None)
    if task is not None and not task.done():
        task.cancel()


async def _set_control(upstream: str, payload: dict) -> dict:
    settings = get_settings()
    base_url = settings.upstreams[upstream]
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(f"{base_url}/control", json=payload)
        resp.raise_for_status()
        return resp.json()


async def trigger_outage(upstream: str) -> dict:
    _cancel_degrade_task(upstream)
    return await _set_control(upstream, {"mode": "error"})


async def trigger_intermittent(upstream: str, failure_rate: float, seed: int | None = None) -> dict:
    _cancel_degrade_task(upstream)
    payload = {"mode": "flaky", "flaky_rate": failure_rate}
    if seed is not None:
        payload["seed"] = seed
    return await _set_control(upstream, payload)


async def reset_gateway_state(upstream: str, client_id: str) -> None:
    """
    Clear this upstream's persisted circuit breaker state and this
    client's rate limit bucket for it, so a benchmark run starts from a
    known-clean CLOSED/full-bucket state rather than carrying over state
    from whatever ran before it.
    """
    redis = get_redis()
    await redis.delete(f"cb:{upstream}", f"rl:{client_id}:{upstream}")


async def trigger_rate_limit_storm(upstream: str) -> dict:
    _cancel_degrade_task(upstream)
    return await _set_control(upstream, {"mode": "rate_limited"})


async def reset(upstream: str) -> dict:
    _cancel_degrade_task(upstream)
    return await _set_control(upstream, {"mode": "normal"})


async def _degrade_loop(upstream: str, start_ms: int, end_ms: int, duration_seconds: float, steps: int):
    await _set_control(upstream, {"mode": "slow", "slow_latency_ms": start_ms})
    step_interval = duration_seconds / steps
    for i in range(1, steps + 1):
        await asyncio.sleep(step_interval)
        latency = int(start_ms + (end_ms - start_ms) * (i / steps))
        await _set_control(upstream, {"slow_latency_ms": latency})


async def trigger_degrade(
    upstream: str, start_ms: int, end_ms: int, duration_seconds: float, steps: int = 10
) -> dict:
    _cancel_degrade_task(upstream)
    task = asyncio.create_task(_degrade_loop(upstream, start_ms, end_ms, duration_seconds, steps))
    _degrade_tasks[upstream] = task
    return {
        "upstream": upstream,
        "scenario": "degrade",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "duration_seconds": duration_seconds,
        "steps": steps,
    }


async def get_status() -> dict:
    settings = get_settings()
    result = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, base_url in settings.upstreams.items():
            resp = await client.get(f"{base_url}/control")
            result[name] = resp.json()
            result[name]["degrade_active"] = name in _degrade_tasks and not _degrade_tasks[name].done()
    return result
