"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  AreaChart, Area, BarChart, Bar, ComposedChart, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Cell, ReferenceLine, Scatter,
} from "recharts";

// -- Types --

interface BotStatus {
  id: string; botId: string; status: string; lastHeartbeat: string;
  pairs: string[]; pairStatuses: Record<string, PairMetrics>;
  startedAt: string; version: string; dbConnected?: boolean;
}
interface OpenOrder { side: string; price: number; amount: number; id: string; }
interface PairMetrics {
  pair: string; price: number; range: string; range_source: string;
  grid_levels: number; grid_configured?: number; grid_buy_count?: number; grid_sell_count?: number;
  active_orders: number; filled_orders: number;
  unplaced_orders?: number; grid_issue?: string;
  allocation?: { equity: number; reserve: number; amount_per_order: number; rebalance_needed: boolean };
  regime?: { regime: string; rsi: number; adx: number; boll_width: number; avg_boll_width: number; sentiment_score?: number; sentiment_confidence?: number };
  trailing_tp?: { pair: string; side: string; entry_price: number; amount: number; highest: number; lowest: number; age_sec: number }[];
  trailing_tp_active?: boolean;
  total_pnl: number; realized_pnl: number; unrealized_pnl: number;
  trade_count: number; max_drawdown_pct: number; sharpe_ratio: number;
  current_equity: number; buy_count?: number; sell_count?: number;
  annualized_return_pct?: number; fees_paid?: number;
  open_orders?: OpenOrder[];
}
interface Trade {
  id: string; timestamp: string; pair: string; side: string;
  price: number; amount: number; pnl: number;
}
interface EquityPoint { timestamp: string; equity: number; }
interface CommandRecord {
  id: string; type: string; status: string; createdAt: string;
  result: Record<string, unknown> | null;
}
interface BotEvent {
  id: string; timestamp: string; level: string; category: string;
  message: string; detail: Record<string, unknown> | null;
}
interface WalletEntry {
  free: number; locked: number; total: number;
  usdc_value: number; price?: number;
}
type WalletData = Record<string, WalletEntry> & { _total_usdc?: number };

interface AnalyticsData {
  summary: {
    total_trades: number; wins: number; losses: number; win_rate: number;
    total_pnl: number; total_fees: number; net_pnl: number;
    avg_win: number; avg_loss: number; profit_factor: number;
    max_win_streak: number; max_loss_streak: number;
  } | null;
  pair_stats: Record<string, { trades: number; pnl: number; wins: number; losses: number; volume: number }>;
  hourly_pnl: { hour: string; pnl: number; count: number }[];
  event_counts_24h: Record<string, number>;
  snapshots: { timestamp: string; detail: Record<string, unknown> }[];
}

interface PiSystem {
  cpu_temp?: number; cpu_percent?: number; ram_total_mb?: number; ram_used_mb?: number;
  ram_percent?: number; load_1m?: number; disk_total_gb?: number; disk_used_gb?: number;
  disk_percent?: number; hostname?: string; rss_kb?: number; public_ip?: string;
}
interface PiStatus { connected: boolean; lastSeen?: string; uptime?: number; system: PiSystem | null; }

interface RLStats {
  rewards: { episode: number; reward: number; exploration: number; timestamp: string }[];
  latestAction: {
    action: { spacing_delta: number; size_delta: number; range_delta: number; distance_delta: number; action_idx: number; was_exploration: boolean } | null;
    reward: number; was_exploration: boolean; episode: number;
    heuristic_adj: Record<string, number> | null; merged_adj: Record<string, number> | null;
    timestamp: string;
  } | null;
  explorationRate: number;
  episodes: number;
  policyHints: Record<string, unknown>;
}

// -- Demo Data --

function generateDemoEquity(): EquityPoint[] {
  const pts: EquityPoint[] = [];
  let eq = 10000;
  const now = Date.now();
  for (let i = 288; i >= 0; i--) {
    eq += (Math.random() - 0.47) * 15;
    eq = Math.max(eq, 9800);
    pts.push({ timestamp: new Date(now - i * 300000).toISOString(), equity: parseFloat(eq.toFixed(2)) });
  }
  return pts;
}

function generateDemoPnl(): { zeit: string; pnl: number }[] {
  const d: { zeit: string; pnl: number }[] = [];
  for (let i = 23; i >= 0; i--) {
    d.push({ zeit: `${String(23 - i).padStart(2, "0")}:00`, pnl: parseFloat(((Math.random() - 0.42) * 8).toFixed(2)) });
  }
  return d;
}

function generateDemoTrades(): Trade[] {
  const now = Date.now();
  return Array.from({ length: 20 }, (_, i) => {
    const side = Math.random() > 0.5 ? "buy" : "sell";
    const price = 87000 + (Math.random() - 0.5) * 2000;
    return {
      id: `demo-${i}`, timestamp: new Date(now - i * 180000 * Math.random() * 5).toISOString(),
      pair: "BTC/USDC", side, price: parseFloat(price.toFixed(2)),
      amount: parseFloat((Math.random() * 0.0005 + 0.0001).toFixed(8)),
      pnl: parseFloat(((Math.random() - 0.4) * 0.5).toFixed(4)),
    };
  }).sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
}

const DEMO_STATUS: BotStatus = {
  id: "demo", botId: "richbot-pi", status: "running",
  lastHeartbeat: new Date().toISOString(), pairs: ["BTC/USDC"],
  pairStatuses: {
    "BTC/USDC": {
      pair: "BTC/USDC", price: 87432.50, range: "[85200.00, 89800.00]", range_source: "ATR+LSTM",
      grid_levels: 20, active_orders: 16, filled_orders: 4, total_pnl: 42.8731,
      realized_pnl: 38.2100, unrealized_pnl: 4.6631, trade_count: 847,
      max_drawdown_pct: 3.24, sharpe_ratio: 1.87, current_equity: 10042.87,
      buy_count: 423, sell_count: 424, annualized_return_pct: 34.2, fees_paid: 12.47,
    },
  },
  startedAt: new Date(Date.now() - 3 * 86400000).toISOString(), version: "2.0",
};

// -- API --

async function fetchJson<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) return null;
    return r.json();
  } catch { return null; }
}

async function postCommand(type: string) {
  return fetch("/api/commands", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type }),
  });
}

// -- Helpers --

function zeitAgo(d: string): string {
  const s = Math.floor((Date.now() - new Date(d).getTime()) / 1000);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

function laufzeit(start: string): string {
  const s = Math.floor((Date.now() - new Date(start).getTime()) / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}T ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function fmt(n: number, d = 2): string {
  return n.toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });
}


function fmtAmount(n: number, base: string): string {
  if (base === "BTC") {
    const sats = Math.round(n * 1e8);
    return sats.toLocaleString("de-DE") + " sat";
  }
  return n.toLocaleString("de-DE", { minimumFractionDigits: 4, maximumFractionDigits: 4 }) + " " + base;
}

// -- Skeleton --

function Skeleton({ h = 200, className = "" }: { h?: number; className?: string }) {
  return (
    <div className={`rounded-2xl overflow-hidden ${className}`} style={{ height: h, background: "var(--bg-card)", border: "1px solid var(--border)" }}>
      <div className="w-full h-full animate-pulse" style={{ background: "linear-gradient(110deg, var(--bg-card) 30%, var(--bg-elevated) 50%, var(--bg-card) 70%)", backgroundSize: "200% 100%", animation: "shimmer 1.5s infinite" }} />
    </div>
  );
}

// -- Components --

function StatusBadge({ status, hb }: { status: string; hb: string }) {
  const sec = Math.floor((Date.now() - new Date(hb).getTime()) / 1000);
  const offline = sec > 120;
  const label = offline ? "Offline" : status === "running" ? "Aktiv" : status === "paused" ? "Pausiert" : "Gestoppt";
  const color = offline ? "var(--down)" : status === "running" ? "var(--up)" : "var(--warn)";

  return (
    <span className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold" style={{ background: `color-mix(in srgb, ${color} 12%, transparent)`, color }}>
      <span className={`w-2 h-2 rounded-full ${!offline && status === "running" ? "pulse-live" : ""}`} style={{ background: color }} />
      {label}
      <span className="font-normal opacity-60">{zeitAgo(hb)}</span>
    </span>
  );
}

const COIN_COLORS: Record<string, string> = {
  USDC: "var(--up)", BTC: "#f7931a", SOL: "#9945ff", ETH: "#627eea",
};

// -- Portfolio Hero --

function PortfolioHero({ walletTotal, totalPnl, trades, quoteCcy, pairs }: {
  walletTotal: number; totalPnl: number; trades: Trade[]; quoteCcy: string;
  pairs: [string, PairMetrics][];
}) {
  const now = Date.now();
  const todayStart = new Date(); todayStart.setHours(0, 0, 0, 0);
  const weekStart = new Date(todayStart); weekStart.setDate(weekStart.getDate() - weekStart.getDay() + 1);
  if (weekStart > todayStart) weekStart.setDate(weekStart.getDate() - 7);

  const todayPnl = trades.filter(t => new Date(t.timestamp).getTime() >= todayStart.getTime()).reduce((s, t) => s + (t.pnl || 0), 0);
  const weekPnl = trades.filter(t => new Date(t.timestamp).getTime() >= weekStart.getTime()).reduce((s, t) => s + (t.pnl || 0), 0);
  const avgSharpe = pairs.length > 0 ? pairs.reduce((s, [, m]) => s + (m.sharpe_ratio || 0), 0) / pairs.length : 0;

  return (
    <div className="card p-5 sm:p-6 mb-4 fade-in">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 mb-5">
        <div>
          <p className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.15em] font-semibold mb-1">Portfolio-Wert</p>
          <div className="flex items-baseline gap-3">
            <span className="text-3xl sm:text-4xl font-bold font-mono tracking-tight">{fmt(walletTotal)}</span>
            <span className="text-sm text-[var(--text-tertiary)]">{quoteCcy}</span>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          {pairs.map(([p, m]) => {
            const regimeKey = m.regime?.regime || "ranging";
            const rs = REGIME_STYLE[regimeKey] || REGIME_STYLE.ranging;
            const ss = m.regime?.sentiment_score ?? 0;
            const sc = m.regime?.sentiment_confidence ?? 0;
            const sIcon = ss > 0.3 ? "\u25B2" : ss < -0.3 ? "\u25BC" : "";
            const sCol = ss > 0.3 ? "var(--up)" : "var(--down)";
            return (
              <span key={p} className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[9px] font-bold"
                style={{ background: rs.bg, color: rs.color }}
                title={sc > 0 ? `News: ${ss > 0 ? "+" : ""}${ss.toFixed(2)} (${(sc * 100).toFixed(0)}%)` : undefined}>
                {p.split("/")[0]} {rs.label}
                {sIcon && <span style={{ color: sCol, fontSize: "8px" }}>{sIcon}</span>}
              </span>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
        <PnlCard label="Heute" value={todayPnl} quoteCcy={quoteCcy} />
        <PnlCard label="Diese Woche" value={weekPnl} quoteCcy={quoteCcy} />
        <PnlCard label="Gesamt" value={totalPnl} quoteCcy={quoteCcy} />
        <div className="card-inner px-3 py-2.5">
          <p className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-medium">Sharpe</p>
          <p className={`text-[15px] font-bold font-mono tracking-tight mt-0.5 ${avgSharpe >= 1 ? "text-[var(--up)]" : "text-[var(--text-primary)]"}`}>
            {avgSharpe.toFixed(2)}
          </p>
        </div>
        <div className="card-inner px-3 py-2.5">
          <p className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-medium">Drawdown</p>
          <p className="text-[15px] font-bold font-mono tracking-tight mt-0.5 text-[var(--warn)]">
            {fmt(Math.max(...pairs.map(([, m]) => m.max_drawdown_pct || 0), 0))}%
          </p>
        </div>
      </div>
    </div>
  );
}

function PnlCard({ label, value, quoteCcy }: { label: string; value: number; quoteCcy: string }) {
  const up = value >= 0;
  return (
    <div className="card-inner px-3 py-2.5">
      <p className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-medium">{label}</p>
      <p className={`text-[15px] font-bold font-mono tracking-tight mt-0.5 ${up ? "text-[var(--up)]" : "text-[var(--down)]"}`}>
        {up ? "+" : ""}{fmt(value, 4)}
      </p>
    </div>
  );
}

// -- Wallet with Target Ratio --

function WalletUebersicht({ wallet, targetRatio, pairStats, equity }: {
  wallet: WalletData; targetRatio?: number;
  pairStats?: Record<string, { trades: number; pnl: number; wins: number; losses: number; volume: number }>;
  equity?: EquityPoint[];
}) {
  const total = wallet._total_usdc ?? 0;
  const coins = Object.entries(wallet)
    .filter(([k]) => !k.startsWith("_"))
    .map(([symbol, entry]) => {
      const e = entry as WalletEntry;
      const pct = total > 0 ? (e.usdc_value / total) * 100 : 0;
      const base = symbol === "USDC" ? null : symbol;
      const pairKey = base ? Object.keys(pairStats || {}).find(k => k.startsWith(base + "/")) : undefined;
      const stats = pairKey ? pairStats?.[pairKey] : undefined;
      return { symbol, ...e, pct, stats };
    })
    .sort((a, b) => b.usdc_value - a.usdc_value);

  const usdcPct = coins.find(c => c.symbol === "USDC")?.pct ?? 0;

  const allPnl = coins.filter(c => c.stats).map(c => c.stats!.pnl);
  const maxAbsPnl = allPnl.length > 0 ? Math.max(...allPnl.map(Math.abs), 0.01) : 1;

  const eqPoints = equity || [];
  const sparkLen = Math.min(eqPoints.length, 30);
  const sparkData = eqPoints.slice(-sparkLen);
  const sparkMin = sparkData.length > 0 ? Math.min(...sparkData.map(e => e.equity)) : 0;
  const sparkMax = sparkData.length > 0 ? Math.max(...sparkData.map(e => e.equity)) : 1;
  const sparkRange = sparkMax - sparkMin || 1;
  const eqFirst = sparkData[0]?.equity ?? 0;
  const eqLast = sparkData[sparkData.length - 1]?.equity ?? 0;
  const eqChange = eqFirst > 0 ? ((eqLast - eqFirst) / eqFirst) * 100 : 0;

  const ratioDelta = targetRatio != null ? usdcPct - targetRatio * 100 : 0;
  const ratioStatus = Math.abs(ratioDelta) < 3 ? "balanced" : ratioDelta > 0 ? "overweight" : "underweight";

  return (
    <div className="card p-4 sm:p-5 mb-3 fade-in">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold">Kapital-Verteilung</h3>
        <div className="flex items-center gap-3">
          {sparkData.length > 3 && (
            <div className="flex items-center gap-1.5">
              <svg width="48" height="16" viewBox={`0 0 ${sparkLen} 16`} className="opacity-80">
                <polyline fill="none" stroke={eqChange >= 0 ? "#22c55e" : "#ef4444"} strokeWidth="1.2" strokeLinejoin="round"
                  points={sparkData.map((e, i) => `${i},${16 - ((e.equity - sparkMin) / sparkRange) * 14 - 1}`).join(" ")} />
              </svg>
              <span className="text-[9px] font-mono font-semibold" style={{ color: eqChange >= 0 ? "var(--up)" : "var(--down)" }}>
                {eqChange >= 0 ? "+" : ""}{eqChange.toFixed(1)}%
              </span>
            </div>
          )}
          <span className="text-base font-bold font-mono">{fmt(total, 2)} <span className="text-[10px] text-[var(--text-tertiary)]">USDC</span></span>
        </div>
      </div>

      {/* Allocation bar */}
      <div className="relative mb-2">
        <div className="h-3 rounded-full overflow-hidden flex" style={{ background: "var(--bg-secondary)" }}>
          {coins.map((c) => (
            <div key={c.symbol} style={{ width: `${Math.max(c.pct, 1)}%`, background: COIN_COLORS[c.symbol] || "var(--text-tertiary)" }}
              className="transition-all duration-500" />
          ))}
        </div>
        {targetRatio != null && targetRatio > 0 && (
          <div className="absolute top-0 h-3 border-r-2 border-dashed"
            style={{ left: `${targetRatio * 100}%`, borderColor: "var(--accent)" }}
            title={`Ziel USDC: ${(targetRatio * 100).toFixed(0)}%`} />
        )}
      </div>

      {/* Target ratio comparison */}
      {targetRatio != null && (
        <div className="flex items-center gap-3 text-[9px] mb-3">
          <div className="flex items-center gap-1.5 text-[var(--text-quaternary)]">
            <span className="w-3 h-0 border-t-2 border-dashed" style={{ borderColor: "var(--accent)" }} />
            <span>Ziel USDC: <strong className="text-[var(--text-tertiary)]">{(targetRatio * 100).toFixed(0)}%</strong></span>
          </div>
          <span className="text-[var(--text-quaternary)]">→</span>
          <span className="font-mono font-semibold" style={{ color: "var(--text-secondary)" }}>{usdcPct.toFixed(1)}%</span>
          <span className="px-1.5 py-0.5 rounded text-[8px] font-bold" style={{
            background: ratioStatus === "balanced" ? "var(--up-bg)" : ratioStatus === "overweight" ? "var(--warn-bg)" : "var(--down-bg)",
            color: ratioStatus === "balanced" ? "var(--up)" : ratioStatus === "overweight" ? "var(--warn)" : "var(--down)",
          }}>
            {ratioStatus === "balanced" ? "IM ZIEL" : ratioStatus === "overweight" ? `+${ratioDelta.toFixed(0)}% ÜBER` : `${ratioDelta.toFixed(0)}% UNTER`}
          </span>
        </div>
      )}

      {/* Coin cards with performance */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        {coins.map((c) => {
          const pnl = c.stats?.pnl ?? 0;
          const pnlUp = pnl >= 0;
          const pnlBarW = maxAbsPnl > 0 ? (Math.abs(pnl) / maxAbsPnl) * 100 : 0;
          const winRate = c.stats && c.stats.trades > 0 ? (c.stats.wins / c.stats.trades) * 100 : null;
          return (
            <div key={c.symbol} className="px-3 py-2.5 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
              <div className="flex items-center gap-2.5 mb-1.5">
                <div className="w-7 h-7 rounded-lg flex items-center justify-center text-[9px] font-bold shrink-0"
                  style={{ background: `color-mix(in srgb, ${COIN_COLORS[c.symbol] || "var(--text-tertiary)"} 15%, transparent)`,
                           color: COIN_COLORS[c.symbol] || "var(--text-tertiary)" }}>
                  {c.symbol}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline justify-between">
                    <span className="font-mono font-semibold text-xs">
                      {c.symbol === "USDC" ? fmt(c.total, 2) : c.total < 0.01 ? c.total.toFixed(8) : fmt(c.total, 4)}
                    </span>
                    <span className="text-[10px] text-[var(--text-quaternary)] font-mono">{c.pct.toFixed(1)}%</span>
                  </div>
                  <div className="flex items-center justify-between text-[9px] text-[var(--text-quaternary)]">
                    <span>{"\u2248"} {fmt(c.usdc_value, 2)} USDC</span>
                    {c.locked > 0 && <span className="text-[var(--warn)]">{c.symbol === "USDC" ? fmt(c.locked, 2) : c.locked.toFixed(6)} gesperrt</span>}
                  </div>
                </div>
              </div>
              {/* Performance bar for traded assets */}
              {c.stats && (
                <div className="mt-1.5 pt-1.5 border-t" style={{ borderColor: "var(--border-subtle)" }}>
                  <div className="flex items-center justify-between text-[9px] mb-1">
                    <span className="font-mono font-semibold" style={{ color: pnlUp ? "var(--up)" : "var(--down)" }}>
                      {pnlUp ? "+" : ""}{pnl.toFixed(4)} USDC
                    </span>
                    <div className="flex items-center gap-2 text-[var(--text-quaternary)]">
                      {winRate !== null && <span>{winRate.toFixed(0)}% W</span>}
                      <span>{c.stats.trades} Trades</span>
                    </div>
                  </div>
                  <div className="h-1 rounded-full overflow-hidden" style={{ background: "var(--bg-elevated)" }}>
                    <div className="h-full rounded-full transition-all duration-500"
                      style={{ width: `${Math.max(pnlBarW, 2)}%`, background: pnlUp ? "var(--up)" : "var(--down)", opacity: 0.7 }} />
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// -- Pair Info Card (compact) --

function PairInfoCard({ pair, m, quote = "USDC" }: { pair: string; m: PairMetrics; quote?: string }) {
  const up = m.total_pnl >= 0;

  return (
    <div className="card p-4 sm:p-5 fade-in">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center font-bold text-[10px]" style={{ background: up ? "var(--up-bg)" : "var(--down-bg)", color: up ? "var(--up)" : "var(--down)" }}>
            {pair.split("/")[0]}
          </div>
          <div>
            <h3 className="font-semibold text-sm leading-tight">{pair}</h3>
            <p className="text-[9px] text-[var(--text-quaternary)] font-mono">{m.range}</p>
          </div>
        </div>
        <div className="text-right">
          <p className="text-lg font-bold font-mono tracking-tight">{fmt(m.price)}</p>
          <p className={`text-[11px] font-mono font-semibold ${up ? "text-[var(--up)]" : "text-[var(--down)]"}`}>
            {up ? "+" : ""}{fmt(m.total_pnl, 4)} {quote}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2 mb-3">
        <div className="card-inner px-2 py-1.5 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">Grid</p>
          <p className="text-[12px] font-bold font-mono">{m.active_orders}/{m.grid_configured || m.grid_levels}</p>
        </div>
        <div className="card-inner px-2 py-1.5 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">Trades</p>
          <p className="text-[12px] font-bold font-mono">{m.trade_count}</p>
        </div>
        <div className="card-inner px-2 py-1.5 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">Sharpe</p>
          <p className={`text-[12px] font-bold font-mono ${m.sharpe_ratio >= 1 ? "text-[var(--up)]" : ""}`}>{m.sharpe_ratio.toFixed(2)}</p>
        </div>
        <div className="card-inner px-2 py-1.5 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">DD</p>
          <p className="text-[12px] font-bold font-mono text-[var(--warn)]">{fmt(m.max_drawdown_pct)}%</p>
        </div>
      </div>

      {(m.annualized_return_pct || m.fees_paid) && (
        <div className="flex items-center gap-3 pt-2 border-t border-[var(--border-subtle)] text-[9px] text-[var(--text-quaternary)]">
          {m.annualized_return_pct !== undefined && <span>Rendite: <strong className="text-[var(--text-tertiary)]">{fmt(m.annualized_return_pct)}%</strong></span>}
          {m.fees_paid !== undefined && <span>Gebuehren: <strong className="text-[var(--text-tertiary)]">{fmt(m.fees_paid)}</strong></span>}
          <span>Kapital: <strong className="text-[var(--text-tertiary)]">{fmt(m.current_equity)}</strong></span>
        </div>
      )}
    </div>
  );
}

// -- Charts --

function EquityChart({ data, quote = "USDC" }: { data: EquityPoint[]; quote?: string }) {
  const cd = data.map((d) => ({
    t: new Date(d.timestamp).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }),
    v: d.equity,
  }));
  const up = cd.length > 1 && cd[cd.length - 1].v >= cd[0].v;
  const col = up ? "var(--up)" : "var(--down)";
  const mn = Math.min(...cd.map((d) => d.v));
  const mx = Math.max(...cd.map((d) => d.v));

  return (
    <div className="card p-4 sm:p-5 h-full">
      <div className="flex items-center justify-between mb-0.5">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold">Kapitalverlauf</h3>
        <span className="text-[9px] text-[var(--text-quaternary)] font-mono">{fmt(mn)} – {fmt(mx)}</span>
      </div>
      <div className="flex items-baseline gap-2 mb-3">
        <span className="text-xl font-bold font-mono">{fmt(cd[cd.length - 1]?.v || 0)}</span>
        <span className="text-[10px] text-[var(--text-tertiary)]">{quote}</span>
        <span className={`text-[11px] font-mono font-semibold ml-1 ${up ? "text-[var(--up)]" : "text-[var(--down)]"}`}>
          {up ? "+" : ""}{fmt((cd[cd.length - 1]?.v || 0) - (cd[0]?.v || 0))} ({fmt(((cd[cd.length - 1]?.v || 1) / (cd[0]?.v || 1) - 1) * 100)}%)
        </span>
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <AreaChart data={cd} margin={{ top: 0, right: 0, left: -15, bottom: 0 }}>
          <defs>
            <linearGradient id="eqG" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={col} stopOpacity={0.15} />
              <stop offset="100%" stopColor={col} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="t" tick={{ fill: "var(--text-quaternary)", fontSize: 9 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "var(--text-quaternary)", fontSize: 9 }} axisLine={false} tickLine={false} domain={[mn * 0.9995, mx * 1.0005]} width={50} />
          <Tooltip
            contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border-accent)", borderRadius: 10, padding: "6px 10px", fontSize: 11 }}
            labelStyle={{ color: "var(--text-tertiary)", marginBottom: 2, fontSize: 10 }}
            formatter={(v: number) => [`${fmt(v)} ${quote}`, "Kapital"]}
            itemStyle={{ color: col, fontFamily: "JetBrains Mono, monospace" }}
          />
          <Area type="monotone" dataKey="v" stroke={col} strokeWidth={1.5} fill="url(#eqG)" dot={false} activeDot={{ r: 3, fill: col, strokeWidth: 0 }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function PnlChart({ data, quote = "USDC" }: { data: { zeit: string; pnl: number }[]; quote?: string }) {
  return (
    <div className="card p-4 sm:p-5 h-full">
      <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">PnL pro Stunde</h3>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} margin={{ top: 0, right: 0, left: -15, bottom: 0 }}>
          <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="zeit" tick={{ fill: "var(--text-quaternary)", fontSize: 9 }} axisLine={false} tickLine={false} interval={3} />
          <YAxis tick={{ fill: "var(--text-quaternary)", fontSize: 9 }} axisLine={false} tickLine={false} width={35} />
          <Tooltip
            contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border-accent)", borderRadius: 10, padding: "6px 10px", fontSize: 11 }}
            formatter={(v: number) => [`${v >= 0 ? "+" : ""}${fmt(v, 4)} ${quote}`, "PnL"]}
          />
          <Bar dataKey="pnl" radius={[3, 3, 0, 0]} maxBarSize={14}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.pnl >= 0 ? "var(--up)" : "var(--down)"} fillOpacity={0.65} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// -- Price Chart --

interface Kline { t: number; o: number; h: number; l: number; c: number; v: number; }

function PreisChart({ pair, orders, trades: pairTrades, gridMeta, quote = "USDC" }: {
  pair: string; orders?: OpenOrder[]; trades?: Trade[]; quote?: string;
  gridMeta?: { levels: number; configured?: number; buyCount?: number; sellCount?: number; range: string; issue?: string; unplaced?: number };
}) {
  const [klines, setKlines] = useState<Kline[]>([]);
  const [interval, setInterval_] = useState("5m");
  const [error, setError] = useState(false);
  const retryRef = useRef(0);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const sym = pair.replace("/", "");
        const url = `https://api.binance.com/api/v3/klines?symbol=${sym}&interval=${interval}&limit=120`;
        const res = await fetch(url);
        if (!res.ok) throw new Error("API error");
        const raw = await res.json();
        if (!active) return;
        const parsed: Kline[] = raw.map((k: unknown[]) => ({
          t: Number(k[0]), o: parseFloat(k[1] as string), h: parseFloat(k[2] as string),
          l: parseFloat(k[3] as string), c: parseFloat(k[4] as string), v: parseFloat(k[5] as string),
        }));
        setKlines(parsed);
        setError(false);
        retryRef.current = 0;
      } catch {
        if (!active) return;
        retryRef.current++;
        if (retryRef.current <= 3) {
          setTimeout(load, 2000 * retryRef.current);
          return;
        }
        try {
          const sym = pair.replace("/", "");
          const res = await fetch(`/api/klines?symbol=${sym}&interval=${interval}&limit=120`);
          if (res.ok && active) {
            setKlines(await res.json());
            setError(false);
            return;
          }
        } catch { /* ignore */ }
        if (active) setError(true);
      }
    };
    load();
    const iv = window.setInterval(load, 30000);
    return () => { active = false; clearInterval(iv); };
  }, [pair, interval]);

  const intervals = ["1m", "5m", "15m", "1h", "4h", "1d"];

  if (error) {
    return (
      <div className="card p-5 h-full flex flex-col items-center justify-center gap-2 text-center">
        <p className="text-xs text-[var(--text-tertiary)]">Preisdaten nicht verfuegbar</p>
        <button onClick={() => { setError(false); retryRef.current = 0; }} className="text-[10px] text-[var(--accent)] underline">Erneut versuchen</button>
      </div>
    );
  }

  if (!klines.length) {
    return (
      <div className="card p-5 h-full flex flex-col">
        <div className="flex items-center justify-between mb-3">
          <div className="h-3 w-24 rounded bg-[var(--bg-elevated)] animate-pulse" />
          <div className="flex gap-1">{intervals.map((iv) => <div key={iv} className="h-4 w-6 rounded bg-[var(--bg-elevated)] animate-pulse" />)}</div>
        </div>
        <div className="h-5 w-32 rounded bg-[var(--bg-elevated)] animate-pulse mb-3" />
        <div className="flex-1 rounded-lg bg-[var(--bg-elevated)] animate-pulse" style={{ minHeight: 180 }} />
      </div>
    );
  }

  const chartStart = klines[0]?.t || 0;
  const chartEnd = klines[klines.length - 1]?.t || 0;
  const bucketMs = klines.length > 1 ? klines[1].t - klines[0].t : 300000;

  const visibleTrades = (pairTrades || []).filter(t => {
    const ts = new Date(t.timestamp).getTime();
    return ts >= chartStart && ts <= chartEnd + bucketMs;
  });

  const tradeMap = new Map<number, Trade[]>();
  for (const t of visibleTrades) {
    const ts = new Date(t.timestamp).getTime();
    let bestIdx = 0;
    let bestDist = Infinity;
    for (let i = 0; i < klines.length; i++) {
      const dist = Math.abs(klines[i].t - ts);
      if (dist < bestDist) { bestDist = dist; bestIdx = i; }
    }
    const arr = tradeMap.get(bestIdx) || [];
    arr.push(t);
    tradeMap.set(bestIdx, arr);
  }

  const data = klines.map((k, i) => {
    const trades = tradeMap.get(i);
    const buys = trades?.filter(t => t.side === "buy") || [];
    const sells = trades?.filter(t => t.side === "sell") || [];
    const avgBuy = buys.length ? buys.reduce((s, t) => s + t.price, 0) / buys.length : undefined;
    const avgSell = sells.length ? sells.reduce((s, t) => s + t.price, 0) / sells.length : undefined;
    return {
      zeit: new Date(k.t).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }),
      preis: k.c, hoch: k.h, tief: k.l,
      buyMarker: avgBuy,
      sellMarker: avgSell,
      _buyCount: buys.length,
      _sellCount: sells.length,
      _trades: trades || undefined,
    };
  });

  const allPrices = klines.flatMap((k) => [k.h, k.l]);
  const orderPrices = (orders || []).map((o) => o.price);
  const allVals = [...allPrices, ...orderPrices];
  const mn = Math.min(...allVals);
  const mx = Math.max(...allVals);
  const pad = (mx - mn) * 0.05;
  const last = klines[klines.length - 1]?.c || 0;
  const first = klines[0]?.c || last;
  const up = last >= first;
  const col = up ? "var(--up)" : "var(--down)";
  const chg = first > 0 ? ((last - first) / first * 100) : 0;

  const buyCount = visibleTrades.filter(t => t.side === "buy").length;
  const sellCount = visibleTrades.filter(t => t.side === "sell").length;
  const tradePnl = visibleTrades.reduce((s, t) => s + (t.pnl || 0), 0);

  const uid = pair.replace(/\W/g, "");
  const gId = `prG-${uid}`;
  const gbId = `glB-${uid}`;
  const gsId = `glS-${uid}`;

  return (
    <div className="card p-4 sm:p-5 h-full">
      <div className="flex items-center justify-between mb-0.5">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold">{pair}</h3>
        <div className="flex items-center gap-0.5">
          {intervals.map((iv) => (
            <button key={iv} onClick={() => setInterval_(iv)}
              className="px-1.5 py-0.5 rounded text-[9px] font-mono transition-all"
              style={{
                background: interval === iv ? "var(--accent-bg)" : "transparent",
                color: interval === iv ? "var(--accent)" : "var(--text-quaternary)",
              }}>
              {iv}
            </button>
          ))}
        </div>
      </div>
      <div className="flex items-baseline gap-2 mb-3">
        <span className="text-xl font-bold font-mono">{fmt(last)}</span>
        <span className="text-[10px] text-[var(--text-tertiary)]">{quote}</span>
        <span className="text-[11px] font-mono font-semibold ml-1" style={{ color: col }}>
          {chg >= 0 ? "+" : ""}{chg.toFixed(2)}%
        </span>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={data} margin={{ top: 4, right: 0, left: -15, bottom: 0 }}>
          <defs>
            <linearGradient id={gId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={col} stopOpacity={0.12} />
              <stop offset="100%" stopColor={col} stopOpacity={0} />
            </linearGradient>
            <filter id={gbId} x="-50%" y="-50%" width="200%" height="200%"><feDropShadow dx="0" dy="0" stdDeviation="1.2" floodColor="#10b981" floodOpacity="0.5" /></filter>
            <filter id={gsId} x="-50%" y="-50%" width="200%" height="200%"><feDropShadow dx="0" dy="0" stdDeviation="1.2" floodColor="#ef4444" floodOpacity="0.5" /></filter>
          </defs>
          <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="zeit" tick={{ fill: "var(--text-quaternary)", fontSize: 9 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "var(--text-quaternary)", fontSize: 9 }} axisLine={false} tickLine={false} domain={[mn - pad, mx + pad]} width={55} tickFormatter={(v) => fmt(v, 0)} />
          <Tooltip
            contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border-accent)", borderRadius: 12, padding: "8px 12px", fontSize: 11, boxShadow: "0 8px 32px rgba(0,0,0,0.4)" }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              const entry = payload[0]?.payload;
              const tdList = entry?._trades as Trade[] | undefined;
              return (
                <div style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-accent)", borderRadius: 12, padding: "8px 12px", fontSize: 11, boxShadow: "0 8px 32px rgba(0,0,0,0.4)", minWidth: 160 }}>
                  <div style={{ color: "var(--text-tertiary)", marginBottom: 4, fontSize: 10, fontWeight: 500 }}>{label}</div>
                  <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 13, fontWeight: 700, color: col }}>{fmt(entry?.preis || 0)} {quote}</div>
                  {entry?.hoch && <div style={{ display: "flex", gap: 10, marginTop: 4, fontSize: 9, color: "var(--text-quaternary)" }}>
                    <span>H: {fmt(entry.hoch)}</span><span>T: {fmt(entry.tief)}</span>
                  </div>}
                  {tdList && tdList.map((t, i) => (
                    <div key={i} style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--border-subtle)" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span style={{
                          display: "inline-flex", alignItems: "center", gap: 3, padding: "2px 6px", borderRadius: 4, fontSize: 9, fontWeight: 700, letterSpacing: "0.04em",
                          background: t.side === "buy" ? "rgba(16,185,129,0.15)" : "rgba(239,68,68,0.15)",
                          color: t.side === "buy" ? "#34d399" : "#fca5a5",
                        }}>
                          <span style={{ fontSize: 11 }}>{t.side === "buy" ? "▲" : "▼"}</span>
                          {t.side === "buy" ? "KAUF" : "VERKAUF"}
                        </span>
                        <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, fontWeight: 600 }}>{fmt(t.price)}</span>
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 4, fontSize: 10 }}>
                        <span style={{ color: "var(--text-quaternary)" }}>{fmtAmount(t.amount, pair.split("/")[0])}</span>
                        <span style={{
                          fontFamily: "JetBrains Mono, monospace", fontWeight: 700, fontSize: 11,
                          padding: "1px 5px", borderRadius: 4,
                          background: t.pnl >= 0 ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.1)",
                          color: t.pnl >= 0 ? "#34d399" : "#fca5a5",
                        }}>
                          {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(4)} {quote}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              );
            }}
          />
          {(orders || []).map((o) => (
            <ReferenceLine key={o.id} y={o.price} stroke={o.side === "buy" ? "#22c55e" : "#ef4444"} strokeDasharray="3 4" strokeOpacity={0.25} strokeWidth={1}
              label={{ value: `${o.side === "buy" ? "K" : "V"} ${fmt(o.price, 0)}`, fill: o.side === "buy" ? "#22c55e" : "#ef4444", fontSize: 7, position: o.side === "buy" ? "left" : "right", offset: 4 }} />
          ))}
          <Area type="monotone" dataKey="preis" stroke={col} strokeWidth={1.5} fill={`url(#${gId})`} dot={false} activeDot={{ r: 3, fill: col, strokeWidth: 0 }} />
          <Scatter dataKey="buyMarker" fill="#10b981" isAnimationActive={false}
            shape={(props: { cx?: number; cy?: number; payload?: Record<string, unknown> }) => {
              if (!props.cx || !props.cy) return <></>;
              const cnt = (props.payload?._buyCount as number) || 1;
              const r = cnt > 1 ? 7 : 4.5;
              return (
                <g filter={`url(#${gbId})`} style={{ cursor: "pointer" }}>
                  <circle cx={props.cx} cy={props.cy} r={r} fill="#10b981" stroke="#065f46" strokeWidth={1} />
                  {cnt === 1 ? (
                    <polygon points={`${props.cx - 2},${props.cy + 0.8} ${props.cx},${props.cy - 2} ${props.cx + 2},${props.cy + 0.8}`}
                      fill="#fff" fillOpacity={0.85} />
                  ) : (
                    <text x={props.cx} y={props.cy + 3} textAnchor="middle" fill="#fff" fontSize={8} fontWeight={700} style={{ fontFamily: "JetBrains Mono, monospace" }}>{cnt}</text>
                  )}
                </g>
              );
            }} />
          <Scatter dataKey="sellMarker" fill="#ef4444" isAnimationActive={false}
            shape={(props: { cx?: number; cy?: number; payload?: Record<string, unknown> }) => {
              if (!props.cx || !props.cy) return <></>;
              const cnt = (props.payload?._sellCount as number) || 1;
              const r = cnt > 1 ? 7 : 4.5;
              return (
                <g filter={`url(#${gsId})`} style={{ cursor: "pointer" }}>
                  <circle cx={props.cx} cy={props.cy} r={r} fill="#ef4444" stroke="#7f1d1d" strokeWidth={1} />
                  {cnt === 1 ? (
                    <polygon points={`${props.cx - 2},${props.cy - 0.8} ${props.cx},${props.cy + 2} ${props.cx + 2},${props.cy - 0.8}`}
                      fill="#fff" fillOpacity={0.85} />
                  ) : (
                    <text x={props.cx} y={props.cy + 3} textAnchor="middle" fill="#fff" fontSize={8} fontWeight={700} style={{ fontFamily: "JetBrains Mono, monospace" }}>{cnt}</text>
                  )}
                </g>
              );
            }} />
        </ComposedChart>
      </ResponsiveContainer>
      {/* Footer: Grid info + Trade summary */}
      <div className="mt-2.5 pt-2 border-t flex flex-col gap-1.5" style={{ borderColor: "var(--border-subtle)" }}>
        {/* Grid Orders Row */}
        {gridMeta && (orders || []).length > 0 && (
          <div className="flex items-center gap-2 text-[9px]">
            <span className="text-[var(--text-quaternary)] uppercase tracking-wider font-semibold" style={{ fontSize: 8 }}>Grid</span>
            <div className="flex items-center gap-1">
              <span className="inline-block w-2.5 h-[3px] rounded-full" style={{ background: "var(--up)" }} />
              <span className="text-[var(--text-quaternary)]">{gridMeta.buyCount ?? 0}K</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="inline-block w-2.5 h-[3px] rounded-full" style={{ background: "var(--down)" }} />
              <span className="text-[var(--text-quaternary)]">{gridMeta.sellCount ?? 0}V</span>
            </div>
            <span className="text-[var(--text-quaternary)] font-mono">= {(orders || []).length}/{gridMeta.configured || gridMeta.levels}</span>
            <span className="text-[var(--text-quaternary)] ml-auto font-mono">{gridMeta.range}</span>
          </div>
        )}
        {gridMeta?.issue && (
          <div className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[8px]"
            style={{ background: "var(--warn-bg)", color: "var(--warn)", border: "1px solid color-mix(in srgb, var(--warn) 15%, transparent)" }}>
            <span className="font-bold shrink-0">{gridMeta.unplaced || "?"} blockiert</span>
            <span className="truncate">{gridMeta.issue.length > 60 ? gridMeta.issue.slice(0, 57) + "..." : gridMeta.issue}</span>
          </div>
        )}
        {/* Trades Row */}
        {visibleTrades.length > 0 && (
          <div className="flex items-center gap-3 text-[9px]">
            <span className="text-[var(--text-quaternary)] uppercase tracking-wider font-semibold" style={{ fontSize: 8 }}>Trades</span>
            <div className="flex items-center gap-1.5">
              <span className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full" style={{ background: "rgba(16,185,129,0.15)" }}>
                <span style={{ color: "#34d399", fontSize: 7, lineHeight: 1 }}>▲</span>
              </span>
              <span className="text-[var(--text-quaternary)] font-mono">{buyCount}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full" style={{ background: "rgba(239,68,68,0.15)" }}>
                <span style={{ color: "#fca5a5", fontSize: 7, lineHeight: 1 }}>▼</span>
              </span>
              <span className="text-[var(--text-quaternary)] font-mono">{sellCount}</span>
            </div>
            <span className="text-[10px] font-mono font-bold px-1.5 py-0.5 rounded ml-auto" style={{
              background: tradePnl >= 0 ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.1)",
              color: tradePnl >= 0 ? "#34d399" : "#fca5a5",
            }}>
              {tradePnl >= 0 ? "+" : ""}{tradePnl.toFixed(4)} {quote}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

// -- Trades with Trailing-TP Info --

function TradesTabelle({ trades, trailingTp, quote = "USDC" }: {
  trades: Trade[];
  trailingTp?: { pair: string; side: string; entry_price: number; amount: number; highest: number; lowest: number; age_sec: number }[];
  quote?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? trades : trades.slice(0, 10);

  if (!trades.length) {
    return (
      <div className="card p-6 text-center">
        <p className="text-[11px] text-[var(--text-quaternary)]">Noch keine Trades</p>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold">Letzte Trades</h3>
        <span className="text-[9px] text-[var(--text-quaternary)] font-mono">{trades.length}</span>
      </div>

      {/* Trailing-TP Active Entries */}
      {trailingTp && trailingTp.length > 0 && (
        <div className="px-4 py-2 border-b border-[var(--border-subtle)]" style={{ background: "rgba(59,130,246,0.05)" }}>
          <p className="text-[9px] text-[#3b82f6] uppercase tracking-wider font-semibold mb-1.5">Trailing Take-Profit aktiv ({trailingTp.length})</p>
          <div className="space-y-1">
            {trailingTp.slice(0, 5).map((tp, i) => {
              const isBuy = tp.side === "buy";
              const extreme = isBuy ? tp.highest : tp.lowest;
              const profitPct = isBuy
                ? ((extreme - tp.entry_price) / tp.entry_price * 100)
                : ((tp.entry_price - extreme) / tp.entry_price * 100);
              return (
                <div key={i} className="flex items-center gap-2 text-[10px] font-mono">
                  <span className="px-1 py-0.5 rounded text-[8px] font-bold" style={{
                    background: isBuy ? "var(--up-bg)" : "var(--down-bg)",
                    color: isBuy ? "var(--up)" : "var(--down)",
                  }}>{isBuy ? "K" : "V"}</span>
                  <span className="text-[var(--text-tertiary)]">{tp.pair.split("/")[0]}</span>
                  <span>{fmt(tp.entry_price, 0)}</span>
                  <span className="text-[var(--text-quaternary)]">{"\u2192"}</span>
                  <span className="text-[#3b82f6]">{fmt(extreme, 0)}</span>
                  <span className={profitPct >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"}>
                    {profitPct >= 0 ? "+" : ""}{profitPct.toFixed(2)}%
                  </span>
                  <span className="text-[var(--text-quaternary)] ml-auto">{Math.floor(tp.age_sec / 60)}m</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Mobile */}
      <div className="sm:hidden divide-y divide-[var(--border-subtle)]">
        {shown.map((t) => (
          <div key={t.id} className="px-4 py-2.5">
            <div className="flex items-center justify-between mb-0.5">
              <div className="flex items-center gap-1.5">
                <span className="w-1 h-1 rounded-full" style={{ background: t.side === "buy" ? "var(--up)" : "var(--down)" }} />
                <span className="text-[10px] font-semibold px-1 py-0.5 rounded" style={{
                  background: t.side === "buy" ? "var(--up-bg)" : "var(--down-bg)",
                  color: t.side === "buy" ? "var(--up)" : "var(--down)"
                }}>{t.side === "buy" ? "KAUF" : "VERK."}</span>
                <span className="text-xs font-mono">{fmt(t.price, 0)}</span>
              </div>
              <span className="text-xs font-mono font-semibold" style={{ color: t.pnl >= 0 ? "var(--up)" : "var(--down)" }}>
                {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(4)}
              </span>
            </div>
            <div className="flex justify-between text-[9px] text-[var(--text-quaternary)]">
              <span className="font-mono">{fmtAmount(t.amount, t.pair.split("/")[0])} ({fmt(t.amount * t.price)} {quote})</span>
              <span>{new Date(t.timestamp).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
            </div>
          </div>
        ))}
      </div>

      {/* Desktop */}
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider">
              <th className="text-left px-4 py-2 font-medium">Zeit</th>
              <th className="text-left px-4 py-2 font-medium">Typ</th>
              <th className="text-right px-4 py-2 font-medium">Preis</th>
              <th className="text-right px-4 py-2 font-medium">Menge</th>
              <th className="text-right px-4 py-2 font-medium">Wert</th>
              <th className="text-right px-4 py-2 font-medium">PnL</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-subtle)]">
            {shown.map((t) => (
              <tr key={t.id} className="hover:bg-[var(--bg-card-hover)] transition-colors">
                <td className="px-4 py-2 font-mono text-[10px] text-[var(--text-tertiary)]">
                  {new Date(t.timestamp).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                </td>
                <td className="px-4 py-2">
                  <span className="inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold" style={{
                    background: t.side === "buy" ? "var(--up-bg)" : "var(--down-bg)",
                    color: t.side === "buy" ? "var(--up)" : "var(--down)"
                  }}>{t.side === "buy" ? "KAUF" : "VERK."}</span>
                </td>
                <td className="px-4 py-2 text-right font-mono">{fmt(t.price, 0)}</td>
                <td className="px-4 py-2 text-right font-mono text-[var(--text-tertiary)]">{fmtAmount(t.amount, t.pair.split("/")[0])}</td>
                <td className="px-4 py-2 text-right font-mono text-[var(--text-tertiary)]">{fmt(t.amount * t.price)} {quote}</td>
                <td className="px-4 py-2 text-right font-mono font-semibold" style={{ color: t.pnl >= 0 ? "var(--up)" : "var(--down)" }}>
                  {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(4)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {trades.length > 10 && (
        <button onClick={() => setExpanded(!expanded)} className="w-full py-2 text-[10px] text-[var(--accent)] hover:bg-[var(--bg-card-hover)] transition-colors border-t border-[var(--border-subtle)]">
          {expanded ? "Weniger anzeigen" : `Alle ${trades.length} Trades anzeigen`}
        </button>
      )}
    </div>
  );
}

// -- Selbst-Optimierung Panel --

function SelbstOptimierungPanel({ optData, pairs, rlStats, onCommand }: {
  optData: { optimizations: BotEvent[]; regimes: BotEvent[]; pairRegimes: Record<string, { regime: PairMetrics["regime"]; allocation: PairMetrics["allocation"]; trailing_tp_count: number; trailing_tp_active: boolean }> };
  pairs: [string, PairMetrics][];
  rlStats: RLStats | null;
  onCommand: (t: string) => void;
}) {
  const lastOpt = optData.optimizations[0];

  const rlRewards = rlStats?.rewards ?? [];
  const last30 = rlRewards.slice(-30);
  const rewardTrend = last30.length >= 2 ? last30[last30.length - 1].reward - last30[0].reward : 0;
  const avgReward = last30.length > 0 ? last30.reduce((s, r) => s + r.reward, 0) / last30.length : 0;
  const rewardMin = last30.length > 0 ? Math.min(...last30.map(r => r.reward)) : 0;
  const rewardMax = last30.length > 0 ? Math.max(...last30.map(r => r.reward)) : 1;
  const rewardRange = Math.max(rewardMax - rewardMin, 0.01);

  const explorationPct = (rlStats?.explorationRate ?? 0.15) * 100;
  const explorationProgress = Math.max(0, Math.min(100, ((0.15 - (rlStats?.explorationRate ?? 0.15)) / (0.15 - 0.03)) * 100));

  const la = rlStats?.latestAction;
  const laAction = la?.action as { spacing_delta?: number; size_delta?: number; range_delta?: number; distance_delta?: number; was_exploration?: boolean } | null;

  const fmtDelta = (v: number | undefined) => {
    if (v === undefined || v === 0) return "0%";
    return `${v > 0 ? "+" : ""}${(v * 100).toFixed(0)}%`;
  };

  return (
    <div className="card p-4 sm:p-5 fade-in">
      <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">Selbst-Optimierung</h3>

      {/* Capital Allocation bars */}
      {pairs.length > 1 && (
        <div className="mb-4">
          <div className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider mb-1.5 font-semibold">Kapital-Allokation</div>
          <div className="flex gap-1.5 h-5 rounded-md overflow-hidden" style={{ background: "var(--bg-secondary)" }}>
            {pairs.map(([p, m], i) => {
              const eq = m.allocation?.equity ?? m.current_equity ?? 0;
              const totalEq = pairs.reduce((s, [, pm]) => s + (pm.allocation?.equity ?? pm.current_equity ?? 0), 0);
              const pct = totalEq > 0 ? (eq / totalEq) * 100 : 100 / pairs.length;
              const colors = ["#3b82f6", "var(--up)", "var(--warn)", "var(--accent)"];
              return (
                <div key={p} className="flex items-center justify-center text-[8px] font-bold text-white transition-all"
                  style={{ width: `${pct}%`, background: colors[i % colors.length], minWidth: 30 }}>
                  {p.split("/")[0]} {pct.toFixed(0)}%
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Per-pair: Entry Filter + Trailing TP */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-4">
        {pairs.map(([p, m]) => {
          const regimeKey = m.regime?.regime || "ranging";
          const rs = REGIME_STYLE[regimeKey] || REGIME_STYLE.ranging;
          const rsi = m.regime?.rsi ?? 50;
          const ef = optData.pairRegimes[p];
          const ttp = ef?.trailing_tp_active;
          const allowBuys = regimeKey === "ranging" || regimeKey === "trend_up"
            || (regimeKey === "trend_down" && rsi < 40) || (regimeKey === "volatile" && rsi < 30);
          const allowSells = regimeKey === "ranging" || regimeKey === "trend_down"
            || (regimeKey === "trend_up" && rsi > 60) || (regimeKey === "volatile" && rsi > 70);

          const sentScore = m.regime?.sentiment_score ?? 0;
          const sentConf = m.regime?.sentiment_confidence ?? 0;
          const sentIcon = sentScore > 0.3 ? "\u25B2" : sentScore < -0.3 ? "\u25BC" : "\u2500";
          const sentColor = sentScore > 0.3 ? "var(--up)" : sentScore < -0.3 ? "var(--down)" : "var(--text-tertiary)";

          return (
            <div key={p} className="rounded-lg p-3" style={{ background: "var(--bg-secondary)" }}>
              <div className="flex items-center justify-between mb-2">
                <span className="text-[11px] font-bold">{p}</span>
                <div className="flex items-center gap-1.5">
                  <span className="inline-flex px-1.5 py-0.5 rounded text-[8px] font-bold" style={{ background: rs.bg, color: rs.color }}>{rs.label}</span>
                  <span title={`News: ${sentScore > 0 ? "+" : ""}${sentScore.toFixed(2)} (Conf ${(sentConf * 100).toFixed(0)}%)`} className="text-[10px] font-bold cursor-default" style={{ color: sentColor }}>{sentIcon}</span>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-1 text-[9px] mb-2">
                <div className="text-center"><span className="text-[var(--text-quaternary)]">RSI</span> <span className="font-mono font-bold" style={{ color: rsi > 70 ? "var(--down)" : rsi < 30 ? "var(--up)" : "var(--text-secondary)" }}>{rsi.toFixed(0)}</span></div>
                <div className="text-center"><span className="text-[var(--text-quaternary)]">ADX</span> <span className="font-mono font-bold">{(m.regime?.adx ?? 0).toFixed(0)}</span></div>
                <div className="text-center"><span className="text-[var(--text-quaternary)]">BollW</span> <span className="font-mono font-bold">{(m.regime?.boll_width ?? 0).toFixed(3)}</span></div>
              </div>
              <div className="flex flex-wrap gap-1">
                <span className="px-1.5 py-0.5 rounded text-[8px] font-semibold" style={{ background: allowBuys ? "var(--up-bg)" : "var(--down-bg)", color: allowBuys ? "var(--up)" : "var(--down)" }}>Buys {allowBuys ? "\u2713" : "\u2717"}</span>
                <span className="px-1.5 py-0.5 rounded text-[8px] font-semibold" style={{ background: allowSells ? "var(--up-bg)" : "var(--down-bg)", color: allowSells ? "var(--up)" : "var(--down)" }}>Sells {allowSells ? "\u2713" : "\u2717"}</span>
                {ttp !== undefined && (
                  <span className="px-1.5 py-0.5 rounded text-[8px] font-semibold" style={{ background: ttp ? "rgba(59,130,246,0.12)" : "var(--bg-elevated)", color: ttp ? "#3b82f6" : "var(--text-tertiary)" }}>
                    Trail-TP {ttp ? "AN" : "AUS"}{ef?.trailing_tp_count ? ` (${ef.trailing_tp_count})` : ""}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Last optimization + regime history */}
      {lastOpt && (
        <div className="rounded-lg p-2.5 mb-3" style={{ background: "var(--bg-secondary)" }}>
          <div className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider mb-1 font-semibold">Letzte Anpassung</div>
          <div className="text-[11px] text-[var(--text-secondary)]">{lastOpt.message}</div>
          <div className="text-[9px] text-[var(--text-tertiary)] mt-0.5 font-mono">
            {new Date(lastOpt.timestamp).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
          </div>
        </div>
      )}
      {optData.regimes.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-4">
          <span className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-semibold self-center">Regime-Verlauf:</span>
          {optData.regimes.slice(0, 5).map((ev) => {
            const d = ev.detail as Record<string, string> | null;
            const newR = d?.new || "ranging";
            const s = REGIME_STYLE[newR] || REGIME_STYLE.ranging;
            return (
              <span key={ev.id} className="inline-flex px-1.5 py-0.5 rounded text-[8px] font-bold"
                style={{ background: s.bg, color: s.color }}>
                {d?.pair?.split("/")[0]} {"\u2192"} {s.label}
                <span className="ml-1 font-normal opacity-60">
                  {new Date(ev.timestamp).toLocaleString("de-DE", { hour: "2-digit", minute: "2-digit" })}
                </span>
              </span>
            );
          })}
        </div>
      )}

      {/* RL Lern-Fortschritt */}
      {(rlStats && rlStats.episodes > 0) && (
        <div className="rounded-lg p-3 mt-1" style={{ background: "var(--bg-secondary)" }}>
          <div className="flex items-center justify-between mb-2.5">
            <div className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-semibold">Lern-Fortschritt</div>
            <span className="text-[8px] font-mono px-1.5 py-0.5 rounded font-bold" style={{
              background: rewardTrend >= 0 ? "var(--up-bg)" : "var(--down-bg)",
              color: rewardTrend >= 0 ? "var(--up)" : "var(--down)",
            }}>{rewardTrend >= 0 ? "\u2191" : "\u2193"} {avgReward >= 0 ? "+" : ""}{avgReward.toFixed(2)} avg</span>
          </div>

          {/* Reward Sparkline */}
          {last30.length >= 2 && (
            <div className="mb-2.5">
              <div className="text-[8px] text-[var(--text-quaternary)] mb-1">Reward (letzte {last30.length} Episoden)</div>
              <svg width="100%" height="36" viewBox={`0 0 ${last30.length - 1} 36`} preserveAspectRatio="none"
                style={{ display: "block", borderRadius: 4, background: "var(--bg-primary)" }}>
                <defs>
                  <linearGradient id="rlSparkGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={rewardTrend >= 0 ? "#10b981" : "#ef4444"} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={rewardTrend >= 0 ? "#10b981" : "#ef4444"} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <path d={
                  last30.map((r, i) => {
                    const x = i;
                    const y = 34 - ((r.reward - rewardMin) / rewardRange) * 30;
                    return `${i === 0 ? "M" : "L"}${x},${y}`;
                  }).join(" ") + ` L${last30.length - 1},36 L0,36 Z`
                } fill="url(#rlSparkGrad)" />
                <path d={
                  last30.map((r, i) => {
                    const x = i;
                    const y = 34 - ((r.reward - rewardMin) / rewardRange) * 30;
                    return `${i === 0 ? "M" : "L"}${x},${y}`;
                  }).join(" ")
                } fill="none" stroke={rewardTrend >= 0 ? "#10b981" : "#ef4444"} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
              </svg>
            </div>
          )}

          {/* Exploration Progress */}
          <div className="mb-2.5">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[8px] text-[var(--text-quaternary)]">Episode {rlStats.episodes} — {explorationPct.toFixed(1)}% Erkundung</span>
              <span className="text-[8px] font-mono" style={{ color: explorationPct > 10 ? "var(--warn)" : "var(--up)" }}>
                {explorationPct > 10 ? "Lernt" : "Nutzt"}
              </span>
            </div>
            <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-primary)" }}>
              <div className="h-full rounded-full transition-all" style={{
                width: `${explorationProgress}%`,
                background: `linear-gradient(90deg, var(--warn), var(--up))`,
              }} />
            </div>
            <div className="flex justify-between text-[7px] text-[var(--text-quaternary)] mt-0.5">
              <span>15% Exploration</span>
              <span>3% Exploitation</span>
            </div>
          </div>

          {/* Last Action */}
          {la && laAction && (
            <div className="mb-2.5 p-2 rounded" style={{ background: "var(--bg-primary)" }}>
              <div className="flex items-center gap-1.5 mb-1">
                <span className="text-[8px] text-[var(--text-quaternary)]">Letzte Aktion:</span>
                <span className="text-[7px] font-bold px-1 py-0.5 rounded" style={{
                  background: la.was_exploration ? "rgba(234,179,8,0.12)" : "var(--up-bg)",
                  color: la.was_exploration ? "#eab308" : "var(--up)",
                }}>{la.was_exploration ? "EXPLORATION" : "EXPLOITATION"}</span>
              </div>
              <div className="text-[9px] font-mono text-[var(--text-secondary)]">
                Spacing {fmtDelta(laAction.spacing_delta)}, Size {fmtDelta(laAction.size_delta)}, Range {fmtDelta(laAction.range_delta)}
              </div>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-[9px] font-mono" style={{ color: la.reward >= 0 ? "var(--up)" : "var(--down)" }}>
                  Reward: {la.reward >= 0 ? "+" : ""}{la.reward.toFixed(3)}
                </span>
                <span className="text-[8px] text-[var(--text-quaternary)]">
                  {zeitAgo(la.timestamp)}
                </span>
              </div>
            </div>
          )}

          {/* Reset Button */}
          <div className="flex justify-end">
            <button onClick={() => { if (confirm("RL-Policy wirklich zurücksetzen?")) onCommand("reset_rl"); }}
              className="px-2 py-1 rounded text-[8px] font-semibold transition-all active:scale-95"
              style={{ background: "var(--down-bg)", color: "var(--down)", border: "1px solid color-mix(in srgb, var(--down) 15%, transparent)" }}>
              Policy zurücksetzen
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// -- Bot Health --

function BotGesundheit({ pi, status, rlStats, sentimentEnabled }: {
  pi: PiStatus | null; status: BotStatus; rlStats: RLStats | null; sentimentEnabled: boolean;
}) {
  const sys = pi?.system;
  const pairs = Object.entries(status.pairStatuses || {}).filter(([k]) => !k.startsWith("__"));
  const sentSource = pairs.length > 0
    ? (pairs[0][1] as PairMetrics)?.regime?.sentiment_confidence !== undefined ? "aktiv" : "—"
    : "—";
  const rlEp = rlStats?.episodes ?? 0;
  const rlExpl = rlStats?.explorationRate ?? 0;
  const rlAvg = rlStats?.rewards?.length
    ? rlStats.rewards.slice(-20).reduce((s, r) => s + r.reward, 0) / Math.min(rlStats.rewards.length, 20)
    : 0;

  return (
    <div className="card p-4 sm:p-5 fade-in">
      <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">Bot-Gesundheit</h3>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-2">
        <div className="card-inner px-3 py-2 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">Uptime</p>
          <p className="text-[13px] font-bold font-mono mt-0.5">{laufzeit(status.startedAt)}</p>
        </div>
        <div className="card-inner px-3 py-2 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">Memory</p>
          <p className="text-[13px] font-bold font-mono mt-0.5" style={{
            color: sys?.ram_percent && sys.ram_percent > 80 ? "var(--down)" : sys?.ram_percent && sys.ram_percent > 60 ? "var(--warn)" : "var(--text-primary)"
          }}>
            {sys?.rss_kb ? `${Math.round(sys.rss_kb / 1024)} MB` : sys?.ram_used_mb ? `${sys.ram_used_mb} MB` : "\u2014"}
          </p>
        </div>
        <div className="card-inner px-3 py-2 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">CPU / Temp</p>
          <p className="text-[13px] font-bold font-mono mt-0.5" style={{
            color: sys?.cpu_temp && sys.cpu_temp > 75 ? "var(--down)" : sys?.cpu_temp && sys.cpu_temp > 60 ? "var(--warn)" : "var(--text-primary)"
          }}>
            {sys?.cpu_percent != null ? `${sys.cpu_percent}%` : "\u2014"}{sys?.cpu_temp != null ? ` / ${sys.cpu_temp}\u00B0C` : ""}
          </p>
        </div>
        <div className="card-inner px-3 py-2 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">Version</p>
          <p className="text-[13px] font-bold font-mono mt-0.5">
            {status.version && status.version.length >= 7 ? status.version.slice(0, 7) : status.version || "\u2014"}
          </p>
        </div>
      </div>

      {/* Sentiment + RL row */}
      <div className="grid grid-cols-2 gap-2">
        <div className="card-inner px-3 py-2">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">Sentiment</p>
          <p className="text-[11px] font-bold font-mono mt-0.5" style={{ color: sentimentEnabled && sentSource === "aktiv" ? "var(--up)" : "var(--text-tertiary)" }}>
            {sentimentEnabled ? `${sentSource} (local)` : "aus"}
          </p>
        </div>
        <div className="card-inner px-3 py-2">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">RL</p>
          {rlEp > 0 ? (
            <div>
              <p className="text-[11px] font-bold font-mono mt-0.5">Ep {rlEp}, {(rlExpl * 100).toFixed(1)}%</p>
              <p className="text-[8px] font-mono mt-0.5" style={{ color: rlAvg >= 0 ? "var(--up)" : "var(--down)" }}>
                Avg: {rlAvg >= 0 ? "+" : ""}{rlAvg.toFixed(2)}
              </p>
            </div>
          ) : (
            <p className="text-[11px] font-bold font-mono mt-0.5 text-[var(--text-tertiary)]">{"\u2014"}</p>
          )}
        </div>
      </div>

      {sys?.public_ip && (
        <div className="mt-2 flex items-center gap-2 text-[9px] text-[var(--text-quaternary)]">
          <span>IP: <span className="font-mono text-[var(--text-tertiary)] select-all">{sys.public_ip}</span></span>
          {sys.disk_percent != null && <span className="ml-auto">Disk: {sys.disk_percent}%</span>}
        </div>
      )}
    </div>
  );
}

// -- Activity Feed --

const EVT_ICONS: Record<string, string> = {
  trade: "T", grid: "G", error: "!", config: "C", system: "S",
  monitoring: "\u26A1", regime: "R", optimization: "\u2699", trailing_tp: "\u2197", memory: "M",
  sentiment: "\uD83D\uDCF0", rl_optimization: "\uD83E\uDDE0",
};
const EVT_COLORS: Record<string, string> = {
  trade: "var(--accent)", grid: "var(--cyan)", error: "var(--down)",
  warn: "var(--warn)", critical: "#ef4444", success: "var(--up)",
  config: "var(--text-secondary)", system: "var(--text-tertiary)",
  monitoring: "var(--warn)", regime: "#3b82f6", optimization: "var(--accent)",
  trailing_tp: "var(--up)", memory: "var(--warn)", sentiment: "#8b5cf6",
  rl_optimization: "#f59e0b",
};

const REGIME_STYLE: Record<string, { label: string; color: string; bg: string }> = {
  ranging:    { label: "SEITW\u00C4RTS", color: "var(--up)", bg: "var(--up-bg)" },
  trend_up:   { label: "AUFW\u00C4RTS", color: "#3b82f6", bg: "rgba(59,130,246,0.12)" },
  trend_down: { label: "ABW\u00C4RTS", color: "var(--warn)", bg: "var(--warn-bg)" },
  volatile:   { label: "VOLATIL", color: "var(--down)", bg: "var(--down-bg)" },
};

function AktivitaetsFeed({ events }: { events: BotEvent[] }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? events : events.slice(0, 12);

  if (!events.length) return null;

  return (
    <div className="card p-4 sm:p-5 h-full">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold">Aktivitaet</h3>
        <span className="text-[9px] text-[var(--text-quaternary)]">{events.length}</span>
      </div>
      <div className="space-y-0.5 max-h-80 overflow-y-auto">
        {shown.map((ev) => {
          const levelColor = ev.level === "critical" ? "critical" : ev.level === "warn" ? "warn" : ev.level === "error" ? "error" : ev.level === "success" ? "success" : "";
          const col = EVT_COLORS[levelColor || ev.category] || "var(--text-tertiary)";
          const icon = EVT_ICONS[ev.category] || "\u00B7";
          const isCritical = ev.level === "critical";
          const zeit = new Date(ev.timestamp).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
          return (
            <div key={ev.id} className="flex items-start gap-2 text-[10px] py-1.5 px-2 rounded hover:bg-[var(--bg-secondary)] transition-colors"
              style={isCritical ? { background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)" } : undefined}>
              <span className="shrink-0 w-4 h-4 rounded flex items-center justify-center text-[8px] font-bold mt-0.5"
                style={{ background: `color-mix(in srgb, ${col} 15%, transparent)`, color: col }}>
                {icon}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[var(--text-primary)] leading-snug">{ev.message}</p>
                {ev.detail && (
                  <p className="text-[9px] text-[var(--text-quaternary)] mt-0.5 font-mono truncate">
                    {Object.entries(ev.detail as Record<string, unknown>)
                      .filter(([k]) => !["pair"].includes(k))
                      .slice(0, 4)
                      .map(([k, v]) => `${k}: ${typeof v === "number" ? (Number.isInteger(v) ? v : (v as number).toFixed(4)) : v}`)
                      .join(" \u00B7 ")}
                  </p>
                )}
              </div>
              <span className="shrink-0 text-[9px] text-[var(--text-quaternary)] font-mono mt-0.5">{zeit}</span>
            </div>
          );
        })}
      </div>
      {events.length > 12 && (
        <button onClick={() => setExpanded(!expanded)}
          className="mt-2 text-[10px] text-[var(--accent)] hover:underline w-full text-center">
          {expanded ? "Weniger anzeigen" : `Alle ${events.length} Events anzeigen`}
        </button>
      )}
    </div>
  );
}

// -- Controls --

function Steuerung({ status, commands, onCommand, botConfig }: {
  status: string; commands: CommandRecord[]; onCommand: (t: string) => void; botConfig: Record<string, unknown> | null;
}) {
  const [logLoading, setLogLoading] = useState(false);
  const sentCfg = botConfig?.sentiment as Record<string, unknown> | undefined;
  const rlCfg = botConfig?.rl as Record<string, unknown> | undefined;
  const [sentEnabled, setSentEnabled] = useState(true);
  const [rlEnabled, setRlEnabled] = useState(false);
  const [sentProvider, setSentProvider] = useState("local");
  const [configLoaded, setConfigLoaded] = useState(false);

  useEffect(() => {
    if (!botConfig || configLoaded) return;
    if (sentCfg) {
      if (typeof sentCfg.enabled === "boolean") setSentEnabled(sentCfg.enabled);
      if (typeof sentCfg.provider === "string") setSentProvider(sentCfg.provider);
    }
    if (rlCfg) {
      if (typeof rlCfg.enabled === "boolean") setRlEnabled(rlCfg.enabled);
    }
    setConfigLoaded(true);
  }, [botConfig, configLoaded, sentCfg, rlCfg]);
  const laeuft = status === "running";
  const gestoppt = status === "stopped" || status === "paused";
  const stLabels: Record<string, string> = { completed: "OK", failed: "Fehler", pending: "..." };
  const labels: Record<string, string> = { stop: "Stoppen", resume: "Fortsetzen", pause: "Pausieren", status: "Status", performance: "Performance", update_config: "Config", update_software: "Update", fetch_logs: "Logs", reset_rl: "RL Reset" };

  const sendConfig = (section: string, key: string, value: unknown) => {
    fetch("/api/commands", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "update_config", payload: { [section]: { [key]: value } } }),
    });
  };

  const btn = (t: string, lbl: string, col: string) => (
    <button key={t} onClick={() => onCommand(t)}
      className="px-3 py-2 rounded-lg text-[10px] font-semibold transition-all active:scale-95"
      style={{ background: `color-mix(in srgb, ${col} 10%, transparent)`, color: col, border: `1px solid color-mix(in srgb, ${col} 15%, transparent)` }}
    >{lbl}</button>
  );

  const downloadLogs = async () => {
    setLogLoading(true);
    try {
      const res = await fetch("/api/commands", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: "fetch_logs" }),
      });
      if (!res.ok) { setLogLoading(false); return; }
      const cmd = await res.json();
      const cmdId = cmd.id;

      for (let i = 0; i < 20; i++) {
        await new Promise(r => setTimeout(r, 1500));
        const check = await fetch(`/api/commands?limit=1`, { cache: "no-store" });
        const list = await check.json();
        const found = (list as CommandRecord[]).find((c: CommandRecord) => c.id === cmdId);
        if (!found || found.status === "pending") continue;
        if (found.status === "completed" && found.result) {
          const logs = (found.result as { logs?: string }).logs || "Keine Logs";
          const blob = new Blob([logs], { type: "text/plain" });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `richbot-logs-${new Date().toISOString().slice(0, 16).replace(/[T:]/g, "-")}.txt`;
          a.click();
          URL.revokeObjectURL(url);
          break;
        }
        if (found.status === "failed") break;
      }
    } catch { /* ignore */ }
    setLogLoading(false);
  };

  return (
    <div className="card p-4 sm:p-5 h-full">
      <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">Steuerung</h3>
      <div className="flex flex-wrap gap-1.5 mb-4">
        {laeuft && <>{btn("pause", "Pausieren", "var(--warn)")}{btn("stop", "Stoppen", "var(--down)")}</>}
        {gestoppt && btn("resume", "Fortsetzen", "var(--up)")}
        {btn("status", "Status", "var(--accent)")}
        {btn("update_software", "Update", "var(--cyan)")}
        <button onClick={downloadLogs} disabled={logLoading}
          className="px-3 py-2 rounded-lg text-[10px] font-semibold transition-all active:scale-95 disabled:opacity-50"
          style={{ background: "color-mix(in srgb, var(--text-secondary) 10%, transparent)", color: "var(--text-secondary)", border: "1px solid color-mix(in srgb, var(--text-secondary) 15%, transparent)" }}
        >{logLoading ? "Lade..." : "Logs"}</button>
      </div>
      {/* Feature-Toggles */}
      <div className="rounded-lg p-2.5 mb-3" style={{ background: "var(--bg-secondary)" }}>
        <div className="text-[8px] text-[var(--text-quaternary)] uppercase tracking-wider mb-2 font-semibold">KI-Features</div>
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-[var(--text-secondary)]">Sentiment</span>
            <div className="flex items-center gap-2">
              <select value={sentProvider} onChange={(e) => { setSentProvider(e.target.value); sendConfig("sentiment", "provider", e.target.value); }}
                className="text-[9px] bg-transparent border border-[var(--border)] rounded px-1.5 py-0.5 text-[var(--text-secondary)] outline-none">
                <option value="local">Local</option>
                <option value="grok">Grok</option>
                <option value="openai">OpenAI</option>
              </select>
              <button onClick={() => { const v = !sentEnabled; setSentEnabled(v); sendConfig("sentiment", "enabled", v); }}
                className="w-8 h-4 rounded-full relative transition-all" style={{ background: sentEnabled ? "var(--up)" : "var(--bg-elevated)" }}>
                <span className="absolute w-3 h-3 rounded-full bg-white top-0.5 transition-all" style={{ left: sentEnabled ? 17 : 2 }} />
              </button>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-[var(--text-secondary)]">RL-Optimizer</span>
            <button onClick={() => { const v = !rlEnabled; setRlEnabled(v); sendConfig("rl", "enabled", v); }}
              className="w-8 h-4 rounded-full relative transition-all" style={{ background: rlEnabled ? "var(--up)" : "var(--bg-elevated)" }}>
              <span className="absolute w-3 h-3 rounded-full bg-white top-0.5 transition-all" style={{ left: rlEnabled ? 17 : 2 }} />
            </button>
          </div>
        </div>
      </div>

      {commands.length > 0 && (
        <div className="space-y-1 max-h-28 overflow-y-auto">
          {commands.slice(0, 8).map((c) => (
            <div key={c.id} className="flex items-center gap-2 text-[10px] py-1 px-2 rounded hover:bg-[var(--bg-secondary)]">
              <span className="text-[var(--text-quaternary)] font-mono w-9 shrink-0">
                {new Date(c.createdAt).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}
              </span>
              <span className="text-[var(--text-tertiary)]">{labels[c.type] || c.type}</span>
              <span className="ml-auto text-[8px] font-bold px-1 py-0.5 rounded" style={{
                background: c.status === "completed" ? "var(--up-bg)" : c.status === "failed" ? "var(--down-bg)" : "var(--warn-bg)",
                color: c.status === "completed" ? "var(--up)" : c.status === "failed" ? "var(--down)" : "var(--warn)",
              }}>{stLabels[c.status] || c.status}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// -- Analytics --

function AnalyticsPanel({ data }: { data: AnalyticsData }) {
  const s = data.summary;
  if (!s) return null;

  return (
    <div className="card p-4 sm:p-5 fade-in">
      <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-4">Analyse</h3>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
        <div className="p-2.5 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase mb-0.5">Win-Rate</p>
          <p className={`text-sm font-bold font-mono ${s.win_rate >= 50 ? "text-[var(--up)]" : "text-[var(--down)]"}`}>{s.win_rate.toFixed(1)}%</p>
          <p className="text-[9px] text-[var(--text-quaternary)]">{s.wins}W / {s.losses}L</p>
        </div>
        <div className="p-2.5 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase mb-0.5">Netto PnL</p>
          <p className={`text-sm font-bold font-mono ${s.net_pnl >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"}`}>{s.net_pnl >= 0 ? "+" : ""}{fmt(s.net_pnl, 4)}</p>
          <p className="text-[9px] text-[var(--text-quaternary)]">Gebuehren: {fmt(s.total_fees, 4)}</p>
        </div>
        <div className="p-2.5 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase mb-0.5">Profit-Faktor</p>
          <p className={`text-sm font-bold font-mono ${s.profit_factor >= 1 ? "text-[var(--up)]" : "text-[var(--down)]"}`}>{s.profit_factor.toFixed(2)}</p>
          <p className="text-[9px] text-[var(--text-quaternary)]">{"\u00D8"} +{fmt(s.avg_win, 4)} / {fmt(s.avg_loss, 4)}</p>
        </div>
        <div className="p-2.5 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase mb-0.5">Streaks</p>
          <p className="text-sm font-bold font-mono">
            <span className="text-[var(--up)]">{s.max_win_streak}</span>
            <span className="text-[var(--text-quaternary)]"> / </span>
            <span className="text-[var(--down)]">{s.max_loss_streak}</span>
          </p>
          <p className="text-[9px] text-[var(--text-quaternary)]">{s.total_trades} Trades</p>
        </div>
      </div>

      {data.hourly_pnl.length > 0 && (
        <div>
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase tracking-[0.14em] font-semibold mb-2">PnL pro Stunde</p>
          <div className="flex flex-wrap gap-0.5">
            {data.hourly_pnl.slice(-24).map((h) => {
              const intensity = Math.min(Math.abs(h.pnl) * 500, 1);
              const col = h.pnl >= 0 ? `rgba(16,185,129,${0.15 + intensity * 0.85})` : `rgba(239,68,68,${0.15 + intensity * 0.85})`;
              const label = h.hour.slice(11, 13) + "h";
              return (
                <div key={h.hour} className="flex flex-col items-center gap-0.5" title={`${label}: ${h.pnl >= 0 ? "+" : ""}${h.pnl.toFixed(4)} (${h.count} Trades)`}>
                  <div className="w-5 h-5 rounded-sm" style={{ background: col }} />
                  <span className="text-[7px] text-[var(--text-quaternary)]">{label}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// -- Dashboard --

export default function Dashboard() {
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [commands, setCommands] = useState<CommandRecord[]>([]);
  const [events, setEvents] = useState<BotEvent[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsData | null>(null);
  const [optData, setOptData] = useState<{ optimizations: BotEvent[]; regimes: BotEvent[]; pairRegimes: Record<string, { regime: PairMetrics["regime"]; allocation: PairMetrics["allocation"]; trailing_tp_count: number; trailing_tp_active: boolean }> }>({ optimizations: [], regimes: [], pairRegimes: {} });
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
  }, [demoEquity, demoTrades]);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 5000);
    return () => clearInterval(iv);
  }, [refresh]);

  useEffect(() => {
    fetch("https://api.github.com/repos/wakemaster88/richbot/commits/main", { cache: "no-store" })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.sha) setLatestCommit(d.sha.slice(0, 7)); })
      .catch(() => {});
  }, []);

  const handleCommand = async (type: string) => {
    if (isDemo) return;
    await postCommand(type);
    setTimeout(refresh, 1000);
  };

  const status = botStatus || DEMO_STATUS;
  const rawStatuses = (status.pairStatuses || {}) as Record<string, PairMetrics | WalletData>;
  const walletData = (rawStatuses["__wallet__"] || null) as WalletData | null;
  const pairs = Object.entries(rawStatuses).filter(([k]) => k !== "__wallet__") as [string, PairMetrics][];
  const quoteCcy = status.pairs?.[0]?.split("/")?.[1] || "USDC";
  const totalPnl = pairs.reduce((s, [, m]) => s + (m.total_pnl || 0), 0);
  const walletTotal = walletData?._total_usdc ?? pairs.reduce((s, [, m]) => s + (m.current_equity || 0), 0);

  const allTrailingTp = pairs.flatMap(([, m]) => m.trailing_tp || []);

  const targetRatio = useMemo(() => {
    if (!pairs.length) return undefined;
    const regimes = pairs.map(([, m]) => m.regime?.regime || "ranging");
    const ratios = regimes.map(r => r === "trend_down" ? 0.7 : r === "trend_up" ? 0.3 : 0.5);
    return ratios.reduce((s, v) => s + v, 0) / ratios.length;
  }, [pairs]);

  const pnlData = useMemo(() => {
    if (isDemo) return demoPnl;
    if (!trades.length) return [];
    const buckets: Record<string, number> = {};
    for (const t of trades) {
      const h = new Date(t.timestamp).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }).replace(/:\d{2}$/, ":00");
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
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-4">
          <Skeleton h={300} />
          <Skeleton h={300} />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <Skeleton h={250} />
          <Skeleton h={250} />
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-[1400px] mx-auto px-4 py-4 sm:px-6 pb-12">
      {/* Banners */}
      {isDemo && (
        <div className="mb-3 px-3 py-2 rounded-lg text-[10px] font-medium text-center" style={{ background: "var(--accent-bg)", color: "var(--accent)", border: "1px solid color-mix(in srgb, var(--accent) 15%, transparent)" }}>
          Demo-Modus — Datenbank nicht erreichbar
        </div>
      )}
      {!isDemo && status.status === "waiting" && (
        <div className="mb-3 px-3 py-2 rounded-lg text-[10px] font-medium text-center" style={{ background: "var(--warn-bg)", color: "var(--warn)", border: "1px solid color-mix(in srgb, var(--warn) 15%, transparent)" }}>
          Warte auf Raspberry Pi...
        </div>
      )}

      {/* Header */}
      <header className="flex items-center justify-between gap-3 mb-4">
        <div className="flex items-center gap-2">
          <StatusBadge status={status.status} hb={status.lastHeartbeat} />
          {(() => {
            const critCount = events.filter(e => e.level === "critical" || e.level === "error").length;
            const warnCount = events.filter(e => e.level === "warn").length;
            return (
              <>
                {critCount > 0 && (
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold"
                    style={{ background: "rgba(239,68,68,0.12)", color: "#ef4444" }}>
                    {"\u26A0"} {critCount}
                  </span>
                )}
                {warnCount > 0 && critCount === 0 && (
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold"
                    style={{ background: "var(--warn-bg)", color: "var(--warn)" }}>
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
                {isUpToDate && <span className="inline-block w-1.5 h-1.5 rounded-full bg-[var(--up)]" />}
                {isOutdated && <span className="inline-block w-1.5 h-1.5 rounded-full bg-[var(--warn)]" />}
                <span style={{ color: isOutdated ? "var(--warn)" : undefined }}>
                  {isHash ? piVer : `v${piVer}`}
                </span>
                {isOutdated && <span style={{ color: "var(--warn)" }}>(Update: {latestCommit})</span>}
              </span>
            );
          })()}
        </div>
      </header>

      {/* 1. Portfolio Hero + PnL + Regime */}
      <PortfolioHero walletTotal={walletTotal} totalPnl={totalPnl} trades={trades} quoteCcy={quoteCcy} pairs={pairs} />

      {/* 2. Kapital-Verteilung with target ratio */}
      {walletData && <WalletUebersicht wallet={walletData} targetRatio={targetRatio}
        pairStats={analytics?.pair_stats} equity={equity} />}

      {/* 3. Per-Pair: Price Chart (with grid info) + Info */}
      {pairs.length > 0 ? pairs.map(([p, m]) => (
        <div key={p} className="grid grid-cols-1 lg:grid-cols-12 gap-3 mb-3">
          <div className="lg:col-span-8">
            <PreisChart pair={p} orders={m.open_orders} trades={trades.filter(t => t.pair === p)} quote={quoteCcy}
              gridMeta={{ levels: m.grid_levels, configured: m.grid_configured, buyCount: m.grid_buy_count, sellCount: m.grid_sell_count, range: m.range, issue: m.grid_issue, unplaced: m.unplaced_orders }} />
          </div>
          <div className="lg:col-span-4">
            <PairInfoCard pair={p} m={m} quote={quoteCcy} />
          </div>
        </div>
      )) : (
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-3 mb-3">
          <div className="lg:col-span-3"><PreisChart pair="BTC/USDC" trades={trades.filter(t => t.pair === "BTC/USDC")} quote={quoteCcy} /></div>
          <div className="lg:col-span-2 card p-5 flex items-center justify-center text-[11px] text-[var(--text-quaternary)]">Keine Paare aktiv</div>
        </div>
      )}

      {/* 4. Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-3">
        <EquityChart data={equity} quote={quoteCcy} />
        {pnlData.length > 0
          ? <PnlChart data={pnlData} quote={quoteCcy} />
          : <div className="card p-5 h-full flex items-center justify-center text-[10px] text-[var(--text-quaternary)]">PnL-Chart nach ersten Trades</div>
        }
      </div>

      {/* 5. Trades with Trailing-TP + Self-Optimization */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-3">
        <TradesTabelle trades={trades} trailingTp={allTrailingTp.length > 0 ? allTrailingTp : undefined} quote={quoteCcy} />
        {!isDemo && pairs.length > 0 && (
          <SelbstOptimierungPanel optData={optData} pairs={pairs} rlStats={rlStats} onCommand={handleCommand} />
        )}
      </div>

      {/* 6. Analytics */}
      {!isDemo && analytics && <div className="mb-3"><AnalyticsPanel data={analytics} /></div>}

      {/* 7. Bot Health + Activity + Controls */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <BotGesundheit pi={piStatus} status={status} rlStats={rlStats} sentimentEnabled={!isDemo} />
        <Steuerung status={status.status} commands={commands} onCommand={handleCommand} botConfig={botConfig} />
        {!isDemo && events.length > 0 && <AktivitaetsFeed events={events} />}
      </div>
    </div>
  );
}
