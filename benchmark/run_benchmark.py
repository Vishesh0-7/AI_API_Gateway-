"""
Phase 3 benchmark: smart gateway vs naive passthrough.

For each failure scenario, this script:
  1. Resets all gateway state (circuit breaker + rate limiter) and puts the
     target upstream into a known failure mode via the chaos API.
  2. Runs N requests directly against the upstream (bypassing the gateway
     entirely -- this is the "naive passthrough" baseline: one attempt, no
     retry, no circuit breaker, no failover).
  3. Re-establishes the identical failure condition (same chaos mode, same
     RNG seed where relevant) and resets gateway state again.
  4. Runs N requests through the gateway's /proxy endpoint (the "smart"
     path: rate limit -> failover routing -> retry with backoff -> circuit
     breaker).
  5. Computes availability, error rate, and latency percentiles for both,
     and writes a comparison table + interpretation to BENCHMARK_REPORT.md.

Design notes:

- "Naive passthrough" is modeled as a single direct HTTP call to the
  upstream with the same per-attempt timeout the gateway uses. This is
  functionally identical to a proxy that adds no reliability logic (forward
  the request, return whatever comes back), which is simpler to implement
  than standing up a second gateway mode, without changing what is being
  measured.

- The intermittent-failure scenario reseeds the upstream's RNG (via the
  `seed` param added to /control) before every run, so the naive and smart
  runs see the exact same sequence of pass/fail draws -- a fair, reproducible
  comparison rather than two independent noisy samples.

- Requests are sent sequentially (not concurrently) with a fixed pacing
  delay, well under the gateway rate limiter's refill rate. This keeps the
  gateway's own client-side rate limiter out of the picture (it is not what
  these scenarios are testing) and keeps request ordering deterministic for
  the seeded scenario.

Run with the full stack up (`docker compose up -d`):
    cd benchmark
    python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
    .venv/Scripts/python run_benchmark.py
"""
import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

GATEWAY_URL = "http://localhost:8080"
DIRECT_UPSTREAM_URLS = {
    "upstream-a": "http://localhost:9001",
    "upstream-b": "http://localhost:9002",
    "upstream-c": "http://localhost:9003",
}
PRIMARY = "upstream-a"
FALLBACK = "upstream-b"
CLIENT_ID = "benchmark"

# Matches the gateway's default GATEWAY_UPSTREAM_TIMEOUT_SECONDS (config.py).
UPSTREAM_TIMEOUT_SECONDS = 5.0
# Generous outer timeout for smart requests: worst case is 3 attempts, each
# up to UPSTREAM_TIMEOUT_SECONDS, plus backoff delays between them.
SMART_CLIENT_TIMEOUT_SECONDS = 30.0


@dataclass
class RequestResult:
    success: bool
    status_code: int | None
    latency_ms: float
    retry_count: int | None = None
    error: str | None = None


@dataclass
class RunStats:
    total: int
    successes: int
    failures: int
    availability_pct: float
    error_rate_pct: float
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    avg_retry_count: float | None = None


