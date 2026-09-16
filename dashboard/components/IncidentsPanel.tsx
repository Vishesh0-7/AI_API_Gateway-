"use client";

import { useState } from "react";
import {
  AiIncidentReport,
  FailureType,
  UPSTREAMS,
  applyReport,
  triggerAnalysis,
} from "@/lib/api";

const FAILURE_STYLES: Record<FailureType, string> = {
  outage: "bg-red-500/15 text-red-400 border-red-500/40",
  degradation: "bg-amber-500/15 text-amber-400 border-amber-500/40",
  rate_limiting: "bg-amber-500/15 text-amber-400 border-amber-500/40",
  transient_blip: "bg-gray-500/15 text-gray-300 border-gray-500/40",
  healthy: "bg-emerald-500/15 text-emerald-400 border-emerald-500/40",
};

const FAILURE_LABEL: Record<FailureType, string> = {
  outage: "OUTAGE",
  degradation: "DEGRADATION",
  rate_limiting: "RATE LIMITING",
  transient_blip: "TRANSIENT BLIP",
  healthy: "HEALTHY",
};

function timeOnly(iso: string): string {
  return new Date(iso).toLocaleTimeString();
}

export function IncidentsPanel({
  reports,
  onChanged,
}: {
  reports: AiIncidentReport[] | null;
  onChanged: () => void;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const runAnalysis = async (upstream: string) => {
    setPending(`analyze:${upstream}`);
    setErrorMsg(null);
    try {
      await triggerAnalysis(upstream, 10);
      onChanged();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
    }
  };

  const runApply = async (id: number) => {
    setPending(`apply:${id}`);
    setErrorMsg(null);
    try {
      await applyReport(id);
      onChanged();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {UPSTREAMS.map((u) => (
          <button
            key={u}
            onClick={() => runAnalysis(u)}
            disabled={pending === `analyze:${u}`}
            className="text-xs font-mono px-2.5 py-1 rounded border border-gray-700 text-gray-300 hover:bg-gray-800 disabled:opacity-50"
          >
            {pending === `analyze:${u}` ? `analyzing ${u}...` : `analyze ${u}`}
          </button>
        ))}
      </div>

      {errorMsg && <p className="text-xs text-red-400">{errorMsg}</p>}

      {!reports && (
        <div className="text-sm text-gray-500">Loading incident reports...</div>
      )}
      {reports && reports.length === 0 && (
        <div className="text-sm text-gray-500">
          No AI incident reports yet. Click &quot;analyze&quot; above for an
          upstream once it has some request history.
        </div>
      )}

      <div className="space-y-3">
        {reports?.map((r) => (
          <div
            key={r.id}
            className={`rounded-lg border px-4 py-3 ${FAILURE_STYLES[r.failure_type]}`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm text-gray-200">{r.upstream}</span>
                <span className="text-xs font-semibold tracking-wide">
                  {FAILURE_LABEL[r.failure_type]}
                </span>
                <span className="text-xs text-gray-500">
                  {(r.confidence * 100).toFixed(0)}% confidence
                </span>
              </div>
              <span className="text-xs text-gray-500">{timeOnly(r.ts)}</span>
            </div>

            <p className="mt-2 text-sm text-gray-300 leading-snug">{r.summary}</p>

            {r.should_adjust && (
              <div className="mt-3 text-xs text-gray-400 border-t border-gray-700/50 pt-2 flex items-center justify-between gap-3">
                <div>
                  <div>
                    suggested: failure_threshold={r.suggested_failure_threshold ?? "-"},
                    recovery_timeout_seconds={r.suggested_recovery_timeout_seconds ?? "-"}
                  </div>
                  <div className="mt-0.5 italic">{r.suggestion_reasoning}</div>
                </div>
                {r.applied ? (
                  <span className="shrink-0 text-emerald-400 text-xs font-semibold">
                    APPLIED
                  </span>
                ) : (
                  <button
                    onClick={() => runApply(r.id)}
                    disabled={pending === `apply:${r.id}`}
                    className="shrink-0 text-xs px-2.5 py-1 rounded border border-gray-700 text-gray-300 hover:bg-gray-800 disabled:opacity-50"
                  >
                    {pending === `apply:${r.id}` ? "applying..." : "apply"}
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
