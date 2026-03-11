"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  AreaChart, Area, BarChart, Bar, ComposedChart, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Cell, ReferenceLine,
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
  grid_levels: number; grid_configured?: number; active_orders: number; filled_orders: number;
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

function MiniStat({ label, wert, farbe }: { label: string; wert: string; farbe?: string }) {
  return (
    <div className="card-inner px-3 py-2.5">
      <p className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-medium">{label}</p>
      <p className={`text-[15px] font-bold font-mono tracking-tight mt-0.5 ${farbe || "text-[var(--text-primary)]"}`}>{wert}</p>
    </div>
  );
}

function PairKarte({ pair, m, quote = "USDC" }: { pair: string; m: PairMetrics; quote?: string }) {
  const up = m.total_pnl >= 0;

  return (
    <div className="card p-4 sm:p-5 fade-in">
      {/* Header */}
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

      {/* Stats Grid */}
      <div className="grid grid-cols-3 gap-2 mb-3">
        <MiniStat label="Grid" wert={`${m.active_orders}/${m.grid_configured || m.grid_levels}`} />
        <MiniStat label="Trades" wert={`${m.trade_count}`} />
        <MiniStat label="Drawdown" wert={`${fmt(m.max_drawdown_pct)}%`} farbe="text-[var(--warn)]" />
      </div>

      {/* Open Orders */}
      {m.open_orders && m.open_orders.length > 0 && (
        <div className="pt-3 border-t border-[var(--border-subtle)]">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase tracking-[0.14em] font-semibold mb-1.5">Offene Orders</p>
          <div className="grid grid-cols-2 gap-1">
            {m.open_orders.map((o) => {
              const isBuy = o.side === "buy";
              const dist = m.price > 0 ? ((o.price - m.price) / m.price * 100) : 0;
              return (
                <div key={o.id} className="flex items-center justify-between text-[10px] font-mono px-2 py-1 rounded-md" style={{ background: isBuy ? "var(--up-bg)" : "var(--down-bg)" }}>
                  <span style={{ color: isBuy ? "var(--up)" : "var(--down)" }}>{isBuy ? "K" : "V"} {fmt(o.price, 0)}</span>
                  <span className="text-[var(--text-quaternary)]">{dist >= 0 ? "+" : ""}{dist.toFixed(1)}%</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Footer */}
      {(m.annualized_return_pct || m.fees_paid) && (
        <div className="flex items-center gap-3 mt-3 pt-3 border-t border-[var(--border-subtle)] text-[9px] text-[var(--text-quaternary)]">
          {m.annualized_return_pct !== undefined && <span>Rendite: <strong className="text-[var(--text-tertiary)]">{fmt(m.annualized_return_pct)}%</strong></span>}
          {m.fees_paid !== undefined && <span>Gebuehren: <strong className="text-[var(--text-tertiary)]">{fmt(m.fees_paid)}</strong></span>}
          <span>Kapital: <strong className="text-[var(--text-tertiary)]">{fmt(m.current_equity)}</strong></span>
        </div>
      )}
    </div>
  );
}

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

interface Kline { t: number; o: number; h: number; l: number; c: number; v: number; }

function PreisChart({ pair, orders, quote = "USDC" }: { pair: string; orders?: OpenOrder[]; quote?: string }) {
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

  const data = klines.map((k) => ({
    zeit: new Date(k.t).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }),
    preis: k.c, hoch: k.h, tief: k.l,
  }));

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
        <ComposedChart data={data} margin={{ top: 0, right: 0, left: -15, bottom: 0 }}>
          <defs>
            <linearGradient id="prG" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={col} stopOpacity={0.12} />
              <stop offset="100%" stopColor={col} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="zeit" tick={{ fill: "var(--text-quaternary)", fontSize: 9 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "var(--text-quaternary)", fontSize: 9 }} axisLine={false} tickLine={false} domain={[mn - pad, mx + pad]} width={55} tickFormatter={(v) => fmt(v, 0)} />
          <Tooltip
            contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border-accent)", borderRadius: 10, padding: "6px 10px", fontSize: 11 }}
            formatter={(v: number, name: string) => [fmt(v) + " " + quote, name === "preis" ? "Preis" : name === "hoch" ? "Hoch" : "Tief"]}
          />
          {(orders || []).map((o) => (
            <ReferenceLine key={o.id} y={o.price} stroke={o.side === "buy" ? "var(--up)" : "var(--down)"} strokeDasharray="4 3" strokeOpacity={0.5}
              label={{ value: `${o.side === "buy" ? "K" : "V"} ${fmt(o.price, 0)}`, fill: o.side === "buy" ? "var(--up)" : "var(--down)", fontSize: 8, position: "right" }} />
          ))}
          <Area type="monotone" dataKey="preis" stroke={col} strokeWidth={1.5} fill="url(#prG)" dot={false} activeDot={{ r: 3, fill: col, strokeWidth: 0 }} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

function TradesTabelle({ trades, quote = "USDC" }: { trades: Trade[]; quote?: string }) {
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

function Steuerung({ status, commands, onCommand }: {
  status: string; commands: CommandRecord[]; onCommand: (t: string) => void;
}) {
  const laeuft = status === "running";
  const gestoppt = status === "stopped" || status === "paused";
  const stLabels: Record<string, string> = { completed: "OK", failed: "Fehler", pending: "..." };
  const labels: Record<string, string> = { stop: "Stoppen", resume: "Fortsetzen", pause: "Pausieren", status: "Status", performance: "Performance", update_config: "Config", update_software: "Update" };

  const btn = (t: string, lbl: string, col: string) => (
    <button key={t} onClick={() => onCommand(t)}
      className="px-3 py-2 rounded-lg text-[10px] font-semibold transition-all active:scale-95"
      style={{ background: `color-mix(in srgb, ${col} 10%, transparent)`, color: col, border: `1px solid color-mix(in srgb, ${col} 15%, transparent)` }}
    >{lbl}</button>
  );

  return (
    <div className="card p-4 sm:p-5 h-full">
      <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">Steuerung</h3>
      <div className="flex flex-wrap gap-1.5 mb-4">
        {laeuft && <>{btn("pause", "Pausieren", "var(--warn)")}{btn("stop", "Stoppen", "var(--down)")}</>}
        {gestoppt && btn("resume", "Fortsetzen", "var(--up)")}
        {btn("status", "Status", "var(--accent)")}
        {btn("update_software", "Update", "var(--cyan)")}
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

// -- Dashboard --

export default function Dashboard() {
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [commands, setCommands] = useState<CommandRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [isDemo, setIsDemo] = useState(false);

  const demoEquity = useMemo(() => generateDemoEquity(), []);
  const demoPnl = useMemo(() => generateDemoPnl(), []);
  const demoTrades = useMemo(() => generateDemoTrades(), []);

  const refresh = useCallback(async () => {
    const [s, t, e, c] = await Promise.all([
      fetchJson<BotStatus>("/api/status"),
      fetchJson<Trade[]>("/api/trades?limit=50"),
      fetchJson<EquityPoint[]>("/api/equity?hours=24"),
      fetchJson<CommandRecord[]>("/api/commands?limit=20"),
    ]);

    if (s?.dbConnected) {
      setBotStatus(s);
      setTrades(t || []);
      setEquity(e || []);
      setCommands(c || []);
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

  const handleCommand = async (type: string) => {
    if (isDemo) return;
    await postCommand(type);
    setTimeout(refresh, 1000);
  };

  const status = botStatus || DEMO_STATUS;
  const pairs = Object.entries((status.pairStatuses || {}) as Record<string, PairMetrics>);
  const quoteCcy = status.pairs?.[0]?.split("/")?.[1] || "USDC";
  const totalPnl = pairs.reduce((s, [, m]) => s + (m.total_pnl || 0), 0);
  const totalTrades = pairs.reduce((s, [, m]) => s + (m.trade_count || 0), 0);
  const totalEquity = pairs.reduce((s, [, m]) => s + (m.current_equity || 0), 0);
  const maxDd = Math.max(...pairs.map(([, m]) => m.max_drawdown_pct || 0), 0);

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
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
          {[1,2,3,4].map(i => <Skeleton key={i} h={70} />)}
        </div>
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
        <StatusBadge status={status.status} hb={status.lastHeartbeat} />
        <div className="flex items-center gap-2 text-[10px] text-[var(--text-quaternary)]">
          <span>{laufzeit(status.startedAt)}</span>
          <span className="w-px h-2.5 bg-[var(--border)]" />
          <span>v{status.version}</span>
        </div>
      </header>

      {/* Quick Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
        <MiniStat label="Gesamt-PnL" wert={`${totalPnl >= 0 ? "+" : ""}${fmt(totalPnl, 4)}`} farbe={totalPnl >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"} />
        <MiniStat label={`Kapital (${quoteCcy})`} wert={fmt(totalEquity)} />
        <MiniStat label="Trades" wert={totalTrades.toLocaleString("de-DE")} />
        <MiniStat label="Drawdown" wert={`${fmt(maxDd)}%`} farbe={maxDd > 5 ? "text-[var(--down)]" : "text-[var(--warn)]"} />
      </div>

      {/* Pair Sections */}
      {pairs.length > 0 ? pairs.map(([p, m]) => (
        <div key={p} className="grid grid-cols-1 lg:grid-cols-5 gap-3 mb-3">
          <div className="lg:col-span-3">
            <PreisChart pair={p} orders={m.open_orders} quote={quoteCcy} />
          </div>
          <div className="lg:col-span-2">
            <PairKarte pair={p} m={m} quote={quoteCcy} />
          </div>
        </div>
      )) : (
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-3 mb-3">
          <div className="lg:col-span-3"><PreisChart pair="BTC/USDC" quote={quoteCcy} /></div>
          <div className="lg:col-span-2 card p-5 flex items-center justify-center text-[11px] text-[var(--text-quaternary)]">Keine Paare aktiv</div>
        </div>
      )}

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-3">
        <EquityChart data={equity} quote={quoteCcy} />
        {pnlData.length > 0
          ? <PnlChart data={pnlData} quote={quoteCcy} />
          : <div className="card p-5 h-full flex items-center justify-center text-[10px] text-[var(--text-quaternary)]">PnL-Chart nach ersten Trades</div>
        }
      </div>

      {/* Trades + Controls */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <div className="lg:col-span-2"><TradesTabelle trades={trades} quote={quoteCcy} /></div>
        <Steuerung status={status.status} commands={commands} onCommand={handleCommand} />
      </div>
    </div>
  );
}
