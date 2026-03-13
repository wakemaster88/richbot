"use client";

import type { CBGlobalData, PairMetrics } from "@/lib/types";
import { CB_COLORS, CB_BG } from "@/lib/constants";
import { ErrorBoundary } from "./ErrorBoundary";

export interface CircuitBreakerStatusProps {
  cbGlobal: CBGlobalData;
  pairs: [string, PairMetrics][];
}

function CircuitBreakerStatusBase({ cbGlobal, pairs }: CircuitBreakerStatusProps) {
  const hasActive = pairs.some(
    ([, m]) => m.circuit_breaker && m.circuit_breaker.level !== "GREEN"
  );

  if (
    !hasActive &&
    !cbGlobal.global_halt &&
    (!cbGlobal.history || cbGlobal.history.length === 0)
  ) {
    return null;
  }

  return (
    <div className="card p-4 sm:p-5 fade-in">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-xs">Circuit Breaker</h3>
        {cbGlobal.global_halt && (
          <span
            className="px-2 py-0.5 rounded text-[9px] font-bold animate-pulse"
            style={{ background: CB_BG.RED, color: CB_COLORS.RED }}
          >
            CASCADE HALT
          </span>
        )}
      </div>

      <div className="flex flex-wrap gap-2 mb-3">
        {pairs.map(([p, m]) => {
          const cb = m.circuit_breaker;
          if (!cb) return null;
          const lvl = cb.level || "GREEN";
          const col = CB_COLORS[lvl as keyof typeof CB_COLORS] || CB_COLORS.GREEN;
          const bg = CB_BG[lvl as keyof typeof CB_BG] || CB_BG.GREEN;
          const coin = p.split("/")[0];
          return (
            <div
              key={p}
              className="card-inner px-3 py-2 rounded-lg flex items-center gap-2 min-w-[120px]"
              style={{ borderLeft: `3px solid ${col}` }}
            >
              <div className="flex flex-col">
                <div className="flex items-center gap-1.5">
                  <span
                    className="w-2 h-2 rounded-full"
                    style={{
                      background: col,
                      boxShadow: lvl !== "GREEN" ? `0 0 6px ${col}` : "none",
                    }}
                  />
                  <span className="text-[10px] font-bold">{coin}</span>
                  <span
                    className="text-[8px] font-semibold px-1 py-px rounded"
                    style={{ background: bg, color: col }}
                  >
                    {lvl}
                  </span>
                </div>
                <div className="text-[8px] text-[var(--text-quaternary)] mt-0.5 font-mono">
                  DD {cb.drawdown_pct.toFixed(1)}%
                  <span className="text-[7px] ml-1">
                    (Y:{cb.yellow_threshold.toFixed(1)} O:{cb.orange_threshold.toFixed(1)} R:
                    {cb.red_threshold.toFixed(1)})
                  </span>
                </div>
                {cb.resume_in_sec > 0 && (
                  <span className="text-[8px] font-medium mt-0.5" style={{ color: col }}>
                    Resume in {Math.ceil(cb.resume_in_sec / 60)}min
                  </span>
                )}
                {lvl !== "GREEN" && (
                  <div className="flex gap-2 text-[7px] text-[var(--text-quaternary)] mt-0.5">
                    <span>Size: {(cb.size_factor * 100).toFixed(0)}%</span>
                    {cb.spacing_mult > 1 && (
                      <span>Spacing: ×{cb.spacing_mult.toFixed(1)}</span>
                    )}
                    {!cb.can_buy && (
                      <span className="text-[var(--down)] font-semibold">Buys gesperrt</span>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {cbGlobal.history && cbGlobal.history.length > 0 && (
        <div>
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase mb-1 font-semibold">
            History
          </p>
          <div className="space-y-0.5 max-h-24 overflow-y-auto">
            {cbGlobal.history.slice(0, 8).map((ev, i) => {
              const col = CB_COLORS[ev.level as keyof typeof CB_COLORS] || "var(--text-tertiary)";
              const zeit = new Date(ev.timestamp * 1000).toLocaleTimeString("de-DE", {
                hour: "2-digit",
                minute: "2-digit",
              });
              return (
                <div key={i} className="flex items-center gap-2 text-[8px] py-0.5">
                  <span className="text-[var(--text-quaternary)] font-mono w-10">{zeit}</span>
                  <span
                    className="w-1.5 h-1.5 rounded-full"
                    style={{ background: col }}
                  />
                  <span className="font-semibold" style={{ color: col }}>
                    {ev.level}
                  </span>
                  <span className="text-[var(--text-tertiary)]">{ev.pair.split("/")[0]}</span>
                  <span className="text-[var(--text-quaternary)] font-mono">
                    DD {ev.drawdown_pct}% (Schwelle {ev.threshold_pct}%)
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

export function CircuitBreakerStatus(props: CircuitBreakerStatusProps) {
  return (
    <ErrorBoundary>
      <CircuitBreakerStatusBase {...props} />
    </ErrorBoundary>
  );
}
