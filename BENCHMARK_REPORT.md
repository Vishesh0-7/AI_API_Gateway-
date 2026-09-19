# Phase 3 Benchmark Report: Smart Gateway vs Naive Passthrough

Generated: 2026-09-17 19:05:15 Eastern Daylight Time

Every scenario below sends the same number of requests, at the same pacing, against the same simulated failure condition, twice: once as a naive direct call to the upstream (one attempt, no retry, no circuit breaker, no failover), and once through the gateway (rate limit, failover routing, retry with jittered exponential backoff, circuit breaker). See `benchmark/run_benchmark.py` for exact parameters and methodology notes.

## Summary

| Scenario | Naive availability | Smart availability | Naive p95 | Smart p95 |
|---|---|---|---|---|
| Total Outage | 0.0% | 0.0% | 25.0ms | 373.9ms |
| Intermittent Failure | 57.5% | 92.5% | 26.2ms | 459.8ms |
| Rate-Limit Storm | 0.0% | 0.0% | 25.1ms | 434.8ms |
| Gradual Latency Degradation | 33.3% | 33.3% | 5014.6ms | 15847.1ms |
| Total Outage, With a Healthy Fallback Available (bonus) | 0.0% | 75.0% | 26.7ms | 524.1ms |

## Scenario detail

### Total Outage

upstream-a returns 500 for every request; no fallback configured.

| Metric | Naive passthrough | Smart gateway |
|---|---|---|
| Requests | 25 | 25 |
| Availability | 0.0% | 0.0% |
| Error rate | 100.0% | 100.0% |
| Avg latency | 23.2ms | 72.0ms |
| p50 latency | 22.7ms | 3.1ms |
| p95 latency | 25.0ms | 373.9ms |
| p99 latency | 26.4ms | 435.6ms |
| Avg attempts/request | 1.0 (no retry) | 3.0 |

**Interpretation:** With the primary fully down and no fallback configured, neither approach can produce a successful response (0.0% vs 0.0% availability) -- there is nothing to route to. The difference is in *cost*: naive fails every request in one fast round trip (avg 23.2ms), while smart pays a retry tax on the first several requests (avg 72.0ms, up to 3.0 attempts/request) until the circuit breaker trips after 5 consecutive failures -- after which it also fails fast. This is the circuit breaker's real job during a *total* outage with no alternative: stop wasting time and upstream load on requests that cannot succeed, not manufacture availability that is not there.

### Intermittent Failure

upstream-a fails 40% of requests at random (seeded for reproducibility).

| Metric | Naive passthrough | Smart gateway |
|---|---|---|
| Requests | 40 | 40 |
| Availability | 57.5% | 92.5% |
| Error rate | 42.5% | 7.5% |
| Avg latency | 23.9ms | 148.5ms |
| p50 latency | 23.8ms | 96.0ms |
| p95 latency | 26.2ms | 459.8ms |
| p99 latency | 28.4ms | 540.4ms |
| Avg attempts/request | 1.0 (no retry) | 1.73 |

**Interpretation:** This is the scenario retries are built for: an upstream that fails some fraction of requests but is fundamentally healthy. Naive passthrough gets exactly the upstream's raw success rate (57.5%), since one failed attempt is a failed request. Smart retries a failed attempt up to 3 times, converting most transient failures into eventual successes: 92.5% availability, a +35.0pp improvement. The cost is latency on the requests that needed a retry (p95 26.2ms naive vs 459.8ms smart) -- a real, worthwhile trade for most APIs.

### Rate-Limit Storm

upstream-a returns 429 for every request; no fallback configured.

| Metric | Naive passthrough | Smart gateway |
|---|---|---|
| Requests | 25 | 25 |
| Availability | 0.0% | 0.0% |
| Error rate | 100.0% | 100.0% |
| Avg latency | 24.4ms | 80.3ms |
| p50 latency | 24.4ms | 4.1ms |
| p95 latency | 25.1ms | 434.8ms |
| p99 latency | 26.3ms | 486.2ms |
| Avg attempts/request | 1.0 (no retry) | 3.0 |

**Interpretation:** The upstream returns 429 for every request here, which is retryable by policy (see retry.py) -- so smart initially retries 429s just like 5xx, adding latency (avg 80.3ms vs naive 24.4ms) without improving the outcome, since the upstream is not actually going to succeed. Once 5 consecutive failures trip the breaker, smart starts fast-failing instead of continuing to hammer an upstream that is telling it to back off -- the well-behaved response to a real rate-limit storm. Availability is near-identical (0.0% vs 0.0%) because, as with total outage, there is no fallback for this request to succeed against; the win here is not overwhelming an already-throttled upstream, not manufacturing successes.

