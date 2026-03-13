"use client";

import { PnlCard } from "./PnlCard";
import { RegimeBadge } from "./RegimeBadge";
import { fmt } from "@/lib/format";
import type { PairMetrics, Trade } from "@/lib/types";

interface PortfolioHeroProps {
  walletTotal: number;
  totalPnl: number;
  trades: Trade[];
  quoteCcy: string;
  pairs: [string, PairMetrics][];
}

export function PortfolioHero({
  walletTotal,
  totalPnl,
  trades,
  quoteCcy,
  pairs,
}: PortfolioHeroProps) {
  const now = Date.now();
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const weekStart = new Date(todayStart);
  weekStart.setDate(weekStart.getDate() - weekStart.getDay() + 1);
  if (weekStart > todayStart) weekStart.setDate(weekStart.getDate() - 7);

  const todayPnl = trades
    .filter((t) => new Date(t.timestamp).getTime() >= todayStart.getTime())
    .reduce((s, t) => s + (t.pnl || 0), 0);
  const weekPnl = trades
    .filter((t) => new Date(t.timestamp).getTime() >= weekStart.getTime())
    .reduce((s, t) => s + (t.pnl || 0), 0);
  const avgSharpe =
    pairs.length > 0
      ? pairs.reduce((s, [, m]) => s + (m.sharpe_ratio || 0), 0) / pairs.length
      : 0;

  return (
    <div className="card p-5 sm:p-6 mb-4 fade-in">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 mb-5">
        <div>
          <p className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.15em] font-semibold mb-1">
            Portfolio-Wert
          </p>
          <div className="flex items-baseline gap-3">
            <span className="text-3xl sm:text-4xl font-bold font-mono tracking-tight">
              {fmt(walletTotal)}
            </span>
            <span className="text-sm text-[var(--text-tertiary)]">{quoteCcy}</span>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          {pairs.map(([p, m]) => (
            <RegimeBadge key={p} pair={p} m={m} />
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
        <PnlCard label="Heute" value={todayPnl} quoteCcy={quoteCcy} />
        <PnlCard label="Diese Woche" value={weekPnl} quoteCcy={quoteCcy} />
        <PnlCard label="Gesamt" value={totalPnl} quoteCcy={quoteCcy} />
        <div className="card-inner px-3 py-2.5">
          <p className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-medium">
            Sharpe
          </p>
          <p
            className={`text-[15px] font-bold font-mono tracking-tight mt-0.5 ${
              avgSharpe >= 1 ? "text-[var(--up)]" : "text-[var(--text-primary)]"
            }`}
          >
            {avgSharpe.toFixed(2)}
          </p>
        </div>
        <div className="card-inner px-3 py-2.5">
          <p className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-medium">
            Drawdown
          </p>
          <p className="text-[15px] font-bold font-mono tracking-tight mt-0.5 text-[var(--warn)]">
            {fmt(Math.max(...pairs.map(([, m]) => m.max_drawdown_pct || 0), 0))}%
          </p>
        </div>
      </div>
    </div>
  );
}
