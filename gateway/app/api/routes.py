"""
Gateway HTTP API.

Endpoints:
  POST /proxy/{primary}   - send a request through the gateway (rate limit ->
                             routing/failover -> retry -> circuit breaker) to
                             the given primary upstream, optionally falling
                             back to other upstreams if the primary's circuit
                             is open.
  GET  /circuit/{upstream} - current circuit breaker state for one upstream.
  GET  /circuits            - circuit breaker state for all known upstreams.
  GET  /ratelimit/{client_id}/{upstream} - current token bucket status.

Every call to /proxy logs one row to Postgres (gateway_events) describing
the full decision: routing, retry attempts, and circuit state before/after.
Logging is fired as a background asyncio task rather than awaited inline,
so a slow or failing Postgres write can never add latency to (or fail) the
actual proxied request -- see app/core/event_log.py for the same principle
applied at the write layer (exceptions are swallowed there too).
"""
import asyncio
import time

import httpx
from fastapi import APIRouter, Header, HTTPException, Query

from app.config import get_settings
from app.core import circuit_breaker_store, circuit_config, router as routing
from app.core.event_log import log_circuit_transition, log_request_event
from app.core.rate_limiter import RateLimiter
from app.core.retry import RetryExhaustedError, RetryPolicy, is_retryable_status, run_with_retry
from app.redis_client import get_redis

router = APIRouter()
settings = get_settings()
rate_limiter = RateLimiter(
    get_redis(), settings.rate_limit_capacity, settings.rate_limit_refill_per_second
)


async def _load_cb(upstream: str):
    cfg = await circuit_config.get_effective_config(get_redis(), upstream, settings)
    return await circuit_breaker_store.load(
        get_redis(),
        upstream,
        cfg["failure_threshold"],
        cfg["recovery_timeout_seconds"],
        cfg["half_open_max_calls"],
    )


