"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import type {
  BotStatus,
  PairMetrics,
  Trade,
  EquityPoint,
  CommandRecord,
  BotEvent,
  WalletData,
  CorrelationData,
  CBGlobalData,
  AnalyticsData,
  PiStatus,
  RLStats,
  SentimentData,
} from "@/lib/types";
import { laufzeit } from "@/lib/format";
import {
  Skeleton,
  StatusBadge,
  PortfolioHero,
  WalletPanel,
  PairInfoCard,
  PriceChart,
  CircuitBreakerStatus,
  CorrelationPanel,
  SentimentPanel,
  EquityChart,
  PnlChart,
  TradesTable,
  OptimizationPanel,
  BotHealthPanel,
  ActivityFeed,
  ControlPanel,
  AnalyticsPanel,
} from "@/components";

// -- Demo Data --

function generateDemoEquity(): EquityPoint[] {
  const pts: EquityPoint[] = [];
  let eq = 10000;
  const now = Date.now();
  for (let i = 288; i >= 0; i--) {
    eq += (Math.random() - 0.47) * 15;
    eq = Math.max(eq, 9800);
    pts.push({
      timestamp: new Date(now - i * 300000).toISOString(),
      equity: parseFloat(eq.toFixed(2)),
    });
  }
  return pts;
}

function generateDemoPnl(): { zeit: string; pnl: number }[] {
  const d: { zeit: string; pnl: number }[] = [];
  for (let i = 23; i >= 0; i--) {
    d.push({
      zeit: `${String(23 - i).padStart(2, "0")}:00`,
      pnl: parseFloat(((Math.random() - 0.42) * 8).toFixed(2)),
    });
  }
  return d;
}

function generateDemoTrades(): Trade[] {
  const now = Date.now();
  return Array.from({ length: 20 }, (_, i) => {
    const side = Math.random() > 0.5 ? "buy" : "sell";
    const price = 87000 + (Math.random() - 0.5) * 2000;
    return {
      id: `demo-${i}`,
      timestamp: new Date(now - i * 180000 * Math.random() * 5).toISOString(),
      pair: "BTC/USDC",
      side,
      price: parseFloat(price.toFixed(2)),
      amount: parseFloat((Math.random() * 0.0005 + 0.0001).toFixed(8)),
      pnl: parseFloat(((Math.random() - 0.4) * 0.5).toFixed(4)),
    };
  }).sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
}

const DEMO_STATUS: BotStatus = {
  id: "demo",
  botId: "richbot-pi",
  status: "running",
  lastHeartbeat: new Date().toISOString(),
  pairs: ["BTC/USDC"],
  pairStatuses: {
    "BTC/USDC": {
      pair: "BTC/USDC",
      price: 87432.5,
      range: "[85200.00, 89800.00]",
      range_source: "ATR+LSTM",
      grid_levels: 20,
      active_orders: 16,
      filled_orders: 4,
      total_pnl: 42.8731,
      realized_pnl: 38.21,
      unrealized_pnl: 4.6631,
      trade_count: 847,
      max_drawdown_pct: 3.24,
      sharpe_ratio: 1.87,
      current_equity: 10042.87,
      buy_count: 423,
      sell_count: 424,
      annualized_return_pct: 34.2,
      fees_paid: 12.47,
    },
  },
  startedAt: new Date(Date.now() - 3 * 86400000).toISOString(),
  version: "2.0",
};

async function fetchJson<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) return null;
    return r.json();
  } catch {
    return null;
  }
}

async function postCommand(type: string) {
  return fetch("/api/commands", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type }),
  });
}

export default function Dashboard() {
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [commands, setCommands] = useState<CommandRecord[]>([]);
  const [events, setEvents] = useState<BotEvent[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsData | null>(null);
  const [optData, setOptData] = useState<{
    optimizations: BotEvent[];
    regimes: BotEvent[];
    pairRegimes: Record<
      string,
      {
        regime: PairMetrics["regime"];
        allocation: PairMetrics["allocation"];
        trailing_tp_count: number;
        trailing_tp_active: boolean;
      }
    >;
  }>({ optimizations: [], regimes: [], pairRegimes: {} });
  const [piStatus, setPiStatus] = useState<PiStatus | null>(null);
  const [rlStats, setRlStats] = useState<RLStats | null>(null);
  const [botConfig, setBotConfig] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [isDemo, setIsDemo] = useState(false);
  const [latestCommit, setLatestCommit] = useState<string | null>(null);

  const demoEquity = useMemo(() => generateDemoEquity(), []);
  const demoPnl = useMemo(() => generateDemoPnl(), []);
  const demoTrades = useMemo(() => generateDemoTrades(), []);

  const refresh = useCallback(async () => {
    const [s, t, e, c, ev, an, opt, pi, rl, cfg] = await Promise.all([
      fetchJson<BotStatus>("/api/status"),
      fetchJson<Trade[]>("/api/trades?limit=100"),
      fetchJson<EquityPoint[]>("/api/equity?hours=24"),
      fetchJson<CommandRecord[]>("/api/commands?limit=20"),
      fetchJson<BotEvent[]>("/api/events?limit=30"),
      fetchJson<AnalyticsData>("/api/analytics"),
      fetchJson<typeof optData>("/api/optimization"),
      fetchJson<PiStatus>("/api/pi"),
      fetchJson<RLStats>("/api/rl-stats"),
      fetchJson<{ config: Record<string, unknown> | null }>("/api/config"),
    ]);

    if (s?.dbConnected) {
      setBotStatus(s);
      setTrades(t || []);
      setEquity(e || []);
      setCommands(c || []);
      setEvents(ev || []);
      if (an?.summary) setAnalytics(an);
      if (opt) setOptData(opt);
      if (pi) setPiStatus(pi);
      if (rl) setRlStats(rl);
      if (cfg?.config) setBotConfig(cfg.config);
      setIsDemo(false);
    } else {
      setBotStatus(DEMO_STATUS);
      setTrades(demoTrades);
      setEquity(demoEquity);
      setCommands([]);
      setIsDemo(true);
    }
    setLoading(false);
  }, [demoEquity, demoPnl, demoTrades]);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 5000);
    return () => clearInterval(iv);
  }, [refresh]);

  useEffect(() => {
    fetch("https://api.github.com/repos/wakemaster88/richbot/commits/main", {
      cache: "no-store",
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d?.sha) setLatestCommit(d.sha.slice(0, 7));
      })
      .catch(() => {});
  }, []);

  const handleCommand = async (type: string) => {
    if (isDemo) return;
    await postCommand(type);
    setTimeout(refresh, 1000);
  };

  const status = botStatus || DEMO_STATUS;
  const rawStatuses = (status.pairStatuses || {}) as Record<
    string,
    PairMetrics | WalletData | CorrelationData | CBGlobalData
  >;
  const walletData = (rawStatuses["__wallet__"] || null) as WalletData | null;
  const correlationData = (rawStatuses["__correlation__"] ||
    null) as CorrelationData | null;
  const cbGlobal = (rawStatuses["__circuit_breaker__"] ||
    null) as CBGlobalData | null;
  const pairs = Object.entries(rawStatuses).filter(
    ([k]) => !k.startsWith("__")
  ) as [string, PairMetrics][];
  const quoteCcy = status.pairs?.[0]?.split("/")?.[1] || "USDC";
  const totalPnl = pairs.reduce((s, [, m]) => s + (m.total_pnl || 0), 0);
  const walletTotal =
    walletData?._total_usdc ??
    pairs.reduce((s, [, m]) => s + (m.current_equity || 0), 0);
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
    if (isDemo) return demoPnl;
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
  }, [isDemo, demoPnl, trades]);

  if (loading) {
    return (
      <div className="max-w-[1400px] mx-auto px-4 py-5 sm:px-6">
        <Skeleton h={120} className="mb-4" />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-4">
          <Skeleton h={300} />
          <Skeleton h={300} />
          <Skeleton h={300} />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          <Skeleton h={250} />
          <Skeleton h={250} />
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-[1400px] mx-auto px-4 py-4 sm:px-6 pb-12">
      {isDemo && (
        <div
          className="mb-3 px-3 py-2 rounded-lg text-[10px] font-medium text-center"
          style={{
            background: "var(--accent-bg)",
            color: "var(--accent)",
            border: "1px solid color-mix(in srgb, var(--accent) 15%, transparent)",
          }}
        >
          Demo-Modus — Datenbank nicht erreichbar
        </div>
      )}
      {!isDemo && status.status === "waiting" && (
        <div
          className="mb-3 px-3 py-2 rounded-lg text-[10px] font-medium text-center"
          style={{
            background: "var(--warn-bg)",
            color: "var(--warn)",
            border: "1px solid color-mix(in srgb, var(--warn) 15%, transparent)",
          }}
        >
          Warte auf Raspberry Pi...
        </div>
      )}

      <header className="flex items-center justify-between gap-3 mb-4">
        <div className="flex items-center gap-2">
          <StatusBadge status={status.status} hb={status.lastHeartbeat} />
          {(() => {
            const critCount = events.filter(
              (e) => e.level === "critical" || e.level === "error"
            ).length;
            const warnCount = events.filter((e) => e.level === "warn").length;
            return (
              <>
                {critCount > 0 && (
                  <span
                    className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold"
                    style={{
                      background: "rgba(239,68,68,0.12)",
                      color: "#ef4444",
                    }}
                  >
                    {"\u26A0"} {critCount}
                  </span>
                )}
                {warnCount > 0 && critCount === 0 && (
                  <span
                    className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold"
                    style={{
                      background: "var(--warn-bg)",
                      color: "var(--warn)",
                    }}
                  >
                    {warnCount} Warnung{warnCount > 1 ? "en" : ""}
                  </span>
                )}
              </>
            );
          })()}
        </div>
        <div className="flex items-center gap-2 text-[10px] text-[var(--text-quaternary)]">
          <span>{laufzeit(status.startedAt)}</span>
          <span className="w-px h-2.5 bg-[var(--border)]" />
          {(() => {
            const piVer = status.version || "?";
            const isHash = piVer.length >= 7 && piVer !== "2.0" && piVer !== "?";
            const isUpToDate = isHash && latestCommit && piVer === latestCommit;
            const isOutdated = isHash && latestCommit && piVer !== latestCommit;
            return (
              <span className="flex items-center gap-1">
                {isUpToDate && (
                  <span
                    className="inline-block w-1.5 h-1.5 rounded-full bg-[var(--up)]"
                  />
                )}
                {isOutdated && (
                  <span
                    className="inline-block w-1.5 h-1.5 rounded-full bg-[var(--warn)]"
                  />
                )}
                <span style={{ color: isOutdated ? "var(--warn)" : undefined }}>
                  {isHash ? piVer : `v${piVer}`}
                </span>
                {isOutdated && (
                  <span style={{ color: "var(--warn)" }}>
                    (Update: {latestCommit})
                  </span>
                )}
              </span>
            );
          })()}
        </div>
      </header>

      <PortfolioHero
        walletTotal={walletTotal}
        totalPnl={totalPnl}
        trades={trades}
        quoteCcy={quoteCcy}
        pairs={pairs}
      />

      {walletData && (
        <WalletPanel
          wallet={walletData}
          targetRatio={targetRatio}
          pairStats={analytics?.pair_stats}
          equity={equity}
        />
      )}

      {pairs.length > 0 ? (
        pairs.map(([p, m]) => (
          <div key={p} className="grid grid-cols-1 lg:grid-cols-12 gap-3 mb-3">
            <div className="lg:col-span-8">
              <PriceChart
                pair={p}
                orders={m.open_orders}
                trades={trades.filter((t) => t.pair === p)}
                quote={quoteCcy}
                regime={m.regime?.regime}
                gridMeta={{
                  levels: m.grid_levels,
                  configured: m.grid_configured,
                  buyCount: m.grid_buy_count,
                  sellCount: m.grid_sell_count,
                  range: m.range,
                  issue: m.grid_issue,
                  unplaced: m.unplaced_orders,
                }}
              />
            </div>
            <div className="lg:col-span-4">
              <PairInfoCard pair={p} m={m} quote={quoteCcy} events={events} />
            </div>
          </div>
        ))
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-3 mb-3">
          <div className="lg:col-span-3">
            <PriceChart
              pair="BTC/USDC"
              trades={trades.filter((t) => t.pair === "BTC/USDC")}
              quote={quoteCcy}
            />
          </div>
          <div className="lg:col-span-2 card p-5 flex items-center justify-center text-[11px] text-[var(--text-quaternary)]">
            Keine Paare aktiv
          </div>
        </div>
      )}

      {cbGlobal && (
        <div className="mb-3">
          <CircuitBreakerStatus cbGlobal={cbGlobal} pairs={pairs} />
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-3">
        <EquityChart data={equity} quote={quoteCcy} />
        {pnlData.length > 0 ? (
          <PnlChart data={pnlData} quote={quoteCcy} />
        ) : (
          <div className="card p-5 h-full flex items-center justify-center text-[10px] text-[var(--text-quaternary)]">
            PnL-Chart nach ersten Trades
          </div>
        )}
      </div>

      {correlationData && correlationData.pairs?.length >= 2 && (
        <div className="mb-3">
          <CorrelationPanel data={correlationData} quote={quoteCcy} />
        </div>
      )}

      {!isDemo && (status.pairStatuses?.["__sentiment__"] as SentimentData | undefined) && (
        <div className="mb-3">
          <SentimentPanel
            data={status.pairStatuses["__sentiment__"] as SentimentData}
            enabled={!isDemo}
          />
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-3">
        <TradesTable
          trades={trades}
          trailingTp={allTrailingTp.length > 0 ? allTrailingTp : undefined}
          quote={quoteCcy}
        />
        {!isDemo && pairs.length > 0 && (
          <OptimizationPanel
            optData={optData}
            pairs={pairs}
            rlStats={rlStats}
            onCommand={handleCommand}
          />
        )}
      </div>

      {!isDemo && analytics && (
        <div className="mb-3">
          <AnalyticsPanel data={analytics} />
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        <BotHealthPanel
          pi={piStatus}
          status={status}
          rlStats={rlStats}
          sentimentEnabled={!isDemo}
        />
        <ControlPanel
          status={status.status}
          commands={commands}
          onCommand={handleCommand}
          botConfig={botConfig}
        />
        {!isDemo && events.length > 0 && <ActivityFeed events={events} />}
      </div>
    </div>
  );
}
