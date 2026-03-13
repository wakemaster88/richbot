import { useState, useEffect, useCallback } from "react";
import type { EquityPoint } from "@/lib/types";

export function useEquity(hours = 24, pollIntervalMs = 5000) {
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch_ = useCallback(async () => {
    try {
      const r = await fetch(`/api/equity?hours=${hours}`, { cache: "no-store" });
      if (!r.ok) return;
      const data = await r.json();
      setEquity(Array.isArray(data) ? data : []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch equity");
    } finally {
      setLoading(false);
    }
  }, [hours]);

  useEffect(() => {
    fetch_();
    const iv = setInterval(fetch_, pollIntervalMs);
    return () => clearInterval(iv);
  }, [fetch_, pollIntervalMs]);

  return { equity, loading, error, refresh: fetch_ };
}
