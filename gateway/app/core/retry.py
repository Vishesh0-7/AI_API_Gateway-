"""
Retry with exponential backoff and full jitter.

Why full jitter (random.uniform(0, delay)) rather than plain exponential
backoff: plain exponential backoff (delay = base * 2^attempt for every
retrying client) causes retry storms -- if many clients fail at the same
moment (e.g. an upstream blip), they all back off in lockstep and all
retry at the same moment again, which can look like a self-inflicted DDoS
on a recovering upstream. "Full jitter" (AWS's term) picks a *random* delay
between 0 and the exponential cap, decorrelating retries across clients.
This is the same strategy recommended in the AWS Architecture Blog's
"Exponential Backoff and Jitter" post and is the standard choice for
production retry logic.

What is retried: only failures classified as retryable (5xx, 429, and
connection/timeout errors) via `is_retryable`. 4xx client errors other than
429 are not retried since retrying a malformed request will just fail again
identically.
"""
import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.2
    max_delay_seconds: float = 2.0

    def delay_for_attempt(self, attempt: int) -> float:
        """attempt is 0-indexed (0 = first retry, i.e. after the 1st failure)."""
        exponential = min(self.max_delay_seconds, self.base_delay_seconds * (2**attempt))
        return random.uniform(0, exponential)


def is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


class RetryExhaustedError(Exception):
    def __init__(self, attempts: int, last_exception: Exception | None = None):
        super().__init__(f"retry exhausted after {attempts} attempts")
        self.attempts = attempts
        self.last_exception = last_exception


async def run_with_retry(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    should_retry: Callable[[T], bool],
    on_attempt: Callable[[int, T | None, Exception | None], None] | None = None,
) -> T:
    """
    Calls `fn()` up to policy.max_attempts times. `should_retry(result)`
    decides, given a successfully-returned result (e.g. an HTTP response
    object), whether it counts as a failure worth retrying. Exceptions
    raised by `fn` (timeouts, connection errors) are always treated as
    retryable. `on_attempt` is an optional hook for logging/metrics, called
    after every attempt with (attempt_number, result_or_None, exception_or_None).
    """
    last_exception: Exception | None = None
    last_result: T | None = None

    for attempt in range(policy.max_attempts):
        try:
            result = await fn()
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
            last_exception = exc
            if on_attempt:
                on_attempt(attempt, None, exc)
            if attempt == policy.max_attempts - 1:
                raise RetryExhaustedError(policy.max_attempts, last_exception) from exc
            await asyncio.sleep(policy.delay_for_attempt(attempt))
            continue

        last_result = result
        if on_attempt:
            on_attempt(attempt, result, None)

        if not should_retry(result):
            return result

        if attempt == policy.max_attempts - 1:
            return result  # exhausted retries, hand back the last (failing) result

        await asyncio.sleep(policy.delay_for_attempt(attempt))

    # Unreachable, but keeps type checkers happy.
    assert last_result is not None
    return last_result
