import { useState, useEffect, useCallback } from "react";
import type { BotEvent } from "@/lib/types";

export function useEvents(limit = 30, pollIntervalMs = 5000) {
  const [events, setEvents] = useState<BotEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch_ = useCallback(async () => {
    try {
      const r = await fetch(`/api/events?limit=${limit}`, { cache: "no-store" });
      if (!r.ok) return;
      const data = await r.json();
      setEvents(Array.isArray(data) ? data : []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch events");
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    fetch_();
    const iv = setInterval(fetch_, pollIntervalMs);
    return () => clearInterval(iv);
  }, [fetch_, pollIntervalMs]);

  return { events, loading, error, refresh: fetch_ };
}
