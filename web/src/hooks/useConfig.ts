import { useState, useEffect, useCallback } from "react";

export function useConfig(pollIntervalMs = 30000) {
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetch_ = useCallback(async () => {
    try {
      const r = await fetch("/api/config", { cache: "no-store" });
      if (!r.ok) return;
      const data = await r.json();
      setConfig(data?.config ?? null);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch config");
    } finally {
      setLoading(false);
    }
  }, []);

  const put = useCallback(async (section: string, key: string, value: unknown) => {
    await fetch("/api/commands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "update_config",
        payload: { [section]: { [key]: value } },
      }),
    });
    fetch_();
  }, [fetch_]);

  useEffect(() => {
    fetch_();
    const iv = setInterval(fetch_, pollIntervalMs);
    return () => clearInterval(iv);
  }, [fetch_, pollIntervalMs]);

  return { config, loading, error, refresh: fetch_, put };
}
