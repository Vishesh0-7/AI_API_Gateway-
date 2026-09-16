export const GATEWAY_URL =
  process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8080";

export type CircuitState = "closed" | "open" | "half_open";

export interface CircuitSnapshot {
  state: CircuitState;
  consecutive_failures: number;
  half_open_successes: number;
  opened_at: number | null;
}

export type CircuitsResponse = Record<string, CircuitSnapshot>;

export interface StatsSummaryRow {
  upstream: string;
  total: number;
  successes: number;
  failures: number;
  rate_limited_count: number;
  success_rate: number | null;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  p99_latency_ms: number | null;
}

export interface RateLimitStatus {
  client_id: string;
  upstream: string;
  capacity: number;
  refill_per_second: number;
  tokens_remaining: number;
  last_refill: number | null;
}

export interface EventRow {
  ts: string;
  client_id: string;
  primary_upstream: string;
  routed_to: string | null;
  upstream_status_code: number | null;
  success: boolean;
  rate_limited: boolean;
  circuit_open: boolean;
  circuit_state_before: string | null;
  circuit_state_after: string | null;
  retry_count: number;
  latency_ms: number | null;
  error: string | null;
}

export type FailureType =
  | "outage"
  | "rate_limiting"
  | "degradation"
  | "transient_blip"
  | "healthy";

export interface AiIncidentReport {
  id: number;
  ts: string;
  upstream: string;
  window_minutes: number;
  failure_type: FailureType;
  confidence: number;
  summary: string;
  should_adjust: boolean;
  suggested_failure_threshold: number | null;
  suggested_recovery_timeout_seconds: number | null;
  suggestion_reasoning: string | null;
  applied: boolean;
  applied_at: string | null;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${GATEWAY_URL}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`GET ${path} failed: ${res.status}`);
  }
  return res.json();
}

async function postJson<T>(path: string): Promise<T> {
  const res = await fetch(`${GATEWAY_URL}${path}`, {
    method: "POST",
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`POST ${path} failed: ${res.status}`);
  }
  return res.json();
}

export const fetchCircuits = () => getJson<CircuitsResponse>("/circuits");

export const fetchStatsSummary = (windowSeconds: number) =>
  getJson<StatsSummaryRow[]>(`/stats/summary?window_seconds=${windowSeconds}`);

export const fetchRateLimit = (clientId: string, upstream: string) =>
  getJson<RateLimitStatus>(`/ratelimit/${clientId}/${upstream}`);

export const fetchRecentEvents = (limit: number) =>
  getJson<EventRow[]>(`/events/recent?limit=${limit}`);

export const fetchAiReports = (limit: number) =>
  getJson<AiIncidentReport[]>(`/ai/reports?limit=${limit}`);

export const triggerAnalysis = (upstream: string, windowMinutes: number) =>
  postJson<unknown>(`/ai/analyze/${upstream}?window_minutes=${windowMinutes}`);

export const applyReport = (id: number) =>
  postJson<unknown>(`/ai/reports/${id}/apply`);

export const UPSTREAMS = ["upstream-a", "upstream-b", "upstream-c"];
