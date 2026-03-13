import { useState, useEffect, useCallback } from "react";
import type { BotStatus } from "@/lib/types";

export function useBotStatus(pollIntervalMs = 5000) {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch_ = useCallback(async () => {
    try {
      const r = await fetch("/api/status", { cache: "no-store" });
      if (!r.ok) return;
      const data = await r.json();
      setStatus(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch status");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetch_();
    const iv = setInterval(fetch_, pollIntervalMs);
    return () => clearInterval(iv);
  }, [fetch_, pollIntervalMs]);

  return { status, loading, error, refresh: fetch_ };
}
