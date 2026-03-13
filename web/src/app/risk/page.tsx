"use client";

import { useState, useEffect, useMemo } from "react";
import Link from "next/link";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import type {
  BotStatus,
  PairMetrics,
  WalletData,
  CorrelationData,
  CBGlobalData,
} from "@/lib/types";
import { fmt } from "@/lib/format";
import { CB_COLORS, CB_BG } from "@/lib/constants";
import { InventorySkewBar } from "@/components";
import { ErrorBoundary } from "@/components/ErrorBoundary";

function DrawdownGauge({ value }: { value: number }) {
  const pct = Math.min(Math.max(value, 0), 10);
  const color =
    pct <= 2 ? "var(--up)" : pct <= 5 ? "var(--warn)" : "var(--down)";
  const rotation = (pct / 10) * 180 - 90;
  return (
    <div className="relative w-24 h-24 mx-auto">
      <svg viewBox="0 0 100 60" className="w-full h-full">
        <path
          d="M 10 50 A 40 40 0 0 1 90 50"
          fill="none"
          stroke="var(--bg-tertiary)"
          strokeWidth="8"
          strokeLinecap="round"
        />
        <path
          d="M 10 50 A 40 40 0 0 1 90 50"
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={`${(pct / 10) * 126} 126`}
        />
        <text
          x="50"
          y="42"
          textAnchor="middle"
          className="text-[14px] font-bold font-mono"
          fill="var(--text-primary)"
        >
          {value.toFixed(1)}%
        </text>
      </svg>
    </div>
  );
}

