"""
Postgres connection pool + schema bootstrap.

Uses asyncpg directly (no ORM) since the write pattern here is simple
append-only event logging plus a handful of read-aggregation queries for
the dashboard -- an ORM would add indirection without buying much. Schema
creation is a plain `CREATE TABLE IF NOT EXISTS` run at startup rather than
a migration framework (e.g. Alembic), which is a reasonable simplification
for a portfolio project with one, append-mostly schema; a real production
service handling schema evolution over time would want migrations instead.
"""
from contextlib import asynccontextmanager

import asyncpg

from app.config import get_settings

_pool: asyncpg.Pool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_events (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    client_id TEXT NOT NULL,
    primary_upstream TEXT NOT NULL,
    routed_to TEXT,
    skipped_open_upstreams JSONB NOT NULL DEFAULT '[]',
    attempts JSONB NOT NULL DEFAULT '[]',
    retry_count INT NOT NULL DEFAULT 0,
    upstream_status_code INT,
    success BOOLEAN NOT NULL,
    rate_limited BOOLEAN NOT NULL DEFAULT FALSE,
    circuit_open BOOLEAN NOT NULL DEFAULT FALSE,
    circuit_state_before TEXT,
    circuit_state_after TEXT,
    latency_ms DOUBLE PRECISION,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_gateway_events_ts ON gateway_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_gateway_events_upstream ON gateway_events (routed_to);

CREATE TABLE IF NOT EXISTS circuit_state_changes (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    upstream TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    consecutive_failures INT
);
CREATE INDEX IF NOT EXISTS idx_circuit_state_changes_ts ON circuit_state_changes (ts DESC);

-- Phase 4: AI-generated incident analyses. Written only by an explicit
-- POST /ai/analyze/{upstream} call (operator- or benchmark-triggered),
-- never from the request path. `applied` tracks whether an operator
-- accepted the suggested circuit breaker threshold via
-- POST /ai/reports/{id}/apply -- suggestions are never auto-applied.
CREATE TABLE IF NOT EXISTS ai_incident_reports (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    upstream TEXT NOT NULL,
    window_minutes INT NOT NULL,
    failure_type TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    summary TEXT NOT NULL,
    should_adjust BOOLEAN NOT NULL DEFAULT FALSE,
    suggested_failure_threshold INT,
    suggested_recovery_timeout_seconds DOUBLE PRECISION,
    suggestion_reasoning TEXT,
    raw_feature_summary JSONB NOT NULL,
    applied BOOLEAN NOT NULL DEFAULT FALSE,
    applied_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ai_incident_reports_ts ON ai_incident_reports (ts DESC);
CREATE INDEX IF NOT EXISTS idx_ai_incident_reports_upstream ON ai_incident_reports (upstream);
"""


async def init_pool() -> None:
    global _pool
    settings = get_settings()
    _pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=10)
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("db pool not initialized -- call init_pool() at startup")
    return _pool


@asynccontextmanager
async def acquire():
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn
