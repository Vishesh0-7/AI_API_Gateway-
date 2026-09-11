"""
Routing: pick a healthy upstream to actually send a request to.

Kept deliberately simple: given a primary upstream and an ordered list of
fallback upstreams, return the first one whose circuit breaker allows a
request right now. This is a "failover" router, not a load balancer -- it
always prefers the primary and only diverts traffic when the primary is
known-bad, rather than spreading load proportionally. That matches the
project's goal (stay available when an upstream degrades) rather than
optimizing throughput.

IMPORTANT: candidates must be checked via `cb.allow_request()`, not by
peeking at `cb.state` directly. `allow_request()` is what actually performs
the time-based OPEN -> HALF_OPEN transition once `recovery_timeout_seconds`
has elapsed; a stale `state == OPEN` check would filter an eligible-for-probe
breaker out before it ever gets the chance to transition, permanently
stranding it in OPEN. (This was a real bug caught in manual testing: the
first version of this function took a `dict[str, CircuitState]` snapshot
instead of the breaker objects themselves, so a primary upstream that
recovered could never close its breaker again -- every request kept
filtering it out for being "open" without ever calling allow_request() on
it.) `allow_request()` itself is pure/synchronous (no I/O), so calling it
here keeps this function easy to unit test; persisting whichever breaker
was actually mutated is the caller's responsibility.
"""
from dataclasses import dataclass

from app.core.circuit_breaker import CircuitBreaker


@dataclass
class RoutingDecision:
    chosen_upstream: str | None
    chosen_breaker: CircuitBreaker | None
    primary_upstream: str
    skipped_open_upstreams: list[str]


def choose_upstream(
    primary: str,
    fallbacks: list[str],
    breakers: dict[str, CircuitBreaker],
) -> RoutingDecision:
    candidates = [primary, *fallbacks]
    skipped: list[str] = []

    for candidate in candidates:
        cb = breakers[candidate]
        if cb.allow_request():
            return RoutingDecision(
                chosen_upstream=candidate,
                chosen_breaker=cb,
                primary_upstream=primary,
                skipped_open_upstreams=skipped,
            )
        skipped.append(candidate)

    return RoutingDecision(
        chosen_upstream=None,
        chosen_breaker=None,
        primary_upstream=primary,
        skipped_open_upstreams=skipped,
    )