function RiskPageContent() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [equitySnapshots, setEquitySnapshots] = useState<
    { timestamp: string; equity: number; pair: string }[]
  >([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    Promise.all([
      fetch("/api/status", { cache: "no-store" }).then((r) => r.json()),
      fetch("/api/equity?hours=720", { cache: "no-store" }).then((r) => r.json()),
    ]).then(([s, eq]) => {
      if (active) {
        setStatus(s);
        setEquitySnapshots(Array.isArray(eq) ? eq : []);
      }
      setLoading(false);
    });
    return () => {
      active = false;
    };
  }, []);

  const rawStatuses = (status?.pairStatuses || {}) as Record<
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

  const totalEquity = walletData?._total_usdc ??
    pairs.reduce((s, [, m]) => s + (m.current_equity || 0), 0);

  const currentDrawdown = useMemo(() => {
    if (pairs.length === 0) return 0;
    return Math.max(...pairs.map(([, m]) => m.max_drawdown_pct || 0), 0);
  }, [pairs]);

  const varPct = correlationData?.portfolio_var_pct ?? 0;
  const varAbs = correlationData?.portfolio_var_abs ?? 0;
  const quote = "USDC";

  const equityByDay = useMemo(() => {
    const byDatePair = new Map<string, { equity: number; ts: number }>();
    for (const s of equitySnapshots) {
      const date = s.timestamp.slice(0, 10);
      const key = `${date}:${s.pair}`;
      const ts = new Date(s.timestamp).getTime();
      const cur = byDatePair.get(key);
      if (!cur || ts > cur.ts) byDatePair.set(key, { equity: s.equity, ts });
    }
    const byDay = new Map<string, number>();
    for (const [k, v] of byDatePair) {
      const date = k.split(":")[0]!;
      byDay.set(date, (byDay.get(date) ?? 0) + v.equity);
    }
    const sorted = Array.from(byDay.entries())
      .sort((a, b) => a[0].localeCompare(b[0]))
      .slice(-30);
    let peak = 0;
    return sorted.map(([day, eq]) => {
      peak = Math.max(peak, eq);
      const dd = peak > 0 ? ((eq - peak) / peak) * 100 : 0;
      return {
        day,
        equity: eq,
        drawdown: dd,
        label: new Date(day + "T12:00:00").toLocaleDateString("de-DE", {
          day: "2-digit",
          month: "2-digit",
        }),
      };
    });
  }, [equitySnapshots]);

  const longShortRatio = useMemo(() => {
    if (!walletData || !totalEquity) return { long: 0, short: 0, ratio: 0 };
    const coins = Object.entries(walletData).filter(([k]) => !k.startsWith("_"));
    let longValue = 0;
    let usdcValue = 0;
    for (const [sym, e] of coins) {
      const entry = e as { usdc_value: number };
      if (sym === "USDC") usdcValue = entry.usdc_value;
      else longValue += entry.usdc_value;
    }
    const ratio = usdcValue > 0 ? longValue / usdcValue : longValue > 0 ? 999 : 0;
    return {
      long: totalEquity > 0 ? (longValue / totalEquity) * 100 : 0,
      short: totalEquity > 0 ? (usdcValue / totalEquity) * 100 : 0,
      ratio,
    };
  }, [walletData, totalEquity]);

  if (loading) {
    return (
      <div className="max-w-[1200px] mx-auto px-4 py-8">
        <div className="h-8 w-48 rounded bg-[var(--bg-elevated)] animate-pulse mb-6" />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="card p-5 h-32 rounded-xl animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-[1200px] mx-auto px-4 py-6 pb-12">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold tracking-tight">Risk Dashboard</h1>
        <Link
          href="/"
          className="text-[12px] text-[var(--text-quaternary)] hover:text-[var(--accent)]"
        >
          ← Dashboard
        </Link>
      </div>

      {/* Portfolio Risk Summary */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <div className="card p-4">
          <p className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-semibold mb-2">
            Current Drawdown
          </p>
          <DrawdownGauge value={currentDrawdown} />
        </div>
        <div className="card p-4 flex flex-col justify-center">
          <p className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-semibold mb-2">
            Value-at-Risk (95%)
          </p>
          <p className="text-lg font-bold font-mono">
            Max. Tagesverlust: {fmt(varAbs, 2)} {quote}
          </p>
          <p
            className={`text-sm font-mono mt-0.5 ${
              varPct > 5 ? "text-[var(--down)]" : varPct > 3 ? "text-[var(--warn)]" : "text-[var(--up)]"
            }`}
          >
            {varPct.toFixed(1)}% des Portfolios
          </p>
        </div>
        {correlationData && correlationData.pairs?.length >= 2 && (
          <div className="card p-4 lg:col-span-2">
            <p className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-semibold mb-2">
              Correlation Heatmap
            </p>
            <div
              className="inline-grid gap-px rounded overflow-hidden"
              style={{
                gridTemplateColumns: `28px repeat(${correlationData.pairs.length}, 1fr)`,
                background: "var(--border-subtle)",
              }}
            >
              <div className="bg-[var(--bg-primary)] p-0.5" />
              {correlationData.pairs.map((p) => (
                <div
                  key={p}
                  className="bg-[var(--bg-primary)] px-1 py-0.5 text-center text-[8px] font-bold text-[var(--text-tertiary)]"
                >
                  {p.split("/")[0]}
                </div>
              ))}
              {correlationData.matrix.map((row, i) => (
                <div key={i} className="contents">
                  <div
                    className="bg-[var(--bg-primary)] px-1 py-0.5 text-[8px] font-bold text-[var(--text-tertiary)] flex items-center"
                  >
                    {correlationData.pairs[i]?.split("/")[0]}
                  </div>
                  {row.map((v, j) => (
                    <div
                      key={j}
                      className="px-0.5 py-0.5 text-center text-[9px] font-mono"
                      style={{
                        background:
                          i === j
                            ? "var(--bg-secondary)"
                            : Math.abs(v) >= 0.9
                              ? "color-mix(in srgb, var(--down) 25%, transparent)"
                              : Math.abs(v) >= 0.7
                                ? "color-mix(in srgb, var(--warn) 20%, transparent)"
                                : "var(--bg-primary)",
                        color:
                          i === j
                            ? "var(--text-quaternary)"
                            : v >= 0.9
                              ? "var(--down)"
                              : v >= 0.7
                                ? "var(--warn)"
                                : "var(--text-tertiary)",
                      }}
                    >
                      {i === j ? "1" : v.toFixed(2)}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Circuit Breaker Status */}
      {cbGlobal && pairs.length > 0 && (
        <div className="card p-4 mb-6">
          <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
            Circuit Breaker Status
          </h3>
          <div className="flex flex-wrap gap-2">
            {pairs.map(([p, m]) => {
              const cb = m.circuit_breaker;
              if (!cb) return null;
              const lvl = cb.level || "GREEN";
              const col = CB_COLORS[lvl as keyof typeof CB_COLORS] || CB_COLORS.GREEN;
              const bg = CB_BG[lvl as keyof typeof CB_BG] || CB_BG.GREEN;
              return (
                <div
                  key={p}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg"
                  style={{
                    background: bg,
                    borderLeft: `4px solid ${col}`,
                  }}
                >
                  <span
                    className="w-3 h-3 rounded-full"
                    style={{
                      background: col,
                      boxShadow: lvl !== "GREEN" ? `0 0 8px ${col}` : "none",
                    }}
                  />
                  <span className="text-sm font-bold">{p.split("/")[0]}</span>
                  <span className="text-[10px] font-semibold" style={{ color: col }}>
                    {lvl}
                  </span>
                  <span className="text-[9px] text-[var(--text-quaternary)] font-mono">
                    DD {cb.drawdown_pct.toFixed(1)}%
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Per-Pair Risk */}
      <div className="card p-4 mb-6">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-4">
          Per-Pair Risk
        </h3>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {pairs.map(([p, m]) => {
            const concentration =
              totalEquity > 0
                ? ((m.current_equity || 0) / totalEquity) * 100
                : 0;
            const mae =
              m.inventory?.unrealized_pnl != null && m.inventory.unrealized_pnl < 0
                ? m.inventory.unrealized_pnl
                : 0;
            return (
              <div
                key={p}
                className="rounded-lg p-3"
                style={{ background: "var(--bg-secondary)" }}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="font-semibold text-sm">{p}</span>
                  <span className="text-[10px] text-[var(--text-quaternary)]">
                    {concentration.toFixed(1)}% Konzentration
                  </span>
                </div>
                {m.skew && m.skew.skew_factor !== 0 && (
                  <InventorySkewBar skew={m.skew} pair={p} />
                )}
                <div className="grid grid-cols-3 gap-2 text-[9px] mt-2">
                  <div>
                    <span className="text-[var(--text-quaternary)]">Slippage</span>
                    <p className="font-mono font-semibold">
                      {(m.avg_slippage_bps ?? 0).toFixed(1)} bps
                    </p>
                  </div>
                  <div>
                    <span className="text-[var(--text-quaternary)]">Spread</span>
                    <p className="font-mono font-semibold">
                      {m.spread?.current_bps != null
                        ? `${m.spread.current_bps.toFixed(1)} bps`
                        : "—"}
                    </p>
                  </div>
                  <div>
                    <span className="text-[var(--text-quaternary)]">MAE</span>
                    <p
                      className={`font-mono font-semibold ${
                        mae < 0 ? "text-[var(--down)]" : ""
                      }`}
                    >
                      {mae < 0 ? fmt(mae, 4) : "—"}
                    </p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Risk History: Drawdown Curve */}
      {equityByDay.length > 2 && (
        <div className="card p-4 mb-6">
          <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
            Drawdown-Kurve (30 Tage)
          </h3>
          <ResponsiveContainer width="100%" height={160}>
            <AreaChart
              data={equityByDay}
              margin={{ top: 0, right: 0, left: -15, bottom: 0 }}
            >
              <defs>
                <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--down)" stopOpacity={0.2} />
                  <stop offset="100%" stopColor="var(--down)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="label"
                tick={{ fill: "var(--text-quaternary)", fontSize: 9 }}
                axisLine={false}
                tickLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={{ fill: "var(--text-quaternary)", fontSize: 9 }}
                axisLine={false}
                tickLine={false}
                width={40}
                domain={["auto", 0]}
                tickFormatter={(v) => v.toFixed(1) + "%"}
              />
              <Tooltip
                contentStyle={{
                  background: "var(--bg-elevated)",
                  border: "1px solid var(--border-accent)",
                  borderRadius: 8,
                  fontSize: 11,
                }}
                formatter={(v: number) => [v.toFixed(2) + "%", "Drawdown"]}
              />
              <Area
                type="monotone"
                dataKey="drawdown"
                stroke="var(--down)"
                fill="url(#ddGrad)"
                strokeWidth={1.5}
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* VaR Violations */}
      {varAbs > 0 && equityByDay.length >= 2 && (() => {
        const violations: { day: string; pnl: number; label: string }[] = [];
        for (let i = 1; i < equityByDay.length; i++) {
          const prev = equityByDay[i - 1]!.equity;
          const curr = equityByDay[i]!.equity;
          const dailyPnl = curr - prev;
          if (dailyPnl < -varAbs) {
            violations.push({
              day: equityByDay[i]!.day,
              pnl: dailyPnl,
              label: equityByDay[i]!.label,
            });
          }
        }
        return violations.length > 0 ? (
          <div className="card p-4 mb-6">
            <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
              VaR Violations (Verlust &gt; VaR 95%)
            </h3>
            <div className="space-y-1 max-h-24 overflow-y-auto">
              {violations.map((v, i) => (
                <div key={i} className="flex items-center gap-2 text-[10px] py-1">
                  <span className="text-[var(--text-quaternary)] font-mono w-12">{v.label}</span>
                  <span className="font-mono font-semibold text-[var(--down)]">{fmt(v.pnl, 2)}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null;
      })()}

      {/* CB Events Timeline */}
      {cbGlobal?.history && cbGlobal.history.length > 0 && (
        <div className="card p-4 mb-6">
          <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
            Circuit Breaker Events
          </h3>
          <div className="space-y-1 max-h-32 overflow-y-auto">
            {cbGlobal.history.slice(0, 15).map((ev, i) => {
              const col = CB_COLORS[ev.level as keyof typeof CB_COLORS] || "var(--text-tertiary)";
              const t = new Date(ev.timestamp * 1000);
              return (
                <div
                  key={i}
                  className="flex items-center gap-2 text-[10px] py-1"
                >
                  <span className="text-[var(--text-quaternary)] font-mono w-16">
                    {t.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
                  </span>
                  <span className="w-2 h-2 rounded-full" style={{ background: col }} />
                  <span className="font-semibold" style={{ color: col }}>
                    {ev.level}
                  </span>
                  <span className="text-[var(--text-tertiary)]">
                    {ev.pair.split("/")[0]}
                  </span>
                  <span className="font-mono text-[var(--text-quaternary)]">
                    DD {ev.drawdown_pct}% → {ev.threshold_pct}%
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Exposure Map */}
      <div className="card p-4">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
          Exposure Map
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <p className="text-[9px] text-[var(--text-quaternary)] mb-1">Long/Short Ratio</p>
            <div className="flex items-center gap-2">
              <div className="flex-1 h-4 rounded-full overflow-hidden flex">
                <div
                  className="transition-all"
                  style={{
                    width: `${longShortRatio.long}%`,
                    background: "var(--up)",
                  }}
                />
                <div
                  className="transition-all"
                  style={{
                    width: `${longShortRatio.short}%`,
                    background: "var(--accent)",
                  }}
                />
              </div>
              <span className="text-sm font-mono font-semibold">
                {longShortRatio.ratio.toFixed(2)}x
              </span>
            </div>
            <p className="text-[9px] text-[var(--text-quaternary)] mt-0.5">
              {longShortRatio.long.toFixed(0)}% Long / {longShortRatio.short.toFixed(0)}% USDC
            </p>
          </div>
          <div>
            <p className="text-[9px] text-[var(--text-quaternary)] mb-1">Netto-Exposure</p>
            <p className="text-lg font-bold font-mono">
              {fmt(totalEquity, 2)} {quote}
            </p>
            <p className="text-[9px] text-[var(--text-quaternary)]">
              Gesamtportfoliowert
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function RiskPage() {
  return (
    <ErrorBoundary>
      <RiskPageContent />
    </ErrorBoundary>
  );
}
