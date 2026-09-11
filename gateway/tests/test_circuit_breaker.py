"""
Unit tests for the circuit breaker state machine (app/core/circuit_breaker.py).

Uses a fake, manually-advanced clock (FakeClock) instead of real time.sleep
so tests are fast and deterministic -- especially important for testing the
recovery_timeout boundary condition.
"""
import pytest

from app.core.circuit_breaker import CircuitBreaker, CircuitState


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def make_breaker(**overrides) -> tuple[CircuitBreaker, FakeClock]:
    clock = FakeClock()
    defaults = dict(
        failure_threshold=3,
        recovery_timeout_seconds=10.0,
        half_open_max_calls=2,
        time_fn=clock,
    )
    defaults.update(overrides)
    return CircuitBreaker(**defaults), clock


class TestClosedState:
    def test_starts_closed(self):
        cb, _ = make_breaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_success_keeps_closed_and_resets_failure_count(self):
        cb, _ = make_breaker(failure_threshold=3)
        cb.on_failure()
        cb.on_failure()
        assert cb.consecutive_failures == 2
        cb.on_success()
        assert cb.consecutive_failures == 0
        assert cb.state == CircuitState.CLOSED

    def test_failures_below_threshold_stay_closed(self):
        cb, _ = make_breaker(failure_threshold=3)
        cb.on_failure()
        cb.on_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_reaching_threshold_trips_to_open(self):
        cb, clock = make_breaker(failure_threshold=3)
        cb.on_failure()
        cb.on_failure()
        cb.on_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.opened_at == clock.now

    def test_interleaved_success_does_not_carry_over_failure_count(self):
        # 2 failures, 1 success (resets), 2 more failures should NOT trip
        # a threshold-of-3 breaker, since the count reset on success.
        cb, _ = make_breaker(failure_threshold=3)
        cb.on_failure()
        cb.on_failure()
        cb.on_success()
        cb.on_failure()
        cb.on_failure()
        assert cb.state == CircuitState.CLOSED


class TestOpenState:
    def test_open_rejects_requests_before_timeout(self):
        cb, clock = make_breaker(failure_threshold=1, recovery_timeout_seconds=10.0)
        cb.on_failure()
        assert cb.state == CircuitState.OPEN
        clock.advance(9.99)
        assert cb.allow_request() is False
        assert cb.state == CircuitState.OPEN

    def test_open_transitions_to_half_open_exactly_at_timeout(self):
        cb, clock = make_breaker(failure_threshold=1, recovery_timeout_seconds=10.0)
        cb.on_failure()
        clock.advance(10.0)
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_open_transitions_to_half_open_after_timeout(self):
        cb, clock = make_breaker(failure_threshold=1, recovery_timeout_seconds=10.0)
        cb.on_failure()
        clock.advance(15.0)
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_on_success_while_open_is_a_noop(self):
        # Defensive: allow_request() should always be checked first by
        # callers, but on_success() must not blow up or misbehave if it's
        # called while OPEN.
        cb, _ = make_breaker(failure_threshold=1)
        cb.on_failure()
        assert cb.state == CircuitState.OPEN
        cb.on_success()
        assert cb.state == CircuitState.OPEN


class TestHalfOpenState:
    def test_single_success_not_enough_to_close_with_max_calls_two(self):
        cb, clock = make_breaker(failure_threshold=1, recovery_timeout_seconds=10.0, half_open_max_calls=2)
        cb.on_failure()
        clock.advance(10.0)
        assert cb.allow_request() is True
        cb.on_success()
        assert cb.state == CircuitState.HALF_OPEN

    def test_reaching_half_open_max_calls_closes_breaker(self):
        cb, clock = make_breaker(failure_threshold=1, recovery_timeout_seconds=10.0, half_open_max_calls=2)
        cb.on_failure()
        clock.advance(10.0)
        cb.allow_request()
        cb.on_success()
        cb.on_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == 0

    def test_failure_during_half_open_reopens_and_resets_timer(self):
        cb, clock = make_breaker(failure_threshold=1, recovery_timeout_seconds=10.0)
        cb.on_failure()
        clock.advance(10.0)
        cb.allow_request()
        assert cb.state == CircuitState.HALF_OPEN
        cb.on_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.opened_at == clock.now
        # And the breaker should stay open for a *fresh* recovery window,
        # not the original one.
        clock.advance(5.0)
        assert cb.allow_request() is False

    def test_half_open_requests_are_always_allowed(self):
        cb, clock = make_breaker(failure_threshold=1, recovery_timeout_seconds=10.0)
        cb.on_failure()
        clock.advance(10.0)
        cb.allow_request()
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.allow_request() is True


class TestSnapshot:
    def test_snapshot_reflects_current_state(self):
        cb, _ = make_breaker(failure_threshold=3)
        cb.on_failure()
        snap = cb.snapshot()
        assert snap["state"] == "closed"
        assert snap["consecutive_failures"] == 1


@pytest.mark.parametrize("threshold", [1, 2, 5, 10])
def test_various_thresholds_trip_at_exactly_threshold_failures(threshold):
    cb, _ = make_breaker(failure_threshold=threshold)
    for _ in range(threshold - 1):
        cb.on_failure()
    assert cb.state == CircuitState.CLOSED
    cb.on_failure()
    assert cb.state == CircuitState.OPEN
