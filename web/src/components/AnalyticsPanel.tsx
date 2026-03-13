"use client";

import type { AnalyticsData } from "@/lib/types";
import { fmt } from "@/lib/format";
import { ErrorBoundary } from "./ErrorBoundary";

export interface AnalyticsPanelProps {
  data: AnalyticsData;
}

function AnalyticsPanelBase({ data }: AnalyticsPanelProps) {
  const s = data.summary;
  if (!s) return null;

  return (
    <div className="card p-4 sm:p-5 fade-in">
      <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-4">
        Analyse
      </h3>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
        <div
          className="p-2.5 rounded-lg"
          style={{ background: "var(--bg-secondary)" }}
        >
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase mb-0.5">
            Win-Rate
          </p>
          <p
            className={`text-sm font-bold font-mono ${s.win_rate >= 50 ? "text-[var(--up)]" : "text-[var(--down)]"}`}
          >
            {s.win_rate.toFixed(1)}%
          </p>
          <p className="text-[9px] text-[var(--text-quaternary)]">
            {s.wins}W / {s.losses}L
          </p>
        </div>
        <div
          className="p-2.5 rounded-lg"
          style={{ background: "var(--bg-secondary)" }}
        >
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase mb-0.5">
            Netto PnL
          </p>
          <p
            className={`text-sm font-bold font-mono ${s.net_pnl >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"}`}
          >
            {s.net_pnl >= 0 ? "+" : ""}
            {fmt(s.net_pnl, 4)}
          </p>
          <p className="text-[9px] text-[var(--text-quaternary)]">
            Gebuehren: {fmt(s.total_fees, 4)}
          </p>
        </div>
        <div
          className="p-2.5 rounded-lg"
          style={{ background: "var(--bg-secondary)" }}
        >
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase mb-0.5">
            Profit-Faktor
          </p>
          <p
            className={`text-sm font-bold font-mono ${s.profit_factor >= 1 ? "text-[var(--up)]" : "text-[var(--down)]"}`}
          >
            {s.profit_factor.toFixed(2)}
          </p>
          <p className="text-[9px] text-[var(--text-quaternary)]">
            {"\u00D8"} +{fmt(s.avg_win, 4)} / {fmt(s.avg_loss, 4)}
          </p>
        </div>
        <div
          className="p-2.5 rounded-lg"
          style={{ background: "var(--bg-secondary)" }}
        >
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase mb-0.5">
            Streaks
          </p>
          <p className="text-sm font-bold font-mono">
            <span className="text-[var(--up)]">{s.max_win_streak}</span>
            <span className="text-[var(--text-quaternary)]"> / </span>
            <span className="text-[var(--down)]">{s.max_loss_streak}</span>
          </p>
          <p className="text-[9px] text-[var(--text-quaternary)]">
            {s.total_trades} Trades
          </p>
        </div>
      </div>

      {data.hourly_pnl.length > 0 && (
        <div>
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase tracking-[0.14em] font-semibold mb-2">
            PnL pro Stunde
          </p>
          <div className="flex flex-wrap gap-0.5">
            {data.hourly_pnl.slice(-24).map((h) => {
              const intensity = Math.min(Math.abs(h.pnl) * 500, 1);
              const col =
                h.pnl >= 0
                  ? `rgba(16,185,129,${0.15 + intensity * 0.85})`
                  : `rgba(239,68,68,${0.15 + intensity * 0.85})`;
              const label = h.hour.slice(11, 13) + "h";
              return (
                <div
                  key={h.hour}
                  className="flex flex-col items-center gap-0.5"
                  title={`${label}: ${h.pnl >= 0 ? "+" : ""}${h.pnl.toFixed(4)} (${h.count} Trades)`}
                >
                  <div className="w-5 h-5 rounded-sm" style={{ background: col }} />
                  <span className="text-[7px] text-[var(--text-quaternary)]">
                    {label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export function AnalyticsPanel(props: AnalyticsPanelProps) {
  return (
    <ErrorBoundary>
      <AnalyticsPanelBase {...props} />
    </ErrorBoundary>
  );
}
