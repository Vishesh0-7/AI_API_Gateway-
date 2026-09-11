"""
Redis-backed persistence for CircuitBreaker state.

Why separate from circuit_breaker.py: the state machine transitions
(circuit_breaker.py) are pure and synchronous so they can be unit tested
without any infrastructure. This module is the thin async layer that loads
a CircuitBreaker's state from Redis, lets the pure state machine make a
decision, and writes the result back -- so the breaker state survives
gateway restarts and (if you scaled the gateway horizontally) would be
shared across replicas.

Concurrency note / known tradeoff: this does a read -> mutate -> write
round trip against Redis without a transaction (no WATCH/MULTI or Lua
script). Under concurrent requests to the same upstream, two requests could
both read "3 consecutive failures" and both write "4" instead of one
writing 4 and the other 5 -- i.e. an update could be lost. For a single-
instance local/dev gateway this is acceptable and keeps the code readable;
the fix for a production, horizontally-scaled deployment would be a Lua
script that does the read-decide-write atomically on the Redis server. This
tradeoff is deliberate and worth calling out, not an oversight.
"""
import json
import time

from redis.asyncio import Redis

from app.core.circuit_breaker import CircuitBreaker, CircuitState


def _key(upstream: str) -> str:
    return f"cb:{upstream}"


async def load(
    redis: Redis,
    upstream: str,
    failure_threshold: int,
    recovery_timeout_seconds: float,
    half_open_max_calls: int,
) -> CircuitBreaker:
    cb = CircuitBreaker(
        failure_threshold=failure_threshold,
        recovery_timeout_seconds=recovery_timeout_seconds,
        half_open_max_calls=half_open_max_calls,
        time_fn=time.monotonic,
    )
    raw = await redis.get(_key(upstream))
    if raw is None:
        return cb

    data = json.loads(raw)
    cb.state = CircuitState(data["state"])
    cb.consecutive_failures = data["consecutive_failures"]
    cb.half_open_successes = data["half_open_successes"]
    # opened_at was stored as a wall-clock timestamp; convert back to an
    # offset from the current monotonic clock so elapsed-time comparisons
    # inside the state machine stay correct across process restarts.
    if data["opened_at_wall"] is not None:
        elapsed_since_open = time.time() - data["opened_at_wall"]
        cb.opened_at = time.monotonic() - elapsed_since_open
    return cb


async def save(redis: Redis, upstream: str, cb: CircuitBreaker) -> None:
    opened_at_wall = None
    if cb.opened_at is not None:
        elapsed_since_open = time.monotonic() - cb.opened_at
        opened_at_wall = time.time() - elapsed_since_open

    payload = {
        "state": cb.state.value,
        "consecutive_failures": cb.consecutive_failures,
        "half_open_successes": cb.half_open_successes,
        "opened_at_wall": opened_at_wall,
    }
    await redis.set(_key(upstream), json.dumps(payload))
