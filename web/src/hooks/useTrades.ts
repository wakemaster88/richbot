import { useState, useEffect, useCallback } from "react";
import type { Trade } from "@/lib/types";

export function useTrades(pair?: string | null, limit = 100, pollIntervalMs = 5000) {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch_ = useCallback(async () => {
    try {
      const url = `/api/trades?limit=${limit}${pair ? `&pair=${encodeURIComponent(pair)}` : ""}`;
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) return;
      const data = await r.json();
      setTrades(Array.isArray(data) ? data : []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch trades");
    } finally {
      setLoading(false);
    }
  }, [pair, limit]);

  useEffect(() => {
    fetch_();
    const iv = setInterval(fetch_, pollIntervalMs);
    return () => clearInterval(iv);
  }, [fetch_, pollIntervalMs]);

  return { trades, loading, error, refresh: fetch_ };
}
