"use client";

import { useState } from "react";
import type { Trade } from "@/lib/types";
import { fmt, fmtAmount } from "@/lib/format";
import { ErrorBoundary } from "./ErrorBoundary";

export interface TrailingTPEntry {
  pair: string;
  side: string;
  entry_price: number;
  amount: number;
  highest: number;
  lowest: number;
  age_sec: number;
}

export interface TradesTableProps {
  trades: Trade[];
  trailingTp?: TrailingTPEntry[];
  quote?: string;
}

function TradesTableBase({ trades, trailingTp, quote = "USDC" }: TradesTableProps) {
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
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold">
          Letzte Trades
        </h3>
        <span className="text-[9px] text-[var(--text-quaternary)] font-mono">{trades.length}</span>
      </div>

      {/* Trailing-TP Active Entries */}
      {trailingTp && trailingTp.length > 0 && (
        <div
          className="px-4 py-2 border-b border-[var(--border-subtle)]"
          style={{ background: "rgba(59,130,246,0.05)" }}
        >
          <p className="text-[9px] text-[#3b82f6] uppercase tracking-wider font-semibold mb-1.5">
            Trailing Take-Profit aktiv ({trailingTp.length})
          </p>
          <div className="space-y-1">
            {trailingTp.slice(0, 5).map((tp, i) => {
              const isBuy = tp.side === "buy";
              const extreme = isBuy ? tp.highest : tp.lowest;
              const profitPct = isBuy
                ? ((extreme - tp.entry_price) / tp.entry_price) * 100
                : ((tp.entry_price - extreme) / tp.entry_price) * 100;
              return (
                <div key={i} className="flex items-center gap-2 text-[10px] font-mono">
                  <span
                    className="px-1 py-0.5 rounded text-[8px] font-bold"
                    style={{
                      background: isBuy ? "var(--up-bg)" : "var(--down-bg)",
                      color: isBuy ? "var(--up)" : "var(--down)",
                    }}
                  >
                    {isBuy ? "K" : "V"}
                  </span>
                  <span className="text-[var(--text-tertiary)]">{tp.pair.split("/")[0]}</span>
                  <span>{fmt(tp.entry_price, 0)}</span>
                  <span className="text-[var(--text-quaternary)]">{"\u2192"}</span>
                  <span className="text-[#3b82f6]">{fmt(extreme, 0)}</span>
                  <span
                    className={
                      profitPct >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"
                    }
                  >
                    {profitPct >= 0 ? "+" : ""}
                    {profitPct.toFixed(2)}%
                  </span>
                  <span className="text-[var(--text-quaternary)] ml-auto">
                    {Math.floor(tp.age_sec / 60)}m
                  </span>
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
                <span
                  className="w-1 h-1 rounded-full"
                  style={{
                    background: t.side === "buy" ? "var(--up)" : "var(--down)",
                  }}
                />
                <span
                  className="text-[10px] font-semibold px-1 py-0.5 rounded"
                  style={{
                    background: t.side === "buy" ? "var(--up-bg)" : "var(--down-bg)",
                    color: t.side === "buy" ? "var(--up)" : "var(--down)",
                  }}
                >
                  {t.side === "buy" ? "KAUF" : "VERK."}
                </span>
                <span className="text-xs font-mono">{fmt(t.price, 0)}</span>
              </div>
              <span
                className="text-xs font-mono font-semibold"
                style={{ color: t.pnl >= 0 ? "var(--up)" : "var(--down)" }}
              >
                {t.pnl >= 0 ? "+" : ""}
                {t.pnl.toFixed(4)}
              </span>
            </div>
            <div className="flex justify-between text-[9px] text-[var(--text-quaternary)]">
              <span className="font-mono">
                {fmtAmount(t.amount, t.pair.split("/")[0])} ({fmt(t.amount * t.price)} {quote})
              </span>
              <div className="flex items-center gap-2">
                {(t.slippageBps ?? 0) > 0 && (
                  <span
                    className="font-mono"
                    style={{
                      color:
                        (t.slippageBps ?? 0) > 5 ? "var(--down)" : "var(--text-quaternary)",
                    }}
                  >
                    {(t.slippageBps ?? 0).toFixed(1)}bps
                  </span>
                )}
                <span>
                  {new Date(t.timestamp).toLocaleString("de-DE", {
                    day: "2-digit",
                    month: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </div>
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
              <th className="text-right px-4 py-2 font-medium">Slip.</th>
              <th className="text-right px-4 py-2 font-medium">PnL</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-subtle)]">
            {shown.map((t) => (
              <tr key={t.id} className="hover:bg-[var(--bg-card-hover)] transition-colors">
                <td className="px-4 py-2 font-mono text-[10px] text-[var(--text-tertiary)]">
                  {new Date(t.timestamp).toLocaleString("de-DE", {
                    day: "2-digit",
                    month: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })}
                </td>
                <td className="px-4 py-2">
                  <span
                    className="inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold"
                    style={{
                      background: t.side === "buy" ? "var(--up-bg)" : "var(--down-bg)",
                      color: t.side === "buy" ? "var(--up)" : "var(--down)",
                    }}
                  >
                    {t.side === "buy" ? "KAUF" : "VERK."}
                  </span>
                </td>
                <td className="px-4 py-2 text-right font-mono">{fmt(t.price, 0)}</td>
                <td className="px-4 py-2 text-right font-mono text-[var(--text-tertiary)]">
                  {fmtAmount(t.amount, t.pair.split("/")[0])}
                </td>
                <td className="px-4 py-2 text-right font-mono text-[var(--text-tertiary)]">
                  {fmt(t.amount * t.price)} {quote}
                </td>
                <td
                  className="px-4 py-2 text-right font-mono text-[10px]"
                  style={{
                    color:
                      (t.slippageBps ?? 0) > 5
                        ? "var(--down)"
                        : (t.slippageBps ?? 0) > 0
                          ? "var(--text-tertiary)"
                          : "var(--text-quaternary)",
                  }}
                >
                  {(t.slippageBps ?? 0) > 0 ? `${(t.slippageBps ?? 0).toFixed(1)}` : "—"}
                </td>
                <td
                  className="px-4 py-2 text-right font-mono font-semibold"
                  style={{ color: t.pnl >= 0 ? "var(--up)" : "var(--down)" }}
                >
                  {t.pnl >= 0 ? "+" : ""}
                  {t.pnl.toFixed(4)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {trades.length > 10 && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full py-2 text-[10px] text-[var(--accent)] hover:bg-[var(--bg-card-hover)] transition-colors border-t border-[var(--border-subtle)]"
        >
          {expanded ? "Weniger anzeigen" : `Alle ${trades.length} Trades anzeigen`}
        </button>
      )}
    </div>
  );
}

export function TradesTable(props: TradesTableProps) {
  return (
    <ErrorBoundary>
      <TradesTableBase {...props} />
    </ErrorBoundary>
  );
}
