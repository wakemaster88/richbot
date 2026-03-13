"use client";

import type { WalletData, WalletEntry, EquityPoint } from "@/lib/types";
import { fmt } from "@/lib/format";
import { COIN_COLORS } from "@/lib/constants";
import { ErrorBoundary } from "./ErrorBoundary";

export interface WalletPanelProps {
  wallet: WalletData;
  targetRatio?: number;
  pairStats?: Record<string, { trades: number; pnl: number; wins: number; losses: number; volume: number }>;
  equity?: EquityPoint[];
}

function WalletPanelBase({ wallet, targetRatio, pairStats, equity }: WalletPanelProps) {
  const total = wallet._total_usdc ?? 0;
  const coins = Object.entries(wallet)
    .filter(([k]) => !k.startsWith("_"))
    .map(([symbol, entry]) => {
      const e = entry as WalletEntry;
      const uv = e?.usdc_value ?? 0;
      const pct = total > 0 ? (uv / total) * 100 : 0;
      const base = symbol === "USDC" ? null : symbol;
      const pairKey = base ? Object.keys(pairStats || {}).find((k) => k.startsWith(base + "/")) : undefined;
      const stats = pairKey ? pairStats?.[pairKey] : undefined;
      return { symbol, ...e, usdc_value: uv, pct, stats };
    })
    .sort((a, b) => (b.usdc_value ?? 0) - (a.usdc_value ?? 0));

  const usdcPct = coins.find((c) => c.symbol === "USDC")?.pct ?? 0;

  const allPnl = coins.filter((c) => c.stats).map((c) => c.stats!.pnl);
  const maxAbsPnl = allPnl.length > 0 ? Math.max(...allPnl.map(Math.abs), 0.01) : 1;

  const eqPoints = equity || [];
  const sparkLen = Math.min(eqPoints.length, 30);
  const sparkData = eqPoints.slice(-sparkLen);
  const sparkMin = sparkData.length > 0 ? Math.min(...sparkData.map((e) => e.equity)) : 0;
  const sparkMax = sparkData.length > 0 ? Math.max(...sparkData.map((e) => e.equity)) : 1;
  const sparkRange = sparkMax - sparkMin || 1;
  const eqFirst = sparkData[0]?.equity ?? 0;
  const eqLast = sparkData[sparkData.length - 1]?.equity ?? 0;
  const eqChange = eqFirst > 0 ? ((eqLast - eqFirst) / eqFirst) * 100 : 0;

  const ratioDelta = targetRatio != null ? usdcPct - targetRatio * 100 : 0;
  const ratioStatus = Math.abs(ratioDelta) < 3 ? "balanced" : ratioDelta > 0 ? "overweight" : "underweight";

  return (
    <div className="card p-4 sm:p-5 mb-3 fade-in">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold">
          Kapital-Verteilung
        </h3>
        <div className="flex items-center gap-3">
          {sparkData.length > 3 && (
            <div className="flex items-center gap-1.5">
              <svg
                width="48"
                height="16"
                viewBox={`0 0 ${sparkLen} 16`}
                className="opacity-80"
              >
                <polyline
                  fill="none"
                  stroke={eqChange >= 0 ? "#22c55e" : "#ef4444"}
                  strokeWidth="1.2"
                  strokeLinejoin="round"
                  points={sparkData
                    .map((e, i) => `${i},${16 - ((e.equity - sparkMin) / sparkRange) * 14 - 1}`)
                    .join(" ")}
                />
              </svg>
              <span
                className="text-[9px] font-mono font-semibold"
                style={{ color: eqChange >= 0 ? "var(--up)" : "var(--down)" }}
              >
                {eqChange >= 0 ? "+" : ""}
                {eqChange.toFixed(1)}%
              </span>
            </div>
          )}
          <span className="text-base font-bold font-mono">
            {fmt(total, 2)} <span className="text-[10px] text-[var(--text-tertiary)]">USDC</span>
          </span>
        </div>
      </div>

      {/* Allocation bar */}
      <div className="relative mb-2">
        <div
          className="h-3 rounded-full overflow-hidden flex"
          style={{ background: "var(--bg-secondary)" }}
        >
          {coins.map((c) => (
            <div
              key={c.symbol}
              style={{
                width: `${Math.max(c.pct, 1)}%`,
                background: COIN_COLORS[c.symbol] || "var(--text-tertiary)",
              }}
              className="transition-all duration-500"
            />
          ))}
        </div>
        {targetRatio != null && targetRatio > 0 && (
          <div
            className="absolute top-0 h-3 border-r-2 border-dashed"
            style={{ left: `${targetRatio * 100}%`, borderColor: "var(--accent)" }}
            title={`Ziel USDC: ${(targetRatio * 100).toFixed(0)}%`}
          />
        )}
      </div>

      {/* Target ratio comparison */}
      {targetRatio != null && (
        <div className="flex items-center gap-3 text-[9px] mb-3">
          <div className="flex items-center gap-1.5 text-[var(--text-quaternary)]">
            <span className="w-3 h-0 border-t-2 border-dashed" style={{ borderColor: "var(--accent)" }} />
            <span>
              Ziel USDC:{" "}
              <strong className="text-[var(--text-tertiary)]">{(targetRatio * 100).toFixed(0)}%</strong>
            </span>
          </div>
          <span className="text-[var(--text-quaternary)]">→</span>
          <span className="font-mono font-semibold" style={{ color: "var(--text-secondary)" }}>
            {usdcPct.toFixed(1)}%
          </span>
          <span
            className="px-1.5 py-0.5 rounded text-[8px] font-bold"
            style={{
              background:
                ratioStatus === "balanced"
                  ? "var(--up-bg)"
                  : ratioStatus === "overweight"
                    ? "var(--warn-bg)"
                    : "var(--down-bg)",
              color:
                ratioStatus === "balanced"
                  ? "var(--up)"
                  : ratioStatus === "overweight"
                    ? "var(--warn)"
                    : "var(--down)",
            }}
          >
            {ratioStatus === "balanced"
              ? "IM ZIEL"
              : ratioStatus === "overweight"
                ? `+${ratioDelta.toFixed(0)}% ÜBER`
                : `${ratioDelta.toFixed(0)}% UNTER`}
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
            <div
              key={c.symbol}
              className="px-3 py-2.5 rounded-lg"
              style={{ background: "var(--bg-secondary)" }}
            >
              <div className="flex items-center gap-2.5 mb-1.5">
                <div
                  className="w-7 h-7 rounded-lg flex items-center justify-center text-[9px] font-bold shrink-0"
                  style={{
                    background: `color-mix(in srgb, ${COIN_COLORS[c.symbol] || "var(--text-tertiary)"} 15%, transparent)`,
                    color: COIN_COLORS[c.symbol] || "var(--text-tertiary)",
                  }}
                >
                  {c.symbol}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline justify-between">
                    <span className="font-mono font-semibold text-xs">
                      {c.symbol === "USDC"
                        ? fmt(c.total, 2)
                        : c.total < 0.01
                          ? c.total.toFixed(8)
                          : fmt(c.total, 4)}
                    </span>
                    <span className="text-[10px] text-[var(--text-quaternary)] font-mono">
                      {c.pct.toFixed(1)}%
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-[9px] text-[var(--text-quaternary)]">
                    <span>{"\u2248"} {fmt(c.usdc_value, 2)} USDC</span>
                    {c.locked > 0 && (
                      <span className="text-[var(--warn)]">
                        {c.symbol === "USDC" ? fmt(c.locked, 2) : c.locked.toFixed(6)} gesperrt
                      </span>
                    )}
                  </div>
                </div>
              </div>
              {c.stats && (
                <div className="mt-1.5 pt-1.5 border-t" style={{ borderColor: "var(--border-subtle)" }}>
                  <div className="flex items-center justify-between text-[9px] mb-1">
                    <span
                      className="font-mono font-semibold"
                      style={{ color: pnlUp ? "var(--up)" : "var(--down)" }}
                    >
                      {pnlUp ? "+" : ""}
                      {pnl.toFixed(4)} USDC
                    </span>
                    <div className="flex items-center gap-2 text-[var(--text-quaternary)]">
                      {winRate !== null && <span>{winRate.toFixed(0)}% W</span>}
                      <span>{c.stats.trades} Trades</span>
                    </div>
                  </div>
                  <div className="h-1 rounded-full overflow-hidden" style={{ background: "var(--bg-elevated)" }}>
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{
                        width: `${Math.max(pnlBarW, 2)}%`,
                        background: pnlUp ? "var(--up)" : "var(--down)",
                        opacity: 0.7,
                      }}
                    />
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

export function WalletPanel(props: WalletPanelProps) {
  return (
    <ErrorBoundary>
      <WalletPanelBase {...props} />
    </ErrorBoundary>
  );
}