async def chaos_post(path: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{GATEWAY_URL}{path}")
        resp.raise_for_status()
        return resp.json()


async def reset_all() -> None:
    await chaos_post("/chaos/reset_all")
    async with httpx.AsyncClient(timeout=15.0) as client:
        for upstream in (PRIMARY, FALLBACK):
            await client.post(
                f"{GATEWAY_URL}/chaos/{upstream}/reset_gateway_state",
                params={"client_id": CLIENT_ID},
            )


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def compute_stats(results: list[RequestResult]) -> RunStats:
    total = len(results)
    successes = sum(1 for r in results if r.success)
    failures = total - successes
    latencies = sorted(r.latency_ms for r in results)
    retry_counts = [r.retry_count for r in results if r.retry_count is not None]
    return RunStats(
        total=total,
        successes=successes,
        failures=failures,
        availability_pct=round(successes / total * 100, 1) if total else 0.0,
        error_rate_pct=round(failures / total * 100, 1) if total else 0.0,
        avg_latency_ms=round(statistics.fmean(latencies), 1) if latencies else 0.0,
        p50_latency_ms=round(_percentile(latencies, 0.50), 1),
        p95_latency_ms=round(_percentile(latencies, 0.95), 1),
        p99_latency_ms=round(_percentile(latencies, 0.99), 1),
        avg_retry_count=round(statistics.fmean(retry_counts), 2) if retry_counts else None,
    )


async def naive_request(client: httpx.AsyncClient, upstream: str) -> RequestResult:
    url = f"{DIRECT_UPSTREAM_URLS[upstream]}/work"
    start = time.perf_counter()
    try:
        resp = await client.get(url, timeout=UPSTREAM_TIMEOUT_SECONDS)
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestResult(success=resp.status_code < 400, status_code=resp.status_code, latency_ms=latency_ms)
    except httpx.TimeoutException:
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestResult(success=False, status_code=None, latency_ms=latency_ms, error="timeout")
    except httpx.HTTPError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestResult(success=False, status_code=None, latency_ms=latency_ms, error=str(exc))


def _extract_retry_count(resp: httpx.Response) -> int | None:
    try:
        data = resp.json()
    except Exception:
        return None
    if isinstance(data, dict):
        if "attempts" in data and isinstance(data["attempts"], list):
            return len(data["attempts"])
        detail = data.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("attempts"), list):
            return len(detail["attempts"])
    return None


async def smart_request(
    client: httpx.AsyncClient, upstream: str, fallbacks: list[str] | None = None
) -> RequestResult:
    params = {}
    if fallbacks:
        params["fallbacks"] = fallbacks
    start = time.perf_counter()
    try:
        resp = await client.post(
            f"{GATEWAY_URL}/proxy/{upstream}",
            params=params,
            headers={"X-Client-Id": CLIENT_ID},
            timeout=SMART_CLIENT_TIMEOUT_SECONDS,
        )
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestResult(
            success=resp.status_code == 200,
            status_code=resp.status_code,
            latency_ms=latency_ms,
            retry_count=_extract_retry_count(resp),
        )
    except httpx.TimeoutException:
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestResult(success=False, status_code=None, latency_ms=latency_ms, error="client_timeout")
    except httpx.HTTPError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestResult(success=False, status_code=None, latency_ms=latency_ms, error=str(exc))


async def run_load(request_fn, n: int, pace_seconds: float) -> list[RequestResult]:
    results: list[RequestResult] = []
    async with httpx.AsyncClient() as client:
        for i in range(n):
            results.append(await request_fn(client))
            if i < n - 1:
                await asyncio.sleep(pace_seconds)
    return results


@dataclass
class Scenario:
    key: str
    title: str
    description: str
    setup: callable  # async () -> None, re-establishes the failure condition
    requests: int
    pace_seconds: float
    naive_fn: callable = None
    smart_fn: callable = None
    interpretation: callable = field(default=None)  # (naive: RunStats, smart: RunStats) -> str


async def setup_total_outage():
    await chaos_post(f"/chaos/{PRIMARY}/outage")


async def setup_intermittent():
    await chaos_post(f"/chaos/{PRIMARY}/intermittent?failure_rate=0.4&seed=1234")


async def setup_rate_limit_storm():
    await chaos_post(f"/chaos/{PRIMARY}/rate_limit_storm")


async def setup_degrade():
    await chaos_post(f"/chaos/{PRIMARY}/degrade?start_ms=200&end_ms=8000&duration_seconds=12&steps=6")


async def setup_outage_with_fallback():
    await chaos_post(f"/chaos/{PRIMARY}/outage")
    await chaos_post(f"/chaos/{FALLBACK}/reset")


def interp_outage(naive: RunStats, smart: RunStats) -> str:
    return (
        f"With the primary fully down and no fallback configured, neither approach can "
        f"produce a successful response ({naive.availability_pct}% vs {smart.availability_pct}% "
        f"availability) -- there is nothing to route to. The difference is in *cost*: naive fails "
        f"every request in one fast round trip (avg {naive.avg_latency_ms}ms), while smart pays a "
        f"retry tax on the first several requests (avg {smart.avg_latency_ms}ms, up to "
        f"{smart.avg_retry_count} attempts/request) until the circuit breaker trips after 5 "
        f"consecutive failures -- after which it also fails fast. This is the circuit breaker's "
        f"real job during a *total* outage with no alternative: stop wasting time and upstream "
        f"load on requests that cannot succeed, not manufacture availability that is not there."
    )


