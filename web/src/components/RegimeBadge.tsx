"use client";

import { REGIME_STYLE } from "@/lib/constants";
import type { PairMetrics } from "@/lib/types";

interface RegimeBadgeProps {
  pair: string;
  m: PairMetrics;
}

export function RegimeBadge({ pair, m }: RegimeBadgeProps) {
  const regimeKey = m.regime?.regime || "ranging";
  const rs = REGIME_STYLE[regimeKey] || REGIME_STYLE.ranging;
  const ss = m.regime?.sentiment_score ?? 0;
  const sc = m.regime?.sentiment_confidence ?? 0;
  const conf = m.regime?.confidence ?? 0;
  const tp = m.regime?.transition_pending;
  const sIcon = ss > 0.3 ? "\u25B2" : ss < -0.3 ? "\u25BC" : "";
  const sCol = ss > 0.3 ? "var(--up)" : "var(--down)";

  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[9px] font-bold"
      style={{ background: rs.bg, color: rs.color }}
      title={
        `Conf: ${conf.toFixed(0)}%` +
        (sc > 0 ? ` | News: ${ss > 0 ? "+" : ""}${ss.toFixed(2)} (${(sc * 100).toFixed(0)}%)` : "") +
        (tp ? ` | → ${tp}` : "")
      }
    >
      {pair.split("/")[0]} {rs.label}
      {conf > 0 && <span style={{ fontSize: "7px", opacity: 0.8 }}>({conf.toFixed(0)}%)</span>}
      {sIcon && <span style={{ color: sCol, fontSize: "8px" }}>{sIcon}</span>}
      {tp && <span style={{ fontSize: "7px", opacity: 0.7 }}>{"\u2192"}</span>}
    </span>
  );
}
