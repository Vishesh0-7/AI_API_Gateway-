"""
Phase 4 AI analysis API. Entirely out-of-band from the gateway's request
path -- these endpoints are triggered on demand (by an operator, the
dashboard, or the Phase 4 benchmark script), never from /proxy.

  POST /ai/analyze/{upstream}?window_minutes=10 - run analysis now, store
      + return the report.
  GET  /ai/reports?upstream=...&limit=20        - list past reports.
  GET  /ai/reports/{id}                          - one report.
  POST /ai/reports/{id}/apply                    - operator accepts the
      suggested circuit breaker threshold; applies it via a per-upstream
      Redis override (app/core/circuit_config.py). Never automatic.
  GET  /circuit_config/{upstream}                - effective (override or
      default) circuit breaker config for an upstream.
  DELETE /circuit_config/{upstream}              - clear an override,
      reverting to the global defaults.
"""
import json

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.core import circuit_config
from app.core.ai_analyzer import analyze_upstream
from app.db import acquire
from app.redis_client import get_redis

router = APIRouter()
settings = get_settings()


def _check_upstream(upstream: str) -> None:
    if upstream not in settings.upstreams:
        raise HTTPException(404, f"unknown upstream: {upstream}")


@router.post("/ai/analyze/{upstream}")
async def analyze(upstream: str, window_minutes: int = Query(default=10, ge=1, le=1440)):
    _check_upstream(upstream)
    current_cb_config = await circuit_config.get_effective_config(get_redis(), upstream, settings)

    analysis, feature_summary = await analyze_upstream(upstream, window_minutes, current_cb_config)

    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ai_incident_reports (
                upstream, window_minutes, failure_type, confidence, summary,
                should_adjust, suggested_failure_threshold, suggested_recovery_timeout_seconds,
                suggestion_reasoning, raw_feature_summary
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING id, ts
            """,
            upstream,
            window_minutes,
            analysis.failure_type.value,
            analysis.confidence,
            analysis.summary,
            analysis.circuit_breaker_suggestion.should_adjust,
            analysis.circuit_breaker_suggestion.suggested_failure_threshold,
            analysis.circuit_breaker_suggestion.suggested_recovery_timeout_seconds,
            analysis.circuit_breaker_suggestion.reasoning,
            json.dumps(feature_summary),
        )

    return {
        "id": row["id"],
        "ts": row["ts"].isoformat(),
        "upstream": upstream,
        "window_minutes": window_minutes,
        "failure_type": analysis.failure_type.value,
        "confidence": analysis.confidence,
        "summary": analysis.summary,
        "circuit_breaker_suggestion": analysis.circuit_breaker_suggestion.model_dump(),
        "applied": False,
    }


@router.get("/ai/reports")
async def list_reports(upstream: str | None = None, limit: int = Query(default=20, ge=1, le=200)):
    async with acquire() as conn:
        if upstream:
            rows = await conn.fetch(
                """
                SELECT id, ts, upstream, window_minutes, failure_type, confidence, summary,
                       should_adjust, suggested_failure_threshold, suggested_recovery_timeout_seconds,
                       suggestion_reasoning, applied, applied_at
                FROM ai_incident_reports WHERE upstream = $1 ORDER BY ts DESC LIMIT $2
                """,
                upstream,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, ts, upstream, window_minutes, failure_type, confidence, summary,
                       should_adjust, suggested_failure_threshold, suggested_recovery_timeout_seconds,
                       suggestion_reasoning, applied, applied_at
                FROM ai_incident_reports ORDER BY ts DESC LIMIT $1
                """,
                limit,
            )
    return [dict(r) for r in rows]


@router.get("/ai/reports/{report_id}")
async def get_report(report_id: int):
    async with acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM ai_incident_reports WHERE id = $1", report_id)
    if row is None:
        raise HTTPException(404, f"no report with id {report_id}")
    return dict(row)


@router.post("/ai/reports/{report_id}/apply")
async def apply_report(report_id: int):
    """
    Explicit operator action: accept this report's suggested circuit
    breaker threshold and apply it as a per-upstream override. No endpoint
    in this codebase calls this automatically -- see the module docstring.
    """
    async with acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM ai_incident_reports WHERE id = $1", report_id)
        if row is None:
            raise HTTPException(404, f"no report with id {report_id}")
        if not row["should_adjust"]:
            raise HTTPException(400, "this report did not suggest a change to apply")

        await circuit_config.set_override(
            get_redis(),
            row["upstream"],
            failure_threshold=row["suggested_failure_threshold"],
            recovery_timeout_seconds=row["suggested_recovery_timeout_seconds"],
        )
        await conn.execute(
            "UPDATE ai_incident_reports SET applied = TRUE, applied_at = now() WHERE id = $1",
            report_id,
        )

    effective = await circuit_config.get_effective_config(get_redis(), row["upstream"], settings)
    return {"report_id": report_id, "upstream": row["upstream"], "applied": True, "effective_config": effective}


@router.get("/circuit_config/{upstream}")
async def get_circuit_config(upstream: str):
    _check_upstream(upstream)
    return await circuit_config.get_effective_config(get_redis(), upstream, settings)


@router.delete("/circuit_config/{upstream}")
async def clear_circuit_config(upstream: str):
    _check_upstream(upstream)
    await circuit_config.clear_override(get_redis(), upstream)
    effective = await circuit_config.get_effective_config(get_redis(), upstream, settings)
    return {"upstream": upstream, "cleared": True, "effective_config": effective}
