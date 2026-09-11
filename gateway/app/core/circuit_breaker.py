"""
Circuit breaker state machine.

Design notes (for interview discussion):

- This is a *pure* state machine: no I/O, no Redis, no async. It takes a
  `time_fn` (defaults to time.monotonic) so tests can control time without
  sleeping. All persistence (making the state survive process restarts /
  be shared across gateway replicas) is a separate concern, layered on top
  in `circuit_breaker_store.py`. Keeping the transition logic pure makes it
  trivial to unit test exhaustively.

- States:
    CLOSED    - normal operation. Requests pass through. Consecutive
                failures are counted; N in a row (failure_threshold) trips
                the breaker to OPEN.
    OPEN      - requests are short-circuited (fail fast, no call to the
                upstream at all) until `recovery_timeout` has elapsed since
                the breaker opened. This is the whole point of a circuit
                breaker: stop hammering a downed dependency and stop paying
                its latency cost while it's unhealthy.
    HALF_OPEN - after the timeout, we let a small number of trial requests
                through to probe whether the upstream has recovered.
                `half_open_max_calls` successes in a row closes the breaker;
                any single failure while half-open reopens it immediately
                (and resets the recovery timer) since a half-recovered
                upstream is still not safe to hammer.

- Why "consecutive" failures rather than a rolling error rate? A rolling
  rate (e.g. 50% errors over 1 minute) is more statistically robust for
  high-volume services, but needs a windowed counter and is harder to
  reason about/test deterministically. Consecutive-failure counting is the
  simplest correct primitive and is what most textbook circuit breakers
  (e.g. Netflix Hystrix's simpler cousins, resilience4j's count-based mode)
  use by default. It's called out here as a deliberate simplicity/robustness
  tradeoff, not an oversight.

- Half-open concurrency: a single `CircuitBreaker` instance only allows one
  "in-flight trial" at a time conceptually via `allow_request()` returning
  True; callers are expected to call `on_success`/`on_failure` for every
  allowed request. In the async gateway, each upstream has exactly one
  CircuitBreaker instance backing it (via Redis-persisted state), so this is
  safe under the single-process model used here.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 10.0
    half_open_max_calls: int = 2
    time_fn: Callable[[], float] = field(default=None)  # type: ignore[assignment]

    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    consecutive_failures: int = field(default=0, init=False)
    half_open_successes: int = field(default=0, init=False)
    opened_at: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.time_fn is None:
            import time

            self.time_fn = time.monotonic

    def allow_request(self) -> bool:
        """Should the caller be allowed to make the upstream call right now?"""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            assert self.opened_at is not None
            if self.time_fn() - self.opened_at >= self.recovery_timeout_seconds:
                self._transition_to_half_open()
                return True
            return False

        # HALF_OPEN: allow probe requests through.
        return True

    def on_success(self) -> None:
        if self.state == CircuitState.CLOSED:
            self.consecutive_failures = 0
            return

        if self.state == CircuitState.HALF_OPEN:
            self.half_open_successes += 1
            if self.half_open_successes >= self.half_open_max_calls:
                self._transition_to_closed()
            return

        # OPEN state shouldn't see on_success (allow_request would have
        # been False), but handle defensively rather than raising.

    def on_failure(self) -> None:
        if self.state == CircuitState.CLOSED:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.failure_threshold:
                self._transition_to_open()
            return

        if self.state == CircuitState.HALF_OPEN:
            # Any failure during the probe phase means the upstream is not
            # healthy yet -- reopen and restart the recovery timer.
            self._transition_to_open()
            return

        # OPEN state: nothing to do, already tripped.

    def _transition_to_open(self) -> None:
        self.state = CircuitState.OPEN
        self.opened_at = self.time_fn()
        self.half_open_successes = 0

    def _transition_to_half_open(self) -> None:
        self.state = CircuitState.HALF_OPEN
        self.half_open_successes = 0

    def _transition_to_closed(self) -> None:
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.half_open_successes = 0
        self.opened_at = None

    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "half_open_successes": self.half_open_successes,
            "opened_at": self.opened_at,
        }
