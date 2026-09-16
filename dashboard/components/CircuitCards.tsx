"use client";

import { CircuitsResponse, CircuitState } from "@/lib/api";

const STATE_STYLES: Record<CircuitState, string> = {
  closed: "bg-emerald-500/15 text-emerald-400 border-emerald-500/40",
  half_open: "bg-amber-500/15 text-amber-400 border-amber-500/40",
  open: "bg-red-500/15 text-red-400 border-red-500/40",
};

const STATE_LABEL: Record<CircuitState, string> = {
  closed: "CLOSED",
  half_open: "HALF-OPEN",
  open: "OPEN",
};

export function CircuitCards({ circuits }: { circuits: CircuitsResponse | null }) {
  if (!circuits) {
    return <div className="text-sm text-gray-500">Loading circuit state...</div>;
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
      {Object.entries(circuits).map(([upstream, snap]) => (
        <div
          key={upstream}
          className={`rounded-lg border px-4 py-3 ${STATE_STYLES[snap.state]}`}
        >
          <div className="flex items-center justify-between">
            <span className="font-mono text-sm text-gray-300">{upstream}</span>
            <span className="text-xs font-semibold tracking-wide">
              {STATE_LABEL[snap.state]}
            </span>
          </div>
          <div className="mt-2 text-xs text-gray-400 space-y-0.5">
            <div>consecutive failures: {snap.consecutive_failures}</div>
            {snap.state === "half_open" && (
              <div>half-open successes: {snap.half_open_successes}</div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
