"use client";

import { useState } from "react";
import { ErrorBoundary } from "./ErrorBoundary";

interface MCResult {
  median_pnl: number;
  mean_pnl: number;
  std_pnl: number;
  percentile_5: number;
  percentile_1: number;
  max_loss: number;
  best_case: number;
  probability_of_loss: number;
  value_at_risk_95: number;
  conditional_var_95: number;
  mean_max_drawdown: number;
  mean_trades: number;
  distribution: number[];
  n_simulations: number;
  days_forward: number;
}

function MonteCarloPanelBase() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<MCResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runMC = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/monte-carlo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days: 30, simulations: 200 }),
      });
      if (!res.ok) {
        setError("Start fehlgeschlagen");
        setLoading(false);
        return;
      }
      const cmd = await res.json();
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 5000));
        const poll = await fetch(`/api/monte-carlo?id=${cmd.id}`);
        const data = await poll.json();
        if (data.status === "completed" && data.result) {
          setResult(data.result as MCResult);
          setLoading(false);
          return;
        }
        if (data.status === "failed") {
          setError("Simulation fehlgeschlagen");
          setLoading(false);
          return;
        }
      }
      setError("Timeout");
      setLoading(false);
    } catch {
      setError("Netzwerkfehler");
      setLoading(false);
    }
  };

  const fmt = (v: number) => (v >= 0 ? `+${v.toFixed(2)}` : v.toFixed(2));

  return (
    <div
      className="rounded-lg p-2.5 mb-3"
      style={{ background: "var(--bg-secondary)" }}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="text-[8px] text-[var(--text-quaternary)] uppercase tracking-wider font-semibold">
          Monte-Carlo Stress-Test
        </div>
        <button
          onClick={runMC}
          disabled={loading}
          className="px-2 py-1 rounded text-[8px] font-semibold transition-all active:scale-95 disabled:opacity-50"
          style={{
            background: "color-mix(in srgb, var(--accent) 12%, transparent)",
            color: "var(--accent)",
            border: "1px solid color-mix(in srgb, var(--accent) 20%, transparent)",
          }}
        >
          {loading ? "Laeuft..." : "Starten"}
        </button>
      </div>
      {error && (
        <div className="text-[9px] text-[var(--down)] mb-1">{error}</div>
      )}
      {result && (
        <div className="space-y-2">
          <div className="grid grid-cols-3 gap-1.5 text-[9px]">
            <div className="text-center">
              <div className="text-[7px] text-[var(--text-quaternary)] uppercase">
                VaR 95%
              </div>
              <div className="font-mono font-bold text-[var(--down)]">
                {fmt(result.value_at_risk_95)}
              </div>
            </div>
            <div className="text-center">
              <div className="text-[7px] text-[var(--text-quaternary)] uppercase">
                CVaR 95%
              </div>
              <div className="font-mono font-bold text-[var(--down)]">
                {fmt(result.conditional_var_95)}
              </div>
            </div>
            <div className="text-center">
              <div className="text-[7px] text-[var(--text-quaternary)] uppercase">
                P(Verlust)
              </div>
              <div
                className="font-mono font-bold"
                style={{
                  color:
                    result.probability_of_loss > 50
                      ? "var(--down)"
                      : "var(--up)",
                }}
              >
                {result.probability_of_loss.toFixed(0)}%
              </div>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-1.5 text-[9px]">
            <div className="text-center">
              <div className="text-[7px] text-[var(--text-quaternary)] uppercase">
                Median
              </div>
              <div
                className={`font-mono font-bold ${result.median_pnl >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"}`}
              >
                {fmt(result.median_pnl)}
              </div>
            </div>
            <div className="text-center">
              <div className="text-[7px] text-[var(--text-quaternary)] uppercase">
                Best
              </div>
              <div className="font-mono font-bold text-[var(--up)]">
                {fmt(result.best_case)}
              </div>
            </div>
            <div className="text-center">
              <div className="text-[7px] text-[var(--text-quaternary)] uppercase">
                Worst
              </div>
              <div className="font-mono font-bold text-[var(--down)]">
                {fmt(result.max_loss)}
              </div>
            </div>
          </div>
          {result.distribution.length > 5 &&
            (() => {
              const d = result.distribution;
              const bins = 20;
              const mn = d[0];
              const mx = d[d.length - 1];
              const step = (mx - mn) / bins || 1;
              const counts = new Array(bins).fill(0);
              for (const v of d) {
                const b = Math.min(Math.floor((v - mn) / step), bins - 1);
                counts[b]++;
              }
              const maxC = Math.max(...counts, 1);
              const zeroIdx =
                mn < 0 && mx > 0 ? Math.floor((0 - mn) / step) : -1;
              return (
                <div className="flex items-end gap-px" style={{ height: 32 }}>
                  {counts.map((c, i) => (
                    <div
                      key={i}
                      className="flex-1 rounded-t-sm transition-all"
                      style={{
                        height: `${(c / maxC) * 100}%`,
                        minHeight: c > 0 ? 2 : 0,
                        background:
                          i < zeroIdx
                            ? "var(--down)"
                            : i === zeroIdx
                              ? "var(--warn)"
                              : "var(--up)",
                        opacity: 0.7,
                      }}
                      title={`${(mn + i * step).toFixed(2)} – ${(mn + (i + 1) * step).toFixed(2)}: ${c}`}
                    />
                  ))}
                </div>
              );
            })()}
          <div className="text-[7px] text-[var(--text-quaternary)] text-center">
            95%-Konfidenz: Verlust max. $
            {Math.abs(result.value_at_risk_95).toFixed(2)} in {result.days_forward}
            d ({result.n_simulations} Sims)
          </div>
        </div>
      )}
    </div>
  );
}

export function MonteCarloPanel() {
  return (
    <ErrorBoundary>
      <MonteCarloPanelBase />
    </ErrorBoundary>
  );
}
