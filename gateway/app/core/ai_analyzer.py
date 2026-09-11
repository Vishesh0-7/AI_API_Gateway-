"""
AI-assisted failure analysis (Phase 4).

Deliberately out-of-band: this module is never called from the synchronous
/proxy request path (see app/api/routes.py -- nothing there imports this
file). It is invoked on-demand via POST /ai/analyze/{upstream}, or by the
Phase 4 benchmark script, and:

  1. Reads a window of already-logged gateway_events / circuit_state_changes
     from Postgres and reduces it to a compact statistical feature summary
     (status code histogram, latency percentiles, a coarse latency trend
     across the window, max consecutive-failure streak, circuit
     transitions) -- this feature extraction is the "pattern recognition
     from status codes/latency/timing" the classification is grounded in,
     not an explicit error message (none is included on purpose).
  2. Sends that summary to a free, open-source model hosted on Groq
     (gpt-oss-120b) in JSON mode, then validates the response against a
     Pydantic output schema, getting back a structured failure-type
     classification, a plain-English summary, and an optional circuit
     breaker threshold suggestion.
  3. Never applies the suggestion itself -- see app/api/ai_routes.py's
     POST /ai/reports/{id}/apply, which is a separate, explicit operator
     action.
"""
import json
from enum import Enum

from groq import AsyncGroq
from pydantic import BaseModel, Field

from app.db import acquire

MODEL = "openai/gpt-oss-120b"


class FailureType(str, Enum):
    OUTAGE = "outage"
    RATE_LIMITING = "rate_limiting"
    DEGRADATION = "degradation"
    TRANSIENT_BLIP = "transient_blip"
    HEALTHY = "healthy"


class CircuitBreakerSuggestion(BaseModel):
    should_adjust: bool = Field(description="Whether a threshold change is worth suggesting at all")
    suggested_failure_threshold: int | None = Field(
        default=None,
        description="Suggested consecutive-failure count to trip the breaker, only if should_adjust is true",
    )
    suggested_recovery_timeout_seconds: float | None = Field(
        default=None,
        description="Suggested seconds to wait before probing recovery, only if should_adjust is true",
    )
    reasoning: str = Field(description="One or two sentences explaining the suggestion, or why no change is needed")


class IncidentAnalysis(BaseModel):
    failure_type: FailureType
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(
        description="Plain-English incident summary, 2-4 sentences, written for an on-call engineer"
    )
    circuit_breaker_suggestion: CircuitBreakerSuggestion


