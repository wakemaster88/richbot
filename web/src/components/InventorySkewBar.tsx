"use client";

import type { PairMetrics } from "@/lib/types";
import { fmt } from "@/lib/format";

export interface InventorySkewBarProps {
  skew: NonNullable<PairMetrics["skew"]>;
  pair: string;
}

export function InventorySkewBar({ skew, pair }: InventorySkewBarProps) {
  const [base, quote] = pair.split("/");
  const skewPct = skew.skew_pct ?? 0;
  const skewFactor = skew.skew_factor ?? 0;
  const currentRatio = skew.current_ratio ?? 0;
  const targetRatio = skew.target_ratio ?? 0.5;

  if (skewFactor === 0) return null;

  return (
    <div className="pb-2 border-b border-[var(--border-subtle)] mb-2">
      <div className="flex items-center justify-between text-[9px] text-[var(--text-quaternary)] mb-1">
        <span>
          Inventory:{" "}
          <strong className="text-[var(--text-secondary)]">
            {(currentRatio * 100).toFixed(0)}% {base}
          </strong>
          {" / "}
          <strong className="text-[var(--text-secondary)]">
            {((1 - currentRatio) * 100).toFixed(0)}% {quote || "USDC"}
          </strong>
          <span className="text-[8px] ml-1">(Ziel: {(targetRatio * 100).toFixed(0)}/{((1 - targetRatio) * 100).toFixed(0)})</span>
        </span>
        <span className={`font-semibold ${Math.abs(skewPct) > 30 ? "text-[var(--warn)]" : "text-[var(--text-tertiary)]"}`}>
          {skewPct > 0 ? "+" : ""}{skewPct.toFixed(1)}%
        </span>
      </div>
      <div className="relative h-2 rounded-full overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
        <div className="absolute top-0 bottom-0 left-1/2 w-px" style={{ background: "var(--border-subtle)" }} />
        <div
          className="absolute top-0 bottom-0 rounded-full transition-all duration-500"
          style={{
            background: skewFactor > 0 ? "var(--warn)" : "var(--accent)",
            left: skewFactor >= 0 ? "50%" : `${50 + skewFactor * 50}%`,
            width: `${Math.min(Math.abs(skewFactor) * 50, 50)}%`,
          }}
        />
      </div>
      <div className="flex justify-between text-[7px] text-[var(--text-quaternary)] mt-0.5">
        <span>−50%</span>
        <span className="font-medium" style={{ color: Math.abs(skewPct) > 30 ? "var(--warn)" : "var(--text-tertiary)" }}>
          {skew.description ?? ""}
        </span>
        <span>+50%</span>
      </div>
      {skew.needs_rebalance && (
        <div className="flex items-center gap-1 mt-1 px-1.5 py-0.5 rounded text-[8px] font-medium"
          style={{ background: "color-mix(in srgb, var(--warn) 12%, transparent)", color: "var(--warn)" }}>
          <span>⚠</span>
          <span>Rebalance empfohlen — Skew zu hoch ({skewPct > 0 ? "+" : ""}{skewPct.toFixed(0)}%)</span>
        </div>
      )}
    </div>
  );
}
