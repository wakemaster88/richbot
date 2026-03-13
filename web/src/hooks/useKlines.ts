import { useState, useEffect, useCallback } from "react";
import type { Kline } from "@/lib/types";

export function useKlines(
  pair: string | null,
  interval = "5m",
  limit = 200,
  pollIntervalMs = 60000,
) {
  const [klines, setKlines] = useState<Kline[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch_ = useCallback(async () => {
    if (!pair) {
      setKlines([]);
      setLoading(false);
      return;
    }
    try {
      const symbol = pair.replace("/", "");
      const url = `/api/klines?symbol=${symbol}&interval=${interval}&limit=${limit}`;
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) return;
      const data = await r.json();
      setKlines(Array.isArray(data) ? data : []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch klines");
    } finally {
      setLoading(false);
    }
  }, [pair, interval, limit]);

  useEffect(() => {
    fetch_();
    const iv = setInterval(fetch_, pollIntervalMs);
    return () => clearInterval(iv);
  }, [fetch_, pollIntervalMs]);

  return { klines, loading, error, refresh: fetch_ };
}
