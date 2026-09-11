"""
Structured event logging to Postgres.

This is the data the Phase 4 AI layer will consume, so every row carries
enough to reconstruct what happened: timing, upstream, status code, circuit
state before/after, and the full retry attempt sequence (as JSONB) rather
than just the final outcome.

Logging is deliberately best-effort: a failure to write to Postgres must
never fail the actual gateway request it's describing. Every call here is
wrapped so an exception is caught and swallowed (with the request still
succeeding) rather than propagated -- observability is not allowed to
become a new failure mode for the thing it's observing.
"""
import json
import logging

from app.db import acquire

logger = logging.getLogger("gateway.event_log")


async def log_request_event(
    *,
    client_id: str,
    primary_upstream: str,
    routed_to: str | None,
    skipped_open_upstreams: list[str],
    attempts: list[dict],
    upstream_status_code: int | None,
    success: bool,
    rate_limited: bool,
    circuit_open: bool,
    circuit_state_before: str | None,
    circuit_state_after: str | None,
    latency_ms: float | None,
    error: str | None = None,
) -> None:
    try:
        async with acquire() as conn:
            await conn.execute(
                """
                INSERT INTO gateway_events (
                    client_id, primary_upstream, routed_to, skipped_open_upstreams,
                    attempts, retry_count, upstream_status_code, success, rate_limited,
                    circuit_open, circuit_state_before, circuit_state_after, latency_ms, error
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                """,
                client_id,
                primary_upstream,
                routed_to,
                json.dumps(skipped_open_upstreams),
                json.dumps(attempts),
                len(attempts),
                upstream_status_code,
                success,
                rate_limited,
                circuit_open,
                circuit_state_before,
                circuit_state_after,
                latency_ms,
                error,
            )
    except Exception:  # noqa: BLE001 - logging must never break the request path
        logger.exception("failed to log gateway event")


async def log_circuit_transition(
    *, upstream: str, from_state: str, to_state: str, consecutive_failures: int
) -> None:
    try:
        async with acquire() as conn:
            await conn.execute(
                """
                INSERT INTO circuit_state_changes (upstream, from_state, to_state, consecutive_failures)
                VALUES ($1, $2, $3, $4)
                """,
                upstream,
                from_state,
                to_state,
                consecutive_failures,
            )
    except Exception:  # noqa: BLE001
        logger.exception("failed to log circuit transition")
