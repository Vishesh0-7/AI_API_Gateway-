"use client";

import { CircuitCards } from "@/components/CircuitCards";
import { StatsTable } from "@/components/StatsTable";
import { RateLimitBars } from "@/components/RateLimitBars";
import { EventsFeed } from "@/components/EventsFeed";
import { IncidentsPanel } from "@/components/IncidentsPanel";
import { ChaosControls } from "@/components/ChaosControls";
import { usePolling } from "@/lib/usePolling";
import {
  fetchCircuits,
  fetchStatsSummary,
  fetchRateLimit,
  fetchRecentEvents,
  fetchAiReports,
  fetchChaosStatus,
  UPSTREAMS,
  RateLimitStatus,
  AiIncidentReport,
  ChaosStatusResponse,
} from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

const CLIENT_ID = "anonymous";
const STATS_WINDOW_SECONDS = 300;
const REPORTS_POLL_MS = 3000;
const CHAOS_POLL_MS = 2000;

/**
 * Same shape as usePolling but exposes a manual refetch, so an action
 * button (analyze/apply/chaos trigger) can refresh its section
 * immediately instead of waiting out the poll interval.
 */
function useReports(intervalMs: number) {
  const [data, setData] = useState<AiIncidentReport[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    try {
      const result = await fetchAiReports(20);
      setData(result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    refetch();
    const id = setInterval(refetch, intervalMs);
    return () => clearInterval(id);
  }, [refetch, intervalMs]);

  return { data, error, refetch };
}

function useChaosStatus(intervalMs: number) {
  const [data, setData] = useState<ChaosStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    try {
      const result = await fetchChaosStatus();
      setData(result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    refetch();
    const id = setInterval(refetch, intervalMs);
    return () => clearInterval(id);
  }, [refetch, intervalMs]);

  return { data, error, refetch };
}

export default function DashboardPage() {
  const circuits = usePolling(fetchCircuits, 2000);
  const stats = usePolling(() => fetchStatsSummary(STATS_WINDOW_SECONDS), 3000);
  const events = usePolling(() => fetchRecentEvents(30), 3000);

  const fetchAllRateLimits = useCallback(async () => {
    const entries = await Promise.all(
      UPSTREAMS.map(async (u) => {
        try {
          const status = await fetchRateLimit(CLIENT_ID, u);
          return [u, status] as const;
        } catch {
          return [u, null] as const;
        }
      })
    );
    return Object.fromEntries(entries) as Record<string, RateLimitStatus | null>;
  }, []);
  const rateLimits = usePolling(fetchAllRateLimits, 2000);
  const reports = useReports(REPORTS_POLL_MS);
  const chaosStatus = useChaosStatus(CHAOS_POLL_MS);

  return (
    <main className="max-w-6xl mx-auto px-6 py-8 space-y-10">
      <header>
        <h1 className="text-xl font-semibold">AI API Reliability Gateway</h1>
        <p className="text-sm text-gray-500 mt-1">
          Live gateway state -- polling every 2-3s. AI incident analysis
          below is triggered on demand, never automatically.
        </p>
        {(circuits.error || stats.error || events.error) && (
          <p className="text-xs text-red-400 mt-2">
            Having trouble reaching the gateway at{" "}
            {process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8080"}.
            Is it running?
          </p>
        )}
      </header>

      <section>
        <h2 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">
          Circuit state
        </h2>
        <CircuitCards circuits={circuits.data} />
      </section>

      <section>
        <h2 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">
          Chaos controls
        </h2>
        <ChaosControls status={chaosStatus.data} onChanged={chaosStatus.refetch} />
      </section>

      <section>
        <h2 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">
          Traffic (last {STATS_WINDOW_SECONDS / 60} min)
        </h2>
        <StatsTable rows={stats.data} />
      </section>

      <section>
        <h2 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">
          Rate limit usage (client: {CLIENT_ID})
        </h2>
        <RateLimitBars statuses={rateLimits.data || {}} />
      </section>

      <section>
        <h2 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">
          Recent requests
        </h2>
        <EventsFeed events={events.data} />
      </section>

      <section>
        <h2 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">
          AI incident analysis
        </h2>
        <IncidentsPanel reports={reports.data} onChanged={reports.refetch} />
      </section>
    </main>
  );
}