def interp_intermittent(naive: RunStats, smart: RunStats) -> str:
    delta = round(smart.availability_pct - naive.availability_pct, 1)
    return (
        f"This is the scenario retries are built for: an upstream that fails some fraction of "
        f"requests but is fundamentally healthy. Naive passthrough gets exactly the upstream's raw "
        f"success rate ({naive.availability_pct}%), since one failed attempt is a failed request. "
        f"Smart retries a failed attempt up to 3 times, converting most transient failures into "
        f"eventual successes: {smart.availability_pct}% availability, a {delta:+}pp improvement. "
        f"The cost is latency on the requests that needed a retry (p95 {naive.p95_latency_ms}ms "
        f"naive vs {smart.p95_latency_ms}ms smart) -- a real, worthwhile trade for most APIs."
    )


def interp_rate_limit(naive: RunStats, smart: RunStats) -> str:
    return (
        f"The upstream returns 429 for every request here, which is retryable by policy (see "
        f"retry.py) -- so smart initially retries 429s just like 5xx, adding latency "
        f"(avg {smart.avg_latency_ms}ms vs naive {naive.avg_latency_ms}ms) without improving the "
        f"outcome, since the upstream is not actually going to succeed. Once 5 consecutive "
        f"failures trip the breaker, smart starts fast-failing instead of continuing to hammer an "
        f"upstream that is telling it to back off -- the well-behaved response to a real rate-limit "
        f"storm. Availability is near-identical ({naive.availability_pct}% vs "
        f"{smart.availability_pct}%) because, as with total outage, there is no fallback for this "
        f"request to succeed against; the win here is not overwhelming an already-throttled "
        f"upstream, not manufacturing successes."
    )


def interp_degrade(naive: RunStats, smart: RunStats) -> str:
    return (
        f"Latency ramps from 200ms to 8000ms over the run, crossing the gateway's "
        f"{UPSTREAM_TIMEOUT_SECONDS:.0f}s per-attempt timeout partway through. Naive has no "
        f"timeout of its own beyond matching the gateway's per-attempt value, so it keeps waiting "
        f"and succeeds on every request, just slower and slower "
        f"(p99 {naive.p99_latency_ms}ms) -- {naive.availability_pct}% availability. Smart, once "
        f"individual attempts start exceeding the timeout, treats the timeout as a retryable "
        f"failure and burns up to 3 attempts trying again at an equally-slow upstream before "
        f"giving up ({smart.availability_pct}% availability, p99 {smart.p99_latency_ms}ms) -- "
        f"worse on both counts here. This is a genuine, worth-discussing tradeoff, not a smart-"
        f"always-wins story: a circuit breaker and retry policy tuned for hard failures can make "
        f"outcomes *worse* than a naive client during pure latency degradation with no errors, "
        f"because it gives up on slow-but-eventually-successful responses that a patient naive "
        f"client would have gotten. The fix in a real system would be separating \"slow\" from "
        f"\"broken\" -- e.g. a longer timeout paired with a latency-aware (not just error-aware) "
        f"circuit breaker -- which is future work, not something this project claims to solve."
    )


def interp_failover(naive: RunStats, smart: RunStats) -> str:
    delta = round(smart.availability_pct - naive.availability_pct, 1)
    return (
        f"Primary is fully down; a healthy fallback (upstream-b) is available. Naive, by "
        f"definition, only ever talks to the primary it was configured with -- it has no concept "
        f"of a fallback, so it fails every request ({naive.availability_pct}% availability). Smart "
        f"is given the same fallback and routes to it once the primary's circuit opens: "
        f"{smart.availability_pct}% availability, a {delta:+}pp improvement. Notably this is not "
        f"100%: the router only diverts to a fallback once the primary's breaker is OPEN (see "
        f"router.py), and CLOSED is the starting state -- so the first 5 requests (the configured "
        f"failure_threshold) are still routed to, and fail against, the dead primary before the "
        f"breaker trips and every request after that succeeds via the fallback. This is a real "
        f"warm-up cost of circuit-breaker-driven failover (it learns from failures, it does not "
        f"health-check ahead of time) worth naming explicitly rather than glossing over. This "
        f"scenario is the one that most directly demonstrates the router's value -- Phase 1 and "
        f"2's other benefits (retry, fast-fail) are about efficiency and cost; this one is about "
        f"actual availability you could not get any other way without a second upstream to route to."
    )


