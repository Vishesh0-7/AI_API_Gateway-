"use client";

import { EventRow } from "@/lib/api";

function timeOnly(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString();
}

export function EventsFeed({ events }: { events: EventRow[] | null }) {
  if (!events) {
    return <div className="text-sm text-gray-500">Loading events...</div>;
  }
  if (events.length === 0) {
    return <div className="text-sm text-gray-500">No events logged yet. Send some traffic through /proxy.</div>;
  }

  return (
    <div className="overflow-x-auto max-h-96 overflow-y-auto">
      <table className="min-w-full text-xs">
        <thead className="sticky top-0 bg-[#0b0f14]">
          <tr className="text-left text-gray-400 border-b border-gray-700">
            <th className="py-1.5 pr-3">Time</th>
            <th className="py-1.5 pr-3">Client</th>
            <th className="py-1.5 pr-3">Primary</th>
            <th className="py-1.5 pr-3">Routed to</th>
            <th className="py-1.5 pr-3">Status</th>
            <th className="py-1.5 pr-3">Result</th>
            <th className="py-1.5 pr-3">Circuit</th>
            <th className="py-1.5 pr-3">Retries</th>
            <th className="py-1.5 pr-3">Latency</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e, i) => (
            <tr key={i} className="border-b border-gray-900">
              <td className="py-1.5 pr-3 text-gray-500">{timeOnly(e.ts)}</td>
              <td className="py-1.5 pr-3">{e.client_id}</td>
              <td className="py-1.5 pr-3 font-mono">{e.primary_upstream}</td>
              <td className="py-1.5 pr-3 font-mono">{e.routed_to ?? "-"}</td>
              <td className="py-1.5 pr-3">{e.upstream_status_code ?? "-"}</td>
              <td className="py-1.5 pr-3">
                {e.rate_limited ? (
                  <span className="text-amber-400">rate-limited</span>
                ) : e.success ? (
                  <span className="text-emerald-400">ok</span>
                ) : (
                  <span className="text-red-400">fail</span>
                )}
              </td>
              <td className="py-1.5 pr-3 text-gray-500">
                {e.circuit_state_before} → {e.circuit_state_after}
              </td>
              <td className="py-1.5 pr-3">{e.retry_count}</td>
              <td className="py-1.5 pr-3">
                {e.latency_ms !== null ? `${e.latency_ms.toFixed(0)}ms` : "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
