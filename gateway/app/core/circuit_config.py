"""
Per-upstream circuit breaker configuration overrides.

By default every upstream uses the global GATEWAY_CB_* settings
(app/config.py). This module lets a specific upstream's failure_threshold /
recovery_timeout_seconds / half_open_max_calls be overridden at runtime,
stored in Redis, without a redeploy. This exists specifically so a Phase 4
AI suggestion can be "accepted" (POST /ai/reports/{id}/apply) and actually
take effect -- there would otherwise be no way to apply a per-upstream
tuning suggestion without editing environment variables and restarting the
gateway, which would defeat the point of an operator being able to accept
or reject a suggestion live.
"""
import json

from redis.asyncio import Redis

from app.config import Settings


def _key(upstream: str) -> str:
    return f"cb_config:{upstream}"


async def get_effective_config(redis: Redis, upstream: str, settings: Settings) -> dict:
    defaults = {
        "failure_threshold": settings.cb_failure_threshold,
        "recovery_timeout_seconds": settings.cb_recovery_timeout_seconds,
        "half_open_max_calls": settings.cb_half_open_max_calls,
    }
    raw = await redis.get(_key(upstream))
    if raw is None:
        return defaults
    override = json.loads(raw)
    defaults.update({k: v for k, v in override.items() if v is not None})
    return defaults


async def set_override(
    redis: Redis,
    upstream: str,
    *,
    failure_threshold: int | None = None,
    recovery_timeout_seconds: float | None = None,
    half_open_max_calls: int | None = None,
) -> dict:
    override = {
        "failure_threshold": failure_threshold,
        "recovery_timeout_seconds": recovery_timeout_seconds,
        "half_open_max_calls": half_open_max_calls,
    }
    override = {k: v for k, v in override.items() if v is not None}
    await redis.set(_key(upstream), json.dumps(override))
    return override


async def clear_override(redis: Redis, upstream: str) -> None:
    await redis.delete(_key(upstream))