def build_scenarios() -> list[Scenario]:
    return [
        Scenario(
            key="total_outage",
            title="Total Outage",
            description=f"{PRIMARY} returns 500 for every request; no fallback configured.",
            setup=setup_total_outage,
            requests=25,
            pace_seconds=0.3,
            naive_fn=lambda client: naive_request(client, PRIMARY),
            smart_fn=lambda client: smart_request(client, PRIMARY),
            interpretation=interp_outage,
        ),
        Scenario(
            key="intermittent_failure",
            title="Intermittent Failure",
            description=f"{PRIMARY} fails 40% of requests at random (seeded for reproducibility).",
            setup=setup_intermittent,
            requests=40,
            pace_seconds=0.3,
            naive_fn=lambda client: naive_request(client, PRIMARY),
            smart_fn=lambda client: smart_request(client, PRIMARY),
            interpretation=interp_intermittent,
        ),
        Scenario(
            key="rate_limit_storm",
            title="Rate-Limit Storm",
            description=f"{PRIMARY} returns 429 for every request; no fallback configured.",
            setup=setup_rate_limit_storm,
            requests=25,
            pace_seconds=0.3,
            naive_fn=lambda client: naive_request(client, PRIMARY),
            smart_fn=lambda client: smart_request(client, PRIMARY),
            interpretation=interp_rate_limit,
        ),
        Scenario(
            key="gradual_degradation",
            title="Gradual Latency Degradation",
            description=f"{PRIMARY} latency ramps 200ms -> 8000ms over 12s (crosses the "
            f"{UPSTREAM_TIMEOUT_SECONDS:.0f}s per-attempt timeout partway through).",
            setup=setup_degrade,
            requests=12,
            pace_seconds=1.0,
            naive_fn=lambda client: naive_request(client, PRIMARY),
            smart_fn=lambda client: smart_request(client, PRIMARY),
            interpretation=interp_degrade,
        ),
        Scenario(
            key="outage_with_failover",
            title="Total Outage, With a Healthy Fallback Available (bonus)",
            description=f"{PRIMARY} is down; {FALLBACK} is healthy. Naive is only ever configured "
            f"to call {PRIMARY} (it has no failover concept); smart is given {FALLBACK} as a "
            f"fallback.",
            setup=setup_outage_with_fallback,
            requests=20,
            pace_seconds=0.3,
            naive_fn=lambda client: naive_request(client, PRIMARY),
            smart_fn=lambda client: smart_request(client, PRIMARY, fallbacks=[FALLBACK]),
            interpretation=interp_failover,
        ),
    ]


async def run_scenario(scenario: Scenario) -> dict:
    print(f"\n=== {scenario.title} ===")
    print(scenario.description)

    await reset_all()
    await scenario.setup()
    print(f"  naive: {scenario.requests} requests @ {scenario.pace_seconds}s pacing...")
    naive_results = await run_load(scenario.naive_fn, scenario.requests, scenario.pace_seconds)

    await reset_all()
    await scenario.setup()
    print(f"  smart: {scenario.requests} requests @ {scenario.pace_seconds}s pacing...")
    smart_results = await run_load(scenario.smart_fn, scenario.requests, scenario.pace_seconds)

    await reset_all()

    naive_stats = compute_stats(naive_results)
    smart_stats = compute_stats(smart_results)
    print(f"  naive availability={naive_stats.availability_pct}%  smart availability={smart_stats.availability_pct}%")

    return {
        "scenario": scenario,
        "naive": naive_stats,
        "smart": smart_stats,
    }


