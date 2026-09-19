"use client";

import { useState } from "react";
import {
  ChaosScenario,
  ChaosStatusResponse,
  UPSTREAMS,
  sendTraffic,
  triggerChaos,
} from "@/lib/api";

const TRAFFIC_COUNT = 10;

function summarizeStatuses(codes: number[]): string {
  const counts = new Map<number, number>();
  for (const c of codes) counts.set(c, (counts.get(c) ?? 0) + 1);
  return [...counts.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([code, n]) => `${n}×${code === 0 ? "err" : code}`)
    .join(", ");
}

const MODE_STYLES: Record<string, string> = {
  normal: "bg-emerald-500/15 text-emerald-400 border-emerald-500/40",
  error: "bg-red-500/15 text-red-400 border-red-500/40",
  rate_limited: "bg-amber-500/15 text-amber-400 border-amber-500/40",
  flaky: "bg-amber-500/15 text-amber-400 border-amber-500/40",
  slow: "bg-amber-500/15 text-amber-400 border-amber-500/40",
};

const SCENARIO_BUTTONS: { scenario: ChaosScenario; label: string }[] = [
  { scenario: "outage", label: "outage" },
  { scenario: "intermittent", label: "flaky" },
  { scenario: "rate_limit_storm", label: "rate limit" },
  { scenario: "degrade", label: "degrade" },
  { scenario: "reset", label: "reset" },
];

export function ChaosControls({
  status,
  onChanged,
}: {
  status: ChaosStatusResponse | null;
  onChanged: () => void;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [trafficResult, setTrafficResult] = useState<Record<string, string>>({});

  const run = async (upstream: string, scenario: ChaosScenario) => {
    const key = `${upstream}:${scenario}`;
    setPending(key);
    setErrorMsg(null);
    try {
      await triggerChaos(upstream, scenario);
      onChanged();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
    }
  };

  const runTraffic = async (upstream: string) => {
    const key = `${upstream}:traffic`;
    setPending(key);
    setErrorMsg(null);
    try {
      const codes = await sendTraffic(upstream, TRAFFIC_COUNT);
      setTrafficResult((prev) => ({ ...prev, [upstream]: summarizeStatuses(codes) }));
      onChanged();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
    }
  };

  if (!status) {
    return <div className="text-sm text-gray-500">Loading chaos controls...</div>;
  }

  return (
    <div className="space-y-3">
      {errorMsg && <p className="text-xs text-red-400">{errorMsg}</p>}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {UPSTREAMS.map((upstream) => {
          const s = status[upstream];
          const mode = s?.mode ?? "normal";
          return (
            <div
              key={upstream}
              className={`rounded-lg border px-4 py-3 ${MODE_STYLES[mode] ?? MODE_STYLES.normal}`}
            >
              <div className="flex items-center justify-between">
                <span className="font-mono text-sm text-gray-200">{upstream}</span>
                <span className="text-xs font-semibold tracking-wide uppercase">
                  {mode}
                  {s?.degrade_active ? " (ramping)" : ""}
                </span>
              </div>

              <div className="mt-3 flex flex-wrap gap-1.5">
                {SCENARIO_BUTTONS.map(({ scenario, label }) => {
                  const key = `${upstream}:${scenario}`;
                  return (
                    <button
                      key={scenario}
                      onClick={() => run(upstream, scenario)}
                      disabled={pending === key}
                      className="text-xs px-2 py-1 rounded border border-gray-700 text-gray-300 hover:bg-gray-800 disabled:opacity-50"
                    >
                      {pending === key ? "..." : label}
                    </button>
                  );
                })}
              </div>

              <div className="mt-2 pt-2 border-t border-gray-700/50">
                <button
                  onClick={() => runTraffic(upstream)}
                  disabled={pending === `${upstream}:traffic`}
                  className="text-xs px-2 py-1 rounded border border-sky-700/60 text-sky-300 hover:bg-sky-900/30 disabled:opacity-50"
                >
                  {pending === `${upstream}:traffic`
                    ? "sending..."
                    : `send traffic (${TRAFFIC_COUNT}x)`}
                </button>
                {trafficResult[upstream] && (
                  <div className="mt-1.5 text-xs text-gray-400 font-mono">
                    {trafficResult[upstream]}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
