"""
Token bucket rate limiter, keyed per (client_id, upstream).

Why token bucket over fixed/sliding window: token bucket naturally allows
short bursts up to `capacity` while enforcing a steady-state average rate of
`refill_per_second`, which matches how real client traffic behaves (bursty,
not perfectly smooth) better than a fixed window (which has edge-of-window
burst doubling) or a sliding-log (which is more accurate but O(n) memory
per key). Token bucket is O(1) state per key -- just (tokens, last_refill).

Why a Lua script instead of read-modify-write from Python (contrast with
circuit_breaker_store.py, which accepts that race): rate limiting is a hot
path called on *every* request, often with real concurrency from the same
client hammering the gateway -- exactly the scenario where a lost update
would silently let a client through over its limit. Redis executes a Lua
script atomically (single-threaded), so EVAL gives us a true compare-and-set
without needing WATCH/MULTI retries from the client side. This is the right
place to pay for atomicity; the circuit breaker's lower request volume and
"one lost update just means a slightly delayed trip" failure mode made that
tradeoff acceptable, this one is not.
"""
from redis.asyncio import Redis

_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_per_second = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1])
local last_refill = tonumber(bucket[2])

if tokens == nil then
    tokens = capacity
    last_refill = now
end

local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + elapsed * refill_per_second)

local allowed = 0
if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
end

redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_refill', tostring(now))
redis.call('EXPIRE', key, 3600)

return {allowed, tostring(tokens)}
"""


class RateLimiter:
    def __init__(self, redis: Redis, capacity: int, refill_per_second: float):
        self._redis = redis
        self._capacity = capacity
        self._refill_per_second = refill_per_second
        self._script = redis.register_script(_TOKEN_BUCKET_LUA)

    def _key(self, client_id: str, upstream: str) -> str:
        return f"rl:{client_id}:{upstream}"

    async def check(self, client_id: str, upstream: str, cost: int = 1) -> tuple[bool, float]:
        """Returns (allowed, tokens_remaining)."""
        import time

        now = time.time()
        result = await self._script(
            keys=[self._key(client_id, upstream)],
            args=[self._capacity, self._refill_per_second, now, cost],
        )
        allowed, tokens_remaining = result
        return bool(int(allowed)), float(tokens_remaining)

    async def status(self, client_id: str, upstream: str) -> dict:
        raw = await self._redis.hmget(self._key(client_id, upstream), "tokens", "last_refill")
        tokens, last_refill = raw
        return {
            "client_id": client_id,
            "upstream": upstream,
            "capacity": self._capacity,
            "refill_per_second": self._refill_per_second,
            "tokens_remaining": float(tokens) if tokens is not None else self._capacity,
            "last_refill": float(last_refill) if last_refill is not None else None,
        }