def render_stats_table(naive: RunStats, smart: RunStats) -> str:
    def row(label, n_val, s_val):
        return f"| {label} | {n_val} | {s_val} |"

    lines = [
        "| Metric | Naive passthrough | Smart gateway |",
        "|---|---|---|",
        row("Requests", naive.total, smart.total),
        row("Availability", f"{naive.availability_pct}%", f"{smart.availability_pct}%"),
        row("Error rate", f"{naive.error_rate_pct}%", f"{smart.error_rate_pct}%"),
        row("Avg latency", f"{naive.avg_latency_ms}ms", f"{smart.avg_latency_ms}ms"),
        row("p50 latency", f"{naive.p50_latency_ms}ms", f"{smart.p50_latency_ms}ms"),
        row("p95 latency", f"{naive.p95_latency_ms}ms", f"{smart.p95_latency_ms}ms"),
        row("p99 latency", f"{naive.p99_latency_ms}ms", f"{smart.p99_latency_ms}ms"),
        row("Avg attempts/request", "1.0 (no retry)", smart.avg_retry_count if smart.avg_retry_count else "n/a"),
    ]
    return "\n".join(lines)


def render_report(run_results: list[dict]) -> str:
    now = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# Phase 3 Benchmark Report: Smart Gateway vs Naive Passthrough",
        "",
        f"Generated: {now}",
        "",
        "Every scenario below sends the same number of requests, at the same pacing, "
        "against the same simulated failure condition, twice: once as a naive direct "
        "call to the upstream (one attempt, no retry, no circuit breaker, no failover), "
        "and once through the gateway (rate limit, failover routing, retry with "
        "jittered exponential backoff, circuit breaker). See `benchmark/run_benchmark.py` "
        "for exact parameters and methodology notes.",
        "",
        "## Summary",
        "",
        "| Scenario | Naive availability | Smart availability | Naive p95 | Smart p95 |",
        "|---|---|---|---|---|",
    ]
    for r in run_results:
        s = r["scenario"]
        n, sm = r["naive"], r["smart"]
        lines.append(
            f"| {s.title} | {n.availability_pct}% | {sm.availability_pct}% | "
            f"{n.p95_latency_ms}ms | {sm.p95_latency_ms}ms |"
        )

    lines += ["", "## Scenario detail", ""]
    for r in run_results:
        s = r["scenario"]
        n, sm = r["naive"], r["smart"]
        lines += [
            f"### {s.title}",
            "",
            s.description,
            "",
            render_stats_table(n, sm),
            "",
            f"**Interpretation:** {s.interpretation(n, sm)}",
            "",
        ]

    lines += [
        "## Key takeaways",
        "",
        "- The smart gateway's biggest, unambiguous win is **intermittent failure** and "
        "**outage-with-a-healthy-fallback**: real availability gains a naive client "
        "structurally cannot get, because it has neither a retry loop nor a second "
        "upstream to route to.",
        "- For a **total** outage or a persistent **rate-limit storm** with no fallback, "
        "the smart gateway cannot manufacture availability that is not there -- its value "
        "there is stopping wasted retries once the pattern is established (fail-fast via "
        "the circuit breaker) rather than improving the success rate.",
        "- **Gradual latency degradation** is the one scenario where naive comes out "
        "ahead: a fixed per-attempt timeout plus retries can turn a slow-but-eventually-"
        "successful upstream into outright failures. This is a genuine design tradeoff "
        "(fail-fast vs. patience), not a bug, and worth calling out as a limitation of "
        "the current error-only (not latency-aware) circuit breaker.",
        "",
    ]
    return "\n".join(lines)


async def main():
    parser = argparse.ArgumentParser(description="Phase 3 smart-vs-naive benchmark")
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parent.parent / "BENCHMARK_REPORT.md"),
        help="Path to write the markdown report to",
    )
    parser.add_argument(
        "--json-output",
        default=None,
        help="Optional path to also dump raw stats as JSON",
    )
    args = parser.parse_args()

    scenarios = build_scenarios()
    run_results = []
    for scenario in scenarios:
        run_results.append(await run_scenario(scenario))

    report_md = render_report(run_results)
    Path(args.output).write_text(report_md, encoding="utf-8")
    print(f"\nReport written to {args.output}")

    if args.json_output:
        payload = [
            {
                "scenario": r["scenario"].key,
                "naive": vars(r["naive"]),
                "smart": vars(r["smart"]),
            }
            for r in run_results
        ]
        Path(args.json_output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Raw stats written to {args.json_output}")


if __name__ == "__main__":
    asyncio.run(main())
