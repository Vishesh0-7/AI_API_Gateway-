"""
Read endpoints backing the dashboard: aggregated success/failure rates,
recent event feed, and circuit state transition history. All read from the
gateway_events / circuit_state_changes tables written by
app/core/event_log.py -- nothing here is on the request hot path.
"""
from fastapi import APIRouter, Query

from app.db import acquire

router = APIRouter()


@router.get("/stats/summary")
async def stats_summary(window_seconds: int = Query(default=300, ge=1)):
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                COALESCE(routed_to, primary_upstream) AS upstream,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE success) AS successes,
                COUNT(*) FILTER (WHERE NOT success) AS failures,
                COUNT(*) FILTER (WHERE rate_limited) AS rate_limited_count,
                AVG(latency_ms) AS avg_latency_ms,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
                PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99_latency_ms
            FROM gateway_events
            WHERE ts > now() - ($1 || ' seconds')::interval
            GROUP BY COALESCE(routed_to, primary_upstream)
            ORDER BY upstream
            """,
            str(window_seconds),
        )
    return [
        {
            "upstream": r["upstream"],
            "total": r["total"],
            "successes": r["successes"],
            "failures": r["failures"],
            "rate_limited_count": r["rate_limited_count"],
            "success_rate": round(r["successes"] / r["total"], 4) if r["total"] else None,
            "avg_latency_ms": round(r["avg_latency_ms"], 2) if r["avg_latency_ms"] is not None else None,
            "p95_latency_ms": round(r["p95_latency_ms"], 2) if r["p95_latency_ms"] is not None else None,
            "p99_latency_ms": round(r["p99_latency_ms"], 2) if r["p99_latency_ms"] is not None else None,
        }
        for r in rows
    ]


@router.get("/events/recent")
async def events_recent(limit: int = Query(default=50, ge=1, le=500)):
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ts, client_id, primary_upstream, routed_to, upstream_status_code,
                   success, rate_limited, circuit_open, circuit_state_before,
                   circuit_state_after, retry_count, latency_ms, error
            FROM gateway_events
            ORDER BY ts DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


@router.get("/circuits/history")
async def circuit_history(limit: int = Query(default=100, ge=1, le=1000)):
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ts, upstream, from_state, to_state, consecutive_failures
            FROM circuit_state_changes
            ORDER BY ts DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]
