"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Polls `fetcher` every `intervalMs` and returns the latest value.
 *
 * Kept as a plain interval + fetch rather than a data-fetching library
 * (SWR/React Query) -- this dashboard has four independent, cheap polling
 * endpoints and no caching/mutation complexity that would justify the
 * dependency. Errors are captured but don't clear the last-good data, so a
 * transient gateway hiccup doesn't blank the dashboard.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number
): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let cancelled = false;

    const tick = async () => {
      try {
        const result = await fetcherRef.current();
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    };

    tick();
    const id = setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs]);

  return { data, error };
}