@router.post("/proxy/{primary}")
async def proxy(
    primary: str,
    fallbacks: list[str] = Query(default=[]),
    x_client_id: str = Header(default="anonymous"),
):
    if primary not in settings.upstreams:
        raise HTTPException(404, f"unknown upstream: {primary}")
    fallbacks = fallbacks or []
    for f in fallbacks:
        if f not in settings.upstreams:
            raise HTTPException(404, f"unknown upstream: {f}")

    allowed, tokens_remaining = await rate_limiter.check(x_client_id, primary)
    if not allowed:
        asyncio.create_task(
            log_request_event(
                client_id=x_client_id,
                primary_upstream=primary,
                routed_to=None,
                skipped_open_upstreams=[],
                attempts=[],
                upstream_status_code=None,
                success=False,
                rate_limited=True,
                circuit_open=False,
                circuit_state_before=None,
                circuit_state_after=None,
                latency_ms=None,
                error="rate_limit_exceeded",
            )
        )
        raise HTTPException(429, f"rate limit exceeded for client={x_client_id} upstream={primary}")

    candidates = [primary, *fallbacks]
    breakers = {name: await _load_cb(name) for name in candidates}
    circuit_state_before = breakers[primary].state.value

    # choose_upstream() calls cb.allow_request() on each candidate in turn,
    # which is also what performs the OPEN -> HALF_OPEN transition once the
    # recovery timeout has elapsed -- see the note in router.py. Whichever
    # breaker it picks may have just been mutated by that transition, so it
    # must be persisted below regardless of the final success/failure outcome.
    decision = routing.choose_upstream(primary, fallbacks, breakers)
    if decision.chosen_upstream is None:
        asyncio.create_task(
            log_request_event(
                client_id=x_client_id,
                primary_upstream=primary,
                routed_to=None,
                skipped_open_upstreams=decision.skipped_open_upstreams,
                attempts=[],
                upstream_status_code=None,
                success=False,
                rate_limited=False,
                circuit_open=True,
                circuit_state_before=circuit_state_before,
                circuit_state_after=circuit_state_before,
                latency_ms=None,
                error="all_candidates_circuit_open",
            )
        )
        raise HTTPException(
            503,
            f"all candidate upstreams are circuit-open: {[primary, *fallbacks]}",
        )

    chosen = decision.chosen_upstream
    cb = decision.chosen_breaker
    chosen_state_before = breakers[chosen].state.value if chosen != primary else circuit_state_before
    base_url = settings.upstreams[chosen]
    policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay_seconds=settings.retry_base_delay_seconds,
        max_delay_seconds=settings.retry_max_delay_seconds,
    )

    attempts_log: list[dict] = []

    async def do_request():
        async with httpx.AsyncClient(timeout=settings.upstream_timeout_seconds) as client:
            return await client.get(f"{base_url}/work")

    def record_attempt(attempt: int, result: httpx.Response | None, exc: Exception | None):
        attempts_log.append(
            {
                "attempt": attempt + 1,
                "status_code": result.status_code if result is not None else None,
                "error": str(exc) if exc else None,
            }
        )

    start = time.perf_counter()
    try:
        response = await run_with_retry(
            do_request,
            policy,
            should_retry=lambda r: is_retryable_status(r.status_code),
            on_attempt=record_attempt,
        )
        success = response.status_code < 400
    except RetryExhaustedError:
        success = False
        response = None

    elapsed_ms = (time.perf_counter() - start) * 1000

    state_before_call = cb.state
    if success:
        cb.on_success()
    else:
        cb.on_failure()
    state_after_call = cb.state
    await circuit_breaker_store.save(get_redis(), chosen, cb)

    if state_before_call != state_after_call:
        asyncio.create_task(
            log_circuit_transition(
                upstream=chosen,
                from_state=state_before_call.value,
                to_state=state_after_call.value,
                consecutive_failures=cb.consecutive_failures,
            )
        )

    asyncio.create_task(
        log_request_event(
            client_id=x_client_id,
            primary_upstream=primary,
            routed_to=chosen,
            skipped_open_upstreams=decision.skipped_open_upstreams,
            attempts=attempts_log,
            upstream_status_code=response.status_code if response is not None else None,
            success=success,
            rate_limited=False,
            circuit_open=False,
            circuit_state_before=chosen_state_before,
            circuit_state_after=cb.state.value,
            latency_ms=round(elapsed_ms, 2),
            error=None if response is not None else "retry_exhausted_with_exception",
        )
    )

    result_body = {
        "client_id": x_client_id,
        "primary_upstream": primary,
        "routed_to": chosen,
        "skipped_open_upstreams": decision.skipped_open_upstreams,
        "attempts": attempts_log,
        "circuit_state_after": cb.state.value,
        "rate_limit_tokens_remaining": tokens_remaining,
        "elapsed_ms": round(elapsed_ms, 2),
    }

    if response is None:
        result_body["upstream_status_code"] = None
        result_body["success"] = False
        raise HTTPException(502, detail=result_body)

    result_body["upstream_status_code"] = response.status_code
    result_body["upstream_body"] = response.text
    result_body["success"] = success

    if not success:
        # The upstream call completed (we got a response) but it was a
        # failure that survived every retry attempt -- surface that as a
        # gateway-level failure (502) rather than a 200, even though the
        # underlying HTTP call itself didn't raise. Returning 200 here would
        # hide real failures from callers and from the benchmark script in
        # Phase 3, which measures availability by status code.
        raise HTTPException(502, detail=result_body)

    return result_body


@router.get("/circuit/{upstream}")
async def circuit_status(upstream: str):
    if upstream not in settings.upstreams:
        raise HTTPException(404, f"unknown upstream: {upstream}")
    cb = await _load_cb(upstream)
    return {"upstream": upstream, **cb.snapshot()}


@router.get("/circuits")
async def all_circuit_status():
    return {name: (await _load_cb(name)).snapshot() for name in settings.upstreams}


@router.get("/ratelimit/{client_id}/{upstream}")
async def rate_limit_status(client_id: str, upstream: str):
    if upstream not in settings.upstreams:
        raise HTTPException(404, f"unknown upstream: {upstream}")
    return await rate_limiter.status(client_id, upstream)
