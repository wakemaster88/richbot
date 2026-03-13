"use client";

import type { CorrelationData } from "@/lib/types";
import { fmt } from "@/lib/format";
import { ErrorBoundary } from "./ErrorBoundary";

export interface CorrelationPanelProps {
  data: CorrelationData;
  quote?: string;
}

function CorrelationPanelBase({ data, quote = "USDC" }: CorrelationPanelProps) {
  const { matrix, pairs, portfolio_var_pct, portfolio_var_abs, high_corr_warnings, size_adjustments } =
    data;
  const n = pairs.length;
  const coins = pairs.map((p) => p.split("/")[0]);

  const corrColor = (v: number) => {
    if (v >= 0.9) return "var(--down)";
    if (v >= 0.7) return "var(--warn)";
    if (v >= 0.4) return "var(--text-tertiary)";
    if (v >= 0) return "var(--up)";
    return "var(--accent)";
  };
  const corrBg = (v: number) => {
    const abs = Math.abs(v);
    if (abs >= 0.9) return "color-mix(in srgb, var(--down) 25%, transparent)";
    if (abs >= 0.7) return "color-mix(in srgb, var(--warn) 20%, transparent)";
    if (abs >= 0.4) return "color-mix(in srgb, var(--text-tertiary) 10%, transparent)";
    return "transparent";
  };

  return (
    <div className="card p-4 sm:p-5 fade-in">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-xs">Korrelation &amp; Portfolio-Risiko</h3>
        <div className="flex items-center gap-2">
          <span className="text-[9px] text-[var(--text-quaternary)]">Tages-VaR:</span>
          <span
            className={`text-xs font-bold font-mono ${portfolio_var_pct > 5 ? "text-[var(--down)]" : portfolio_var_pct > 3 ? "text-[var(--warn)]" : "text-[var(--up)]"}`}
          >
            {portfolio_var_pct.toFixed(1)}%
            <span className="text-[9px] font-normal text-[var(--text-quaternary)] ml-1">
              ({fmt(portfolio_var_abs)} {quote})
            </span>
          </span>
        </div>
      </div>

      {n >= 2 && matrix.length >= 2 && (
        <div className="mb-3">
          <div
            className="inline-grid gap-px rounded overflow-hidden"
            style={{
              gridTemplateColumns: `48px repeat(${n}, 1fr)`,
              background: "var(--border-subtle)",
            }}
          >
            <div className="bg-[var(--bg-primary)] p-1" />
            {coins.map((c) => (
              <div
                key={`h-${c}`}
                className="bg-[var(--bg-primary)] px-2 py-1.5 text-center text-[9px] font-bold text-[var(--text-tertiary)]"
              >
                {c}
              </div>
            ))}
            {matrix.map((row, i) => {
              const frag = [
                <div
                  key={`l-${i}`}
                  className="bg-[var(--bg-primary)] px-2 py-1.5 text-[9px] font-bold text-[var(--text-tertiary)] flex items-center"
                >
                  {coins[i]}
                </div>,
                ...row.map((v, j) => (
                  <div
                    key={`c-${i}-${j}`}
                    className="px-2 py-1.5 text-center text-[11px] font-mono font-semibold transition-colors"
                    style={{
                      background: i === j ? "var(--bg-secondary)" : corrBg(v),
                      color: i === j ? "var(--text-quaternary)" : corrColor(v),
                    }}
                  >
                    {i === j ? "1.00" : v.toFixed(2)}
                  </div>
                )),
              ];
              return frag;
            })}
          </div>
          <div className="flex items-center gap-3 mt-1.5 text-[8px] text-[var(--text-quaternary)]">
            <span className="flex items-center gap-1">
              <span
                className="w-2 h-2 rounded-sm"
                style={{ background: "color-mix(in srgb, var(--up) 30%, transparent)" }}
              />
              niedrig
            </span>
            <span className="flex items-center gap-1">
              <span
                className="w-2 h-2 rounded-sm"
                style={{ background: "color-mix(in srgb, var(--warn) 30%, transparent)" }}
              />
              hoch (&gt;0.7)
            </span>
            <span className="flex items-center gap-1">
              <span
                className="w-2 h-2 rounded-sm"
                style={{ background: "color-mix(in srgb, var(--down) 30%, transparent)" }}
              />
              extrem (&gt;0.9)
            </span>
          </div>
        </div>
      )}

      {high_corr_warnings.length > 0 && (
        <div className="space-y-1 mb-2">
          {high_corr_warnings.map((w, i) => (
            <div
              key={i}
              className="flex items-center gap-1.5 px-2 py-1 rounded text-[9px] font-medium"
              style={{
                background: w.extreme
                  ? "color-mix(in srgb, var(--down) 12%, transparent)"
                  : "color-mix(in srgb, var(--warn) 12%, transparent)",
                color: w.extreme ? "var(--down)" : "var(--warn)",
              }}
            >
              <span>{w.extreme ? "⚠" : "⚡"}</span>
              <span>
                {w.pair_a.split("/")[0]} / {w.pair_b.split("/")[0]}:{(w.correlation * 100).toFixed(0)}% korreliert
                {w.extreme && " — Diversifikation pruefen!"}
              </span>
            </div>
          ))}
        </div>
      )}

      {Object.keys(size_adjustments).length > 0 && (
        <div className="flex flex-wrap gap-2 text-[9px] text-[var(--text-quaternary)]">
          {Object.entries(size_adjustments).map(([p, factor]) => (
            <span key={p}>
              {p.split("/")[0]}:{" "}
              <strong className="text-[var(--warn)]">
                −{((1 - factor) * 100).toFixed(0)}%
              </strong>{" "}
              Size
            </span>
          ))}
        </div>
      )}

      {n < 2 && (
        <p className="text-[9px] text-[var(--text-quaternary)]">
          Korrelationsmatrix verfuegbar ab 2 Pairs
        </p>
      )}
    </div>
  );
}

export function CorrelationPanel(props: CorrelationPanelProps) {
  return (
    <ErrorBoundary>
      <CorrelationPanelBase {...props} />
    </ErrorBoundary>
  );
}
