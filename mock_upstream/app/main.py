"""
Mock upstream API.

A single service, deployed multiple times (upstream-a, upstream-b, upstream-c)
via docker-compose with different UPSTREAM_NAME env vars. Each instance holds
its own in-memory "mode" that determines how it behaves on /work requests.
The /control endpoints let the chaos layer (Phase 2) or a human flip that mode
at runtime without restarting the container.

Modes:
  normal        - fast, always 200
  slow          - fixed extra latency (SLOW_LATENCY_MS) before responding 200
  error         - always 500
  rate_limited  - always 429 with a Retry-After header
  flaky         - fails (500) with probability FLAKY_RATE, else 200
"""
import asyncio
import os
import random
import time
from typing import Literal

from fastapi import FastAPI, Response
from pydantic import BaseModel

UPSTREAM_NAME = os.environ.get("UPSTREAM_NAME", "upstream")

Mode = Literal["normal", "slow", "error", "rate_limited", "flaky"]

app = FastAPI(title=f"Mock Upstream ({UPSTREAM_NAME})")

state = {
    "mode": "normal",
    "slow_latency_ms": 2000,
    "flaky_rate": 0.5,
    "base_latency_ms": 20,
}


class ControlRequest(BaseModel):
    mode: Mode | None = None
    slow_latency_ms: int | None = None
    flaky_rate: float | None = None
    base_latency_ms: int | None = None
    # Reseeding random lets the benchmark script (Phase 3) replay the exact
    # same sequence of flaky-mode pass/fail outcomes across two runs of the
    # same scenario (naive vs. smart), so the comparison isn't confounded by
    # different random draws landing on each run.
    seed: int | None = None


@app.get("/health")
async def health():
    return {"upstream": UPSTREAM_NAME, "mode": state["mode"]}


@app.get("/control")
async def get_control():
    return {"upstream": UPSTREAM_NAME, **state}


@app.post("/control")
async def set_control(req: ControlRequest):
    if req.mode is not None:
        state["mode"] = req.mode
    if req.slow_latency_ms is not None:
        state["slow_latency_ms"] = req.slow_latency_ms
    if req.flaky_rate is not None:
        state["flaky_rate"] = req.flaky_rate
    if req.base_latency_ms is not None:
        state["base_latency_ms"] = req.base_latency_ms
    if req.seed is not None:
        random.seed(req.seed)
    return {"upstream": UPSTREAM_NAME, **state}


@app.api_route("/work", methods=["GET", "POST"])
async def work(response: Response):
    start = time.perf_counter()
    mode = state["mode"]

    await asyncio.sleep(state["base_latency_ms"] / 1000)

    if mode == "normal":
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"upstream": UPSTREAM_NAME, "status": "ok", "elapsed_ms": round(elapsed_ms, 2)}

    if mode == "slow":
        await asyncio.sleep(state["slow_latency_ms"] / 1000)
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"upstream": UPSTREAM_NAME, "status": "ok", "elapsed_ms": round(elapsed_ms, 2)}

    if mode == "error":
        response.status_code = 500
        return {"upstream": UPSTREAM_NAME, "status": "error", "detail": "simulated upstream failure"}

    if mode == "rate_limited":
        response.status_code = 429
        response.headers["Retry-After"] = "1"
        return {"upstream": UPSTREAM_NAME, "status": "rate_limited", "detail": "simulated 429"}

    if mode == "flaky":
        if random.random() < state["flaky_rate"]:
            response.status_code = 500
            return {"upstream": UPSTREAM_NAME, "status": "error", "detail": "simulated flaky failure"}
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"upstream": UPSTREAM_NAME, "status": "ok", "elapsed_ms": round(elapsed_ms, 2)}

    response.status_code = 500
    return {"upstream": UPSTREAM_NAME, "status": "error", "detail": f"unknown mode {mode}"}
