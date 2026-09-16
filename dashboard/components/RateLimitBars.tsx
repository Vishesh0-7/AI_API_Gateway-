"use client";

import { RateLimitStatus } from "@/lib/api";

export function RateLimitBars({
  statuses,
}: {
  statuses: Record<string, RateLimitStatus | null>;
}) {
  const entries = Object.entries(statuses);
  if (entries.every(([, v]) => v === null)) {
    return <div className="text-sm text-gray-500">Loading rate limits...</div>;
  }

  return (
    <div className="space-y-3">
      {entries.map(([upstream, status]) => {
        if (!status) return null;
        const fraction = Math.max(
          0,
          Math.min(1, status.tokens_remaining / status.capacity)
        );
        const low = fraction < 0.2;
        return (
          <div key={upstream}>
            <div className="flex justify-between text-xs text-gray-400 mb-1">
              <span className="font-mono">{upstream}</span>
              <span>
                {status.tokens_remaining.toFixed(1)} / {status.capacity} tokens
              </span>
            </div>
            <div className="h-2 rounded bg-gray-800 overflow-hidden">
              <div
                className={`h-full rounded ${low ? "bg-red-500" : "bg-sky-500"}`}
                style={{ width: `${fraction * 100}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
