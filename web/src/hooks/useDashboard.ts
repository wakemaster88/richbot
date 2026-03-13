import { useState, useEffect, useCallback, useMemo } from "react";
import {
  useBotStatus,
  useTrades,
  useEquity,
  useEvents,
  useConfig,
} from "./index";
import type {
  AnalyticsData,
  PiStatus,
  RLStats,
  PairMetrics,
  WalletData,
  CorrelationData,
  CBGlobalData,
} from "@/lib/types";

const POLL_MS = 5000;

async function fetchJson<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url, { cache: "no-store" });
    return r.ok ? r.json() : null;
  } catch {
    return null;
  }
}

export function useDashboard() {
  const { status: botStatus, loading: statusLoading, refresh: refreshStatus } = useBotStatus(POLL_MS);
  const { trades, loading: tradesLoading, refresh: refreshTrades } = useTrades(null, 100, POLL_MS);
  const { equity, loading: equityLoading, refresh: refreshEquity } = useEquity(24, POLL_MS);
  const { events, loading: eventsLoading, refresh: refreshEvents } = useEvents(30, POLL_MS);
  const { config: botConfig, refresh: refreshConfig } = useConfig(30000);

  const [analytics, setAnalytics] = useState<AnalyticsData | null>(null);
  const [optData, setOptData] = useState<{
    optimizations: import("@/lib/types").BotEvent[];
    regimes: import("@/lib/types").BotEvent[];
    pairRegimes: Record<string, {
      regime: PairMetrics["regime"];
      allocation: PairMetrics["allocation"];
      trailing_tp_count: number;
      trailing_tp_active: boolean;
    }>;
  }>({ optimizations: [], regimes: [], pairRegimes: {} });
  const [piStatus, setPiStatus] = useState<PiStatus | null>(null);
  const [rlStats, setRlStats] = useState<RLStats | null>(null);

  const fetchAux = useCallback(async () => {
    const [an, opt, pi, rl] = await Promise.all([
      fetchJson<AnalyticsData>("/api/analytics"),
      fetchJson<typeof optData>("/api/optimization"),
      fetchJson<PiStatus>("/api/pi"),
      fetchJson<RLStats>("/api/rl-stats"),
    ]);
    if (an?.summary) setAnalytics(an);
    if (opt) setOptData(opt);
    if (pi) setPiStatus(pi);
    if (rl) setRlStats(rl);
  }, []);

  useEffect(() => {
    fetchAux();
    const iv = setInterval(fetchAux, POLL_MS);
    return () => clearInterval(iv);
  }, [fetchAux]);

  const refreshAll = useCallback(() => {
    refreshStatus();
    refreshTrades();
    refreshEquity();
    refreshEvents();
    refreshConfig();
    fetchAux();
  }, [refreshStatus, refreshTrades, refreshEquity, refreshEvents, refreshConfig, fetchAux]);

  const loading = statusLoading && !botStatus;
  const rawStatuses = (botStatus?.pairStatuses || {}) as Record<string, PairMetrics | WalletData | CorrelationData | CBGlobalData>;
  const walletData = (rawStatuses["__wallet__"] || null) as WalletData | null;
  const correlationData = (rawStatuses["__correlation__"] || null) as CorrelationData | null;
  const cbGlobal = (rawStatuses["__circuit_breaker__"] || null) as CBGlobalData | null;
  const pairs = Object.entries(rawStatuses).filter(([k]) => !k.startsWith("__")) as [string, PairMetrics][];
  const quoteCcy = botStatus?.pairs?.[0]?.split("/")?.[1] || "USDC";
  const totalPnl = pairs.reduce((s, [, m]) => s + (m.total_pnl || 0), 0);
  const walletTotal = walletData?._total_usdc ?? pairs.reduce((s, [, m]) => s + (m.current_equity || 0), 0);
  const allTrailingTp = pairs.flatMap(([, m]) => m.trailing_tp || []);

  const targetRatio = useMemo(() => {
    if (!pairs.length) return undefined;
    const regimes = pairs.map(([, m]) => m.regime?.regime || "ranging");
    const ratios = regimes.map((r) =>
      r === "trend_down" ? 0.7 : r === "trend_up" ? 0.3 : 0.5
    );
    return ratios.reduce((s, v) => s + v, 0) / ratios.length;
  }, [pairs]);

  const pnlData = useMemo(() => {
    if (!trades.length) return [];
    const buckets: Record<string, number> = {};
    for (const t of trades) {
      const h = new Date(t.timestamp)
        .toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })
        .replace(/:\d{2}$/, ":00");
      buckets[h] = (buckets[h] || 0) + (t.pnl || 0);
    }
    return Object.entries(buckets)
      .map(([zeit, pnl]) => ({ zeit, pnl: parseFloat(pnl.toFixed(4)) }))
      .sort((a, b) => a.zeit.localeCompare(b.zeit));
  }, [trades]);

  return {
    status: botStatus,
    trades,
    equity,
    events,
    analytics,
    optData,
    piStatus,
    rlStats,
    botConfig,
    loading,
    pairs,
    quoteCcy,
    totalPnl,
    walletTotal,
    walletData,
    correlationData,
    cbGlobal,
    allTrailingTp,
    targetRatio,
    pnlData,
    refresh: refreshAll,
  };
}