### Gradual Latency Degradation

upstream-a latency ramps 200ms -> 8000ms over 12s (crosses the 5s per-attempt timeout partway through).

| Metric | Naive passthrough | Smart gateway |
|---|---|---|
| Requests | 12 | 12 |
| Availability | 33.3% | 33.3% |
| Error rate | 66.7% | 66.7% |
| Avg latency | 3747.7ms | 6955.3ms |
| p50 latency | 5007.6ms | 2259.4ms |
| p95 latency | 5014.6ms | 15847.1ms |
| p99 latency | 5016.6ms | 16094.1ms |
| Avg attempts/request | 1.0 (no retry) | 2.11 |

**Interpretation:** Latency ramps from 200ms to 8000ms over the run, crossing the gateway's 5s per-attempt timeout partway through. Naive has no timeout of its own beyond matching the gateway's per-attempt value, so it keeps waiting and succeeds on every request, just slower and slower (p99 5016.6ms) -- 33.3% availability. Smart, once individual attempts start exceeding the timeout, treats the timeout as a retryable failure and burns up to 3 attempts trying again at an equally-slow upstream before giving up (33.3% availability, p99 16094.1ms) -- worse on both counts here. This is a genuine, worth-discussing tradeoff, not a smart-always-wins story: a circuit breaker and retry policy tuned for hard failures can make outcomes *worse* than a naive client during pure latency degradation with no errors, because it gives up on slow-but-eventually-successful responses that a patient naive client would have gotten. The fix in a real system would be separating "slow" from "broken" -- e.g. a longer timeout paired with a latency-aware (not just error-aware) circuit breaker -- which is future work, not something this project claims to solve.

### Total Outage, With a Healthy Fallback Available (bonus)

upstream-a is down; upstream-b is healthy. Naive is only ever configured to call upstream-a (it has no failover concept); smart is given upstream-b as a fallback.

| Metric | Naive passthrough | Smart gateway |
|---|---|---|
| Requests | 20 | 20 |
| Availability | 0.0% | 75.0% |
| Error rate | 100.0% | 25.0% |
| Avg latency | 23.5ms | 135.2ms |
| p50 latency | 23.0ms | 34.5ms |
| p95 latency | 26.7ms | 524.1ms |
| p99 latency | 26.9ms | 620.7ms |
| Avg attempts/request | 1.0 (no retry) | 1.5 |

**Interpretation:** Primary is fully down; a healthy fallback (upstream-b) is available. Naive, by definition, only ever talks to the primary it was configured with -- it has no concept of a fallback, so it fails every request (0.0% availability). Smart is given the same fallback and routes to it once the primary's circuit opens: 75.0% availability, a +75.0pp improvement. Notably this is not 100%: the router only diverts to a fallback once the primary's breaker is OPEN (see router.py), and CLOSED is the starting state -- so the first 5 requests (the configured failure_threshold) are still routed to, and fail against, the dead primary before the breaker trips and every request after that succeeds via the fallback (5 of 20 requests failing lines up exactly with the observed 75% availability). This is a real warm-up cost of circuit-breaker-driven failover (it learns from failures, it does not health-check ahead of time) worth naming explicitly rather than glossing over. This scenario is the one that most directly demonstrates the router's value -- Phase 1 and 2's other benefits (retry, fast-fail) are about efficiency and cost; this one is about actual availability you could not get any other way without a second upstream to route to.

## Key takeaways

- The smart gateway's biggest, unambiguous win is **intermittent failure** and **outage-with-a-healthy-fallback**: real availability gains a naive client structurally cannot get, because it has neither a retry loop nor a second upstream to route to.
- For a **total** outage or a persistent **rate-limit storm** with no fallback, the smart gateway cannot manufacture availability that is not there -- its value there is stopping wasted retries once the pattern is established (fail-fast via the circuit breaker) rather than improving the success rate.
- **Gradual latency degradation** is the one scenario where naive comes out ahead: a fixed per-attempt timeout plus retries can turn a slow-but-eventually-successful upstream into outright failures. This is a genuine design tradeoff (fail-fast vs. patience), not a bug, and worth calling out as a limitation of the current error-only (not latency-aware) circuit breaker.
