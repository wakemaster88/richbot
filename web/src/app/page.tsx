"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Cell,
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
  grid_levels: number; active_orders: number; filled_orders: number;
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
  const pairs = ["BTC/USDC"];
  const now = Date.now();
  return Array.from({ length: 20 }, (_, i) => {
    const side = Math.random() > 0.5 ? "buy" : "sell";
    const pair = pairs[Math.floor(Math.random() * pairs.length)];
    const price = 87000 + (Math.random() - 0.5) * 2000;
    return {
      id: `demo-${i}`, timestamp: new Date(now - i * 180000 * Math.random() * 5).toISOString(),
      pair, side, price: parseFloat(price.toFixed(2)),
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

function fmtBtc(n: number): string {
  return n.toLocaleString("de-DE", { minimumFractionDigits: 8, maximumFractionDigits: 8 });
}

function fmtSats(n: number): string {
  const sats = Math.round(n * 1e8);
  return sats.toLocaleString("de-DE") + " sat";
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

function Stat({ label, wert, sub, farbe, klein }: {
  label: string; wert: string; sub?: string; farbe?: string; klein?: boolean;
}) {
  return (
    <div className="card card-hover p-4 transition-all">
      <p className="text-[10px] text-[var(--text-tertiary)] uppercase tracking-[0.1em] font-medium mb-1.5">{label}</p>
      <p className={`${klein ? "text-lg" : "text-[22px]"} font-bold font-mono tracking-tight leading-none ${farbe || ""}`} style={farbe ? {} : { color: "var(--text-primary)" }}>
        {wert}
      </p>
      {sub && <p className="text-[10px] text-[var(--text-tertiary)] mt-1.5">{sub}</p>}
    </div>
  );
}

function PairKarte({ pair, m, quote = "USDC" }: { pair: string; m: PairMetrics; quote?: string }) {
  const up = m.total_pnl >= 0;
  const rows = [
    { l: `Preis (${quote})`, v: fmt(m.price), c: "" },
    { l: "Grid", v: `${m.active_orders}/${m.grid_levels}`, c: "" },
    { l: "Trades", v: `${m.trade_count}`, c: "" },
    { l: `PnL (${quote})`, v: `${up ? "+" : ""}${fmt(m.total_pnl, 4)}`, c: up ? "text-[var(--up)]" : "text-[var(--down)]" },
    { l: "Drawdown", v: `${fmt(m.max_drawdown_pct)}%`, c: "text-[var(--warn)]" },
    { l: "Sharpe", v: fmt(m.sharpe_ratio), c: m.sharpe_ratio > 1.5 ? "text-[var(--up)]" : "" },
  ];

  return (
    <div className="card card-hover p-5 transition-all fade-in">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center font-bold text-xs" style={{ background: up ? "var(--up-bg)" : "var(--down-bg)", color: up ? "var(--up)" : "var(--down)" }}>
            {pair.split("/")[0]}
          </div>
          <div>
            <h3 className="font-semibold text-[15px] leading-tight">{pair}</h3>
            <p className="text-[10px] text-[var(--text-tertiary)] font-mono">{m.range} · {m.range_source}</p>
          </div>
        </div>
        <div className="text-right">
          <p className="text-lg font-bold font-mono">{fmt(m.price)} <span className="text-xs text-[var(--text-tertiary)]">{quote}</span></p>
          <p className={`text-xs font-mono font-semibold ${up ? "text-[var(--up)]" : "text-[var(--down)]"}`}>
            {up ? "+" : ""}{fmt(m.total_pnl, 4)} {quote}
          </p>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        {rows.map((r) => (
          <div key={r.l} className="card-inner px-3 py-2">
            <p className="text-[9px] text-[var(--text-tertiary)] uppercase tracking-wider">{r.l}</p>
            <p className={`text-sm font-mono font-semibold mt-0.5 ${r.c}`}>{r.v}</p>
          </div>
        ))}
      </div>
      {(m.annualized_return_pct || m.fees_paid) && (
        <div className="flex items-center gap-4 mt-3 pt-3 border-t border-[var(--border-subtle)] text-[10px] text-[var(--text-tertiary)]">
          {m.annualized_return_pct !== undefined && <span>Jahresrendite: <strong className="text-[var(--text-secondary)]">{fmt(m.annualized_return_pct)}%</strong></span>}
          {m.fees_paid !== undefined && <span>Gebuhren: <strong className="text-[var(--text-secondary)]">{fmt(m.fees_paid)} {quote}</strong></span>}
          <span>Kapital: <strong className="text-[var(--text-secondary)]">{fmt(m.current_equity)} {quote}</strong></span>
        </div>
      )}
      {m.open_orders && m.open_orders.length > 0 && (
        <div className="mt-3 pt-3 border-t border-[var(--border-subtle)]">
          <p className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-medium mb-2">Offene Orders</p>
          <div className="space-y-1">
            {m.open_orders.map((o) => {
              const isBuy = o.side === "buy";
              const dist = m.price > 0 ? ((o.price - m.price) / m.price * 100) : 0;
              return (
                <div key={o.id} className="flex items-center justify-between text-[11px] font-mono px-2.5 py-1.5 rounded-lg" style={{ background: isBuy ? "var(--up-bg)" : "var(--down-bg)" }}>
                  <div className="flex items-center gap-2">
                    <span className="text-[9px] font-bold px-1.5 py-0.5 rounded" style={{ color: isBuy ? "var(--up)" : "var(--down)" }}>
                      {isBuy ? "KAUF" : "VERK."}
                    </span>
                    <span className="text-[var(--text-secondary)]">{fmt(o.price)} {quote}</span>
                  </div>
                  <div className="flex items-center gap-3 text-[var(--text-tertiary)]">
                    <span>{fmtSats(o.amount)}</span>
                    <span className="text-[10px]" style={{ color: dist < 0 ? "var(--up)" : "var(--down)" }}>
                      {dist >= 0 ? "+" : ""}{dist.toFixed(1)}%
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
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
    <div className="card p-5">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-xs text-[var(--text-tertiary)] uppercase tracking-[0.1em] font-medium">Kapitalverlauf</h3>
        <div className="flex items-center gap-3 text-[11px] text-[var(--text-tertiary)]">
          <span>Min: <strong className="text-[var(--text-secondary)] font-mono">{fmt(mn)}</strong></span>
          <span>Max: <strong className="text-[var(--text-secondary)] font-mono">{fmt(mx)}</strong></span>
        </div>
      </div>
      <div className="flex items-baseline gap-2 mb-4">
        <span className="text-2xl font-bold font-mono">{fmt(cd[cd.length - 1]?.v || 0)}</span>
        <span className="text-xs text-[var(--text-tertiary)]">{quote}</span>
        <span className={`text-xs font-mono font-semibold ml-1 ${up ? "text-[var(--up)]" : "text-[var(--down)]"}`}>
          {up ? "+" : ""}{fmt((cd[cd.length - 1]?.v || 0) - (cd[0]?.v || 0))} ({fmt(((cd[cd.length - 1]?.v || 1) / (cd[0]?.v || 1) - 1) * 100)}%)
        </span>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={cd} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="eqG" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={col} stopOpacity={0.15} />
              <stop offset="100%" stopColor={col} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="t" tick={{ fill: "var(--text-quaternary)", fontSize: 10 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "var(--text-quaternary)", fontSize: 10 }} axisLine={false} tickLine={false} domain={[mn * 0.9995, mx * 1.0005]} width={55} />
          <Tooltip
            contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border-accent)", borderRadius: 10, padding: "8px 12px", fontSize: 12 }}
            labelStyle={{ color: "var(--text-tertiary)", marginBottom: 4 }}
            formatter={(v: number) => [`${fmt(v)} ${quote}`, "Kapital"]}
            itemStyle={{ color: col, fontFamily: "JetBrains Mono" }}
          />
          <Area type="monotone" dataKey="v" stroke={col} strokeWidth={1.5} fill="url(#eqG)" dot={false} activeDot={{ r: 3, fill: col, strokeWidth: 0 }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function PnlChart({ data, quote = "USDC" }: { data: { zeit: string; pnl: number }[]; quote?: string }) {
  return (
    <div className="card p-5">
      <h3 className="text-xs text-[var(--text-tertiary)] uppercase tracking-[0.1em] font-medium mb-4">PnL pro Stunde</h3>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="zeit" tick={{ fill: "var(--text-quaternary)", fontSize: 10 }} axisLine={false} tickLine={false} interval={2} />
          <YAxis tick={{ fill: "var(--text-quaternary)", fontSize: 10 }} axisLine={false} tickLine={false} width={40} />
          <Tooltip
            contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border-accent)", borderRadius: 10, padding: "8px 12px", fontSize: 12 }}
            formatter={(v: number) => [`${v >= 0 ? "+" : ""}${fmt(v, 4)} ${quote}`, "PnL"]}
          />
          <Bar dataKey="pnl" radius={[3, 3, 0, 0]} maxBarSize={16}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.pnl >= 0 ? "var(--up)" : "var(--down)"} fillOpacity={0.7} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function TradesTabelle({ trades, quote = "USDC" }: { trades: Trade[]; quote?: string }) {
  if (!trades.length) return <div className="card p-8 text-center text-sm text-[var(--text-tertiary)]">Noch keine Trades</div>;

  return (
    <div className="card overflow-hidden">
      <div className="px-5 py-3.5 border-b border-[var(--border)]">
        <div className="flex items-center justify-between">
          <h3 className="text-xs text-[var(--text-tertiary)] uppercase tracking-[0.1em] font-medium">Letzte Trades</h3>
          <span className="text-[10px] text-[var(--text-quaternary)]">{trades.length} angezeigt</span>
        </div>
      </div>

      {/* Mobile */}
      <div className="sm:hidden divide-y divide-[var(--border-subtle)]">
        {trades.map((t) => (
          <div key={t.id} className="px-4 py-3">
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center gap-2">
                <span className={`w-1.5 h-1.5 rounded-full`} style={{ background: t.side === "buy" ? "var(--up)" : "var(--down)" }} />
                <span className="text-sm font-medium">{t.pair}</span>
                <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded" style={{
                  background: t.side === "buy" ? "var(--up-bg)" : "var(--down-bg)",
                  color: t.side === "buy" ? "var(--up)" : "var(--down)"
                }}>
                  {t.side === "buy" ? "KAUF" : "VERK."}
                </span>
              </div>
              <span className="text-sm font-mono font-semibold" style={{ color: t.pnl >= 0 ? "var(--up)" : "var(--down)" }}>
                {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(4)} {quote}
              </span>
            </div>
            <div className="flex justify-between text-[10px] text-[var(--text-tertiary)]">
              <span className="font-mono">{fmt(t.price)} · {fmtSats(t.amount)} <span className="text-[var(--text-quaternary)]">({fmt(t.amount * t.price)} {quote})</span></span>
              <span>{new Date(t.timestamp).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
            </div>
          </div>
        ))}
      </div>

      {/* Desktop */}
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-wider">
              <th className="text-left px-5 py-2.5 font-medium">Zeit</th>
              <th className="text-left px-5 py-2.5 font-medium">Paar</th>
              <th className="text-left px-5 py-2.5 font-medium">Typ</th>
              <th className="text-right px-5 py-2.5 font-medium">Preis</th>
              <th className="text-right px-5 py-2.5 font-medium">Menge (BTC)</th>
              <th className="text-right px-5 py-2.5 font-medium">Wert ({quote})</th>
              <th className="text-right px-5 py-2.5 font-medium">PnL</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-subtle)]">
            {trades.map((t) => (
              <tr key={t.id} className="hover:bg-[var(--bg-card-hover)] transition-colors">
                <td className="px-5 py-2.5 font-mono text-[11px] text-[var(--text-tertiary)]">
                  {new Date(t.timestamp).toLocaleString("de-DE", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                </td>
                <td className="px-5 py-2.5 font-medium">{t.pair}</td>
                <td className="px-5 py-2.5">
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold" style={{
                    background: t.side === "buy" ? "var(--up-bg)" : "var(--down-bg)",
                    color: t.side === "buy" ? "var(--up)" : "var(--down)"
                  }}>
                    {t.side === "buy" ? "KAUF" : "VERKAUF"}
                  </span>
                </td>
                <td className="px-5 py-2.5 text-right font-mono">{fmt(t.price)}</td>
                <td className="px-5 py-2.5 text-right font-mono text-[var(--text-secondary)]">
                  <span>{fmtBtc(t.amount)}</span>
                  <span className="block text-[10px] text-[var(--text-quaternary)]">{fmtSats(t.amount)}</span>
                </td>
                <td className="px-5 py-2.5 text-right font-mono text-[var(--text-secondary)]">{fmt(t.amount * t.price)} {quote}</td>
                <td className="px-5 py-2.5 text-right font-mono font-semibold" style={{ color: t.pnl >= 0 ? "var(--up)" : "var(--down)" }}>
                  {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(4)} {quote}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Steuerung({ status, commands, onCommand }: {
  status: string; commands: CommandRecord[]; onCommand: (t: string) => void;
}) {
  const laeuft = status === "running";
  const gestoppt = status === "stopped" || status === "paused";
  const labels: Record<string, string> = { stop: "Stoppen", resume: "Fortsetzen", pause: "Pausieren", status: "Status", performance: "Performance", update_config: "Config" };
  const stLabels: Record<string, string> = { completed: "OK", failed: "Fehler", pending: "..." };

  const btn = (t: string, lbl: string, col: string) => (
    <button key={t} onClick={() => onCommand(t)}
      className="px-4 py-2.5 rounded-xl text-xs font-semibold transition-all active:scale-95"
      style={{ background: `color-mix(in srgb, ${col} 10%, transparent)`, color: col, border: `1px solid color-mix(in srgb, ${col} 15%, transparent)` }}
    >{lbl}</button>
  );

  return (
    <div className="card p-5">
      <h3 className="text-xs text-[var(--text-tertiary)] uppercase tracking-[0.1em] font-medium mb-4">Steuerung</h3>
      <div className="flex flex-wrap gap-2 mb-5">
        {laeuft && <>{btn("pause", "Pausieren", "var(--warn)")}{btn("stop", "Stoppen", "var(--down)")}</>}
        {gestoppt && btn("resume", "Fortsetzen", "var(--up)")}
        {btn("status", "Status abrufen", "var(--accent)")}
      </div>
      {commands.length > 0 && (
        <>
          <p className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-wider mb-2 font-medium">Befehlshistorie</p>
          <div className="space-y-1 max-h-36 overflow-y-auto">
            {commands.map((c) => (
              <div key={c.id} className="flex items-center gap-2 text-[11px] py-1.5 px-2.5 rounded-lg hover:bg-[var(--bg-secondary)]">
                <span className="text-[var(--text-quaternary)] font-mono w-10 shrink-0">
                  {new Date(c.createdAt).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}
                </span>
                <span className="text-[var(--text-secondary)] font-medium">{labels[c.type] || c.type}</span>
                <span className="ml-auto text-[9px] font-bold px-1.5 py-0.5 rounded" style={{
                  background: c.status === "completed" ? "var(--up-bg)" : c.status === "failed" ? "var(--down-bg)" : "var(--warn-bg)",
                  color: c.status === "completed" ? "var(--up)" : c.status === "failed" ? "var(--down)" : "var(--warn)",
                }}>
                  {stLabels[c.status] || c.status}
                </span>
              </div>
            ))}
          </div>
        </>
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
  const avgSharpe = pairs.length ? pairs.reduce((s, [, m]) => s + (m.sharpe_ratio || 0), 0) / pairs.length : 0;
  const avgReturn = pairs.length ? pairs.reduce((s, [, m]) => s + (m.annualized_return_pct || 0), 0) / pairs.length : 0;
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
      <div className="min-h-[85vh] flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-8 h-8 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-[var(--text-secondary)]">Verbinde mit RichBot...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-[1400px] mx-auto px-4 py-5 sm:px-6 pb-16">
      {/* Demo Banner */}
      {isDemo && (
        <div className="mb-4 px-4 py-2.5 rounded-xl text-[11px] font-medium text-center" style={{ background: "var(--accent-bg)", color: "var(--accent)", border: "1px solid color-mix(in srgb, var(--accent) 15%, transparent)" }}>
          Demo-Modus — Datenbank nicht erreichbar. Prufe die Umgebungsvariablen.
        </div>
      )}

      {/* Waiting for Bot */}
      {!isDemo && status.status === "waiting" && (
        <div className="mb-4 px-4 py-2.5 rounded-xl text-[11px] font-medium text-center" style={{ background: "var(--warn-bg)", color: "var(--warn)", border: "1px solid color-mix(in srgb, var(--warn) 15%, transparent)" }}>
          Datenbank verbunden — Warte auf Raspberry Pi. Starte den Bot, um Live-Daten zu sehen.
        </div>
      )}

      {/* Header */}
      <header className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-5">
        <StatusBadge status={status.status} hb={status.lastHeartbeat} />
        <div className="flex items-center gap-3 text-[11px] text-[var(--text-tertiary)]">
          <span>Laufzeit: <strong className="text-[var(--text-secondary)]">{laufzeit(status.startedAt)}</strong></span>
          <span className="w-px h-3 bg-[var(--border)]" />
          <span>{status.pairs.length} Paare</span>
          <span className="w-px h-3 bg-[var(--border)]" />
          <span>v{status.version}</span>
        </div>
      </header>

      {/* Top Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-5">
        <Stat label="Gesamt-PnL" wert={`${totalPnl >= 0 ? "+" : ""}${fmt(totalPnl, 4)}`} sub={quoteCcy} farbe={totalPnl >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"} klein />
        <Stat label="Kapital" wert={fmt(totalEquity)} sub={quoteCcy} klein />
        <Stat label="Trades" wert={totalTrades.toLocaleString("de-DE")} sub="gesamt" klein />
        <Stat label="Drawdown" wert={`${fmt(maxDd)}%`} farbe={maxDd > 5 ? "text-[var(--down)]" : "text-[var(--warn)]"} klein />
        <Stat label="Sharpe" wert={fmt(avgSharpe)} farbe={avgSharpe > 1.5 ? "text-[var(--up)]" : ""} klein />
        <Stat label="Rendite p.a." wert={`${fmt(avgReturn)}%`} farbe={avgReturn > 0 ? "text-[var(--up)]" : "text-[var(--down)]"} klein />
      </div>

      {/* Pair Cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-5">
        {pairs.map(([p, m]) => <PairKarte key={p} pair={p} m={m} quote={quoteCcy} />)}
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-5">
        <EquityChart data={equity} quote={quoteCcy} />
        {pnlData.length > 0 && <PnlChart data={pnlData} quote={quoteCcy} />}
        {pnlData.length === 0 && <div className="card p-5 flex items-center justify-center text-sm text-[var(--text-tertiary)]">PnL-Chart erscheint nach ersten Trades</div>}
      </div>

      {/* Trades + Controls */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2"><TradesTabelle trades={trades} quote={quoteCcy} /></div>
        <div><Steuerung status={status.status} commands={commands} onCommand={handleCommand} /></div>
      </div>
    </div>
  );
}
