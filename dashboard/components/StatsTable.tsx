"use client";

import { StatsSummaryRow } from "@/lib/api";

function pct(v: number | null): string {
  if (v === null) return "-";
  return `${(v * 100).toFixed(1)}%`;
}

function ms(v: number | null): string {
  if (v === null) return "-";
  return `${v.toFixed(0)}ms`;
}

export function StatsTable({ rows }: { rows: StatsSummaryRow[] | null }) {
  if (!rows) {
    return <div className="text-sm text-gray-500">Loading stats...</div>;
  }
  if (rows.length === 0) {
    return <div className="text-sm text-gray-500">No traffic in this window yet.</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="text-left text-gray-400 border-b border-gray-700">
            <th className="py-2 pr-4">Upstream</th>
            <th className="py-2 pr-4">Total</th>
            <th className="py-2 pr-4">Success rate</th>
            <th className="py-2 pr-4">Failures</th>
            <th className="py-2 pr-4">Rate-limited</th>
            <th className="py-2 pr-4">Avg latency</th>
            <th className="py-2 pr-4">p95</th>
            <th className="py-2 pr-4">p99</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.upstream} className="border-b border-gray-800">
              <td className="py-2 pr-4 font-mono">{r.upstream}</td>
              <td className="py-2 pr-4">{r.total}</td>
              <td className="py-2 pr-4">{pct(r.success_rate)}</td>
              <td className="py-2 pr-4">{r.failures}</td>
              <td className="py-2 pr-4">{r.rate_limited_count}</td>
              <td className="py-2 pr-4">{ms(r.avg_latency_ms)}</td>
              <td className="py-2 pr-4">{ms(r.p95_latency_ms)}</td>
              <td className="py-2 pr-4">{ms(r.p99_latency_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
