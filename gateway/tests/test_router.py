"""
Unit tests for the failover router (app/core/router.py).

Includes a regression test for a real bug caught during manual/docker
testing: the router must drive the circuit breaker's own allow_request()
(which performs the time-based OPEN -> HALF_OPEN transition) rather than
filtering candidates by a stale CircuitState snapshot, or a recovered
primary upstream could never close its breaker again.
"""
from app.core.circuit_breaker import CircuitBreaker, CircuitState
from app.core.router import choose_upstream


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def make_cb(clock: FakeClock, **overrides) -> CircuitBreaker:
    defaults = dict(
        failure_threshold=3,
        recovery_timeout_seconds=10.0,
        half_open_max_calls=2,
        time_fn=clock,
    )
    defaults.update(overrides)
    return CircuitBreaker(**defaults)


def test_all_closed_picks_primary():
    clock = FakeClock()
    breakers = {"a": make_cb(clock), "b": make_cb(clock)}
    decision = choose_upstream("a", ["b"], breakers)
    assert decision.chosen_upstream == "a"
    assert decision.skipped_open_upstreams == []


def test_primary_open_falls_back():
    clock = FakeClock()
    a = make_cb(clock, failure_threshold=1)
    a.on_failure()  # trips a to OPEN
    b = make_cb(clock)
    decision = choose_upstream("a", ["b"], {"a": a, "b": b})
    assert decision.chosen_upstream == "b"
    assert decision.skipped_open_upstreams == ["a"]


def test_all_open_returns_none():
    clock = FakeClock()
    a = make_cb(clock, failure_threshold=1)
    a.on_failure()
    b = make_cb(clock, failure_threshold=1)
    b.on_failure()
    decision = choose_upstream("a", ["b"], {"a": a, "b": b})
    assert decision.chosen_upstream is None
    assert decision.skipped_open_upstreams == ["a", "b"]


def test_regression_recovered_primary_is_chosen_again_via_half_open():
    """
    Regression test: an OPEN primary whose recovery_timeout has elapsed must
    be selectable again (transitioning to HALF_OPEN), not permanently
    skipped. This is the exact bug caught in manual docker-compose testing.
    """
    clock = FakeClock()
    a = make_cb(clock, failure_threshold=1, recovery_timeout_seconds=10.0)
    a.on_failure()
    assert a.state == CircuitState.OPEN

    clock.advance(10.0)  # recovery timeout elapsed

    decision = choose_upstream("a", [], {"a": a})
    assert decision.chosen_upstream == "a"
    assert decision.chosen_breaker is a
    assert a.state == CircuitState.HALF_OPEN
    assert decision.skipped_open_upstreams == []


def test_regression_primary_still_within_timeout_falls_back_not_stuck():
    clock = FakeClock()
    a = make_cb(clock, failure_threshold=1, recovery_timeout_seconds=10.0)
    a.on_failure()
    b = make_cb(clock)

    clock.advance(5.0)  # not yet recovered

    decision = choose_upstream("a", ["b"], {"a": a, "b": b})
    assert decision.chosen_upstream == "b"
    assert a.state == CircuitState.OPEN  # untouched, still waiting
    assert decision.skipped_open_upstreams == ["a"]