async def extract_feature_summary(upstream: str, window_minutes: int) -> dict:
    async with acquire() as conn:
        events = await conn.fetch(
            """
            SELECT ts, upstream_status_code, success, rate_limited, latency_ms, retry_count
            FROM gateway_events
            WHERE routed_to = $1 AND ts > now() - ($2 || ' minutes')::interval
            ORDER BY ts
            """,
            upstream,
            str(window_minutes),
        )
        transitions = await conn.fetch(
            """
            SELECT ts, from_state, to_state, consecutive_failures
            FROM circuit_state_changes
            WHERE upstream = $1 AND ts > now() - ($2 || ' minutes')::interval
            ORDER BY ts
            """,
            upstream,
            str(window_minutes),
        )

    total = len(events)
    if total == 0:
        return {"upstream": upstream, "window_minutes": window_minutes, "total_requests": 0}

    status_histogram: dict[str, int] = {}
    latencies: list[float] = []
    failure_streak = 0
    max_failure_streak = 0
    for e in events:
        code = str(e["upstream_status_code"]) if e["upstream_status_code"] is not None else "none"
        status_histogram[code] = status_histogram.get(code, 0) + 1
        if e["latency_ms"] is not None:
            latencies.append(e["latency_ms"])
        if e["success"]:
            failure_streak = 0
        else:
            failure_streak += 1
            max_failure_streak = max(max_failure_streak, failure_streak)

    latencies_sorted = sorted(latencies)

    def pct(p: float) -> float | None:
        if not latencies_sorted:
            return None
        k = int((len(latencies_sorted) - 1) * p)
        return round(latencies_sorted[k], 1)

    # Coarse time-bucketed latency trend across the window -- this is what
    # lets the model tell "flat, always failing fast" (outage) apart from
    # "latency climbing over time" (degradation) from shape alone.
    n = len(latencies)
    thirds = [latencies[: n // 3], latencies[n // 3 : 2 * n // 3], latencies[2 * n // 3 :]] if n else []
    latency_trend = [round(sum(t) / len(t), 1) if t else None for t in thirds]

    return {
        "upstream": upstream,
        "window_minutes": window_minutes,
        "total_requests": total,
        "success_count": sum(1 for e in events if e["success"]),
        "failure_count": sum(1 for e in events if not e["success"]),
        "rate_limited_count": sum(1 for e in events if e["rate_limited"]),
        "status_code_histogram": status_histogram,
        "max_consecutive_failure_streak": max_failure_streak,
        "avg_retry_count": round(sum(e["retry_count"] for e in events) / total, 2),
        "latency_ms": {
            "avg": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "p50": pct(0.50),
            "p95": pct(0.95),
            "p99": pct(0.99),
            "trend_early_mid_late_avg": latency_trend,
        },
        "circuit_state_transitions": [
            {
                "ts": t["ts"].isoformat(),
                "from_state": t["from_state"],
                "to_state": t["to_state"],
                "consecutive_failures": t["consecutive_failures"],
            }
            for t in transitions
        ],
        "window_start": events[0]["ts"].isoformat(),
        "window_end": events[-1]["ts"].isoformat(),
    }


def _build_system_prompt() -> str:
    return (
        "You are an SRE assistant. Respond with a single JSON object only, no "
        "prose, no markdown fences, matching exactly this schema:\n"
        + json.dumps(IncidentAnalysis.model_json_schema(), indent=2)
    )


def _build_prompt(feature_summary: dict, current_cb_config: dict) -> str:
    return (
        "You are analyzing failure telemetry from an API reliability gateway for one "
        "upstream over a recent time window. Classify the failure pattern from the "
        "statistical shape of the data (status code distribution, latency trend across "
        "the window, consecutive-failure streaks, circuit breaker transitions) -- not from "
        "any explicit error message, since none is included here on purpose.\n\n"
        "Failure type definitions:\n"
        "- outage: requests fail hard and consistently (5xx-dominated), latency stays low "
        "(fast failures), little to no variance over the window.\n"
        "- rate_limiting: failures are dominated by 429 status codes.\n"
        "- degradation: latency trends upward over the window "
        "(see latency_ms.trend_early_mid_late_avg), with or without failures appearing once "
        "latency crosses a timeout.\n"
        "- transient_blip: failures are present but a minority of requests, scattered rather "
        "than forming one long consecutive streak, and/or the window ends healthy.\n"
        "- healthy: negligible or no failures.\n\n"
        f"Current circuit breaker config for this upstream: {json.dumps(current_cb_config)}\n\n"
        f"Feature summary:\n{json.dumps(feature_summary, indent=2)}\n\n"
        "Write a plain-English incident summary a human on-call engineer would want to read "
        "(2-4 sentences: what happened, roughly when within the window, and the practical "
        "impact). Then decide whether the circuit breaker's failure_threshold or "
        "recovery_timeout_seconds should change given what you observed, and if so, suggest "
        "specific new values with reasoning grounded in the data above. Only suggest a change "
        "if the data clearly supports one -- 'no change needed' is a valid and often correct "
        "answer, especially for transient_blip or healthy."
    )


async def analyze_upstream(
    upstream: str, window_minutes: int, current_cb_config: dict
) -> tuple[IncidentAnalysis, dict]:
    feature_summary = await extract_feature_summary(upstream, window_minutes)

    if feature_summary.get("total_requests", 0) == 0:
        analysis = IncidentAnalysis(
            failure_type=FailureType.HEALTHY,
            confidence=1.0,
            summary=f"No requests logged for {upstream} in the last {window_minutes} minute(s). Nothing to analyze.",
            circuit_breaker_suggestion=CircuitBreakerSuggestion(
                should_adjust=False, reasoning="No traffic in this window."
            ),
        )
        return analysis, feature_summary

    client = AsyncGroq()
    response = await client.chat.completions.create(
        model=MODEL,
        max_tokens=1500,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": _build_prompt(feature_summary, current_cb_config)},
        ],
    )
    analysis = IncidentAnalysis.model_validate_json(response.choices[0].message.content)
    return analysis, feature_summary
