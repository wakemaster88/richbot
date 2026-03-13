"use client";

import type { PairMetrics, BotEvent } from "@/lib/types";
import { fmt } from "@/lib/format";
import { CB_COLORS, CB_BG, EVT_ICONS, EVT_COLORS } from "@/lib/constants";
import { InventorySkewBar } from "./InventorySkewBar";
import { ErrorBoundary } from "./ErrorBoundary";

export interface PairInfoCardProps {
  pair: string;
  m: PairMetrics;
  quote?: string;
  events?: BotEvent[];
}

function PairInfoCardBase({ pair, m, quote = "USDC", events = [] }: PairInfoCardProps) {
  const up = m.total_pnl >= 0;
  const coin = pair.split("/")[0];
  const pairEvents = events
    .filter((ev) => {
      const d = ev.detail as Record<string, unknown> | null;
      if (d?.pair === pair) return true;
      if (ev.message.includes(pair) || ev.message.includes(coin)) return true;
      return false;
    })
    .slice(0, 8);

  return (
    <div className="card p-4 sm:p-5 fade-in flex flex-col h-full">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center font-bold text-[10px]"
            style={{ background: up ? "var(--up-bg)" : "var(--down-bg)", color: up ? "var(--up)" : "var(--down)" }}
          >
            {coin}
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <h3 className="font-semibold text-sm leading-tight">{pair}</h3>
              {m.circuit_breaker && m.circuit_breaker.level !== "GREEN" && (
                <span
                  className="text-[7px] font-bold px-1 py-px rounded"
                  style={{
                    background: CB_BG[m.circuit_breaker.level as keyof typeof CB_BG] || CB_BG.GREEN,
                    color: CB_COLORS[m.circuit_breaker.level as keyof typeof CB_COLORS] || CB_COLORS.GREEN,
                  }}
                >
                  {m.circuit_breaker.level}
                </span>
              )}
            </div>
            <p className="text-[9px] text-[var(--text-quaternary)] font-mono">{m.range}</p>
          </div>
        </div>
        <div className="text-right">
          <p className="text-lg font-bold font-mono tracking-tight">{fmt(m.price)}</p>
          <p className={`text-[11px] font-mono font-semibold ${up ? "text-[var(--up)]" : "text-[var(--down)]"}`}>
            {up ? "+" : ""}
            {fmt(m.total_pnl, 4)} {quote}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2 mb-3">
        <div className="card-inner px-2 py-1.5 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">Grid</p>
          <p className="text-[12px] font-bold font-mono">
            {m.active_orders}/{m.grid_configured || m.grid_levels}
            {(m.partially_filled_orders ?? 0) > 0 && (
              <span className="text-[9px] text-[#f59e0b] ml-0.5">({m.partially_filled_orders}⏳)</span>
            )}
          </p>
        </div>
        <div className="card-inner px-2 py-1.5 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">Trades</p>
          <p className="text-[12px] font-bold font-mono">{m.trade_count}</p>
        </div>
        <div className="card-inner px-2 py-1.5 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">Sharpe</p>
          <p
            className={`text-[12px] font-bold font-mono ${m.sharpe_ratio >= 1 ? "text-[var(--up)]" : ""}`}
          >
            {m.sharpe_ratio.toFixed(2)}
          </p>
        </div>
        <div className="card-inner px-2 py-1.5 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">DD</p>
          <p className="text-[12px] font-bold font-mono text-[var(--warn)]">{fmt(m.max_drawdown_pct)}%</p>
        </div>
      </div>

      {(m.annualized_return_pct || m.fees_paid || m.fee_metrics) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 pb-2 border-b border-[var(--border-subtle)] text-[9px] text-[var(--text-quaternary)] mb-2">
          {m.fee_metrics && (
            <span>
              Netto/Trade:{" "}
              <strong
                className={
                  m.fee_metrics.net_profit_per_trade_pct > 0 ? "text-[var(--up)]" : "text-[var(--down)]"
                }
              >
                {m.fee_metrics.net_profit_per_trade_pct > 0 ? "+" : ""}
                {m.fee_metrics.net_profit_per_trade_pct.toFixed(2)}%
              </strong>
            </span>
          )}
          {m.avg_slippage_bps !== undefined && m.avg_slippage_bps > 0 && (
            <span>
              Slippage:{" "}
              <strong
                className={
                  m.avg_slippage_bps > 5 ? "text-[var(--down)]" : "text-[var(--text-tertiary)]"
                }
              >
                {m.avg_slippage_bps.toFixed(1)} bps
              </strong>
            </span>
          )}
          {m.maker_fill_pct !== undefined && (
            <span>
              Maker:{" "}
              <strong className="text-[var(--text-tertiary)]">{m.maker_fill_pct.toFixed(0)}%</strong>
            </span>
          )}
          {m.annualized_return_pct !== undefined && (
            <span>
              Rendite:{" "}
              <strong className="text-[var(--text-tertiary)]">{fmt(m.annualized_return_pct)}%</strong>
            </span>
          )}
          {m.fees_paid !== undefined && (
            <span>
              Gebuehren: <strong className="text-[var(--text-tertiary)]">{fmt(m.fees_paid)}</strong>
            </span>
          )}
          <span>
            Kapital: <strong className="text-[var(--text-tertiary)]">{fmt(m.current_equity)}</strong>
          </span>
        </div>
      )}
      {m.inventory && m.inventory.base_inventory > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 pb-2 border-b border-[var(--border-subtle)] text-[9px] text-[var(--text-quaternary)] mb-2">
          <span>
            Avg. Entry:{" "}
            <strong className="text-[var(--text-secondary)] font-mono">
              {fmt(m.inventory.avg_cost_basis)}
            </strong>
          </span>
          <span>
            Unrealized:{" "}
            <strong
              className={
                m.inventory.unrealized_pnl >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"
              }
              style={{ fontFamily: "JetBrains Mono, monospace" }}
            >
              {m.inventory.unrealized_pnl >= 0 ? "+" : ""}
              {fmt(m.inventory.unrealized_pnl, 4)}
            </strong>
          </span>
          <span>
            Cost:{" "}
            <strong className="text-[var(--text-tertiary)] font-mono">{fmt(m.inventory.total_cost)}</strong>
            {" → "}
            <strong className="text-[var(--text-secondary)] font-mono">
              {fmt(m.inventory.market_value)}
            </strong>
          </span>
          <span>
            Pos:{" "}
            <strong className="text-[var(--text-tertiary)] font-mono">
              {m.inventory.base_inventory.toFixed(6)}
            </strong>
          </span>
        </div>
      )}
      {m.skew && m.skew.skew_factor !== 0 && (
        <InventorySkewBar skew={m.skew} pair={pair} />
      )}
      {m.fee_metrics && !m.fee_metrics.spacing_is_profitable && (
        <div
          className="flex items-center gap-1.5 px-2 py-1 mb-2 rounded text-[9px] font-medium"
          style={{
            background: "color-mix(in srgb, var(--down) 12%, transparent)",
            color: "var(--down)",
          }}
        >
          <span>⚠</span>
          <span>
            Grid-Abstand ({m.fee_metrics.current_spacing_pct.toFixed(2)}%) zu eng fuer Gebuehren (min.{" "}
            {m.fee_metrics.min_profitable_spacing_pct.toFixed(2)}%)
          </span>
        </div>
      )}
      {m.spread && m.spread.current_bps > 0 && (
        <div className="pb-2 border-b border-[var(--border-subtle)] mb-2">
          <div className="flex items-center justify-between text-[9px] text-[var(--text-quaternary)] mb-1">
            <span>
              Spread:{" "}
              <strong
                className={`font-mono ${m.spread.is_wide ? "text-[var(--warn)]" : "text-[var(--text-secondary)]"}`}
              >
                {m.spread.current_bps.toFixed(1)} bps
              </strong>
              <span className="text-[8px] ml-1">(avg: {m.spread.avg_60m_bps.toFixed(1)} bps)</span>
            </span>
            <span
              className={`text-[8px] font-mono ${m.spread.percentile > 80 ? "text-[var(--warn)]" : "text-[var(--text-quaternary)]"}`}
            >
              P{m.spread.percentile.toFixed(0)}
            </span>
          </div>
          {m.spread.history.length > 2 &&
            (() => {
              const pts = m.spread.history;
              const maxBps = Math.max(...pts.map((p) => p.bps), 0.1);
              const w = 100;
              const h = 20;
              const path = pts
                .map((p, i) => {
                  const x = (i / Math.max(pts.length - 1, 1)) * w;
                  const y = h - (p.bps / maxBps) * h;
                  return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
                })
                .join(" ");
              return (
                <svg
                  viewBox={`0 0 ${w} ${h}`}
                  className="w-full"
                  style={{ height: 20 }}
                  preserveAspectRatio="none"
                >
                  <path
                    d={path}
                    fill="none"
                    stroke={m.spread.is_wide ? "var(--warn)" : "var(--accent)"}
                    strokeWidth="1.2"
                  />
                </svg>
              );
            })()}
          {m.spread.is_wide && (
            <div
              className="flex items-center gap-1 mt-1 px-1.5 py-0.5 rounded text-[8px] font-medium"
              style={{
                background: "color-mix(in srgb, var(--warn) 12%, transparent)",
                color: "var(--warn)",
              }}
            >
              <span>⚠</span>
              <span>Spread ungewoehnlich weit — Grid automatisch angepasst</span>
            </div>
          )}
        </div>
      )}

      <div className="flex-1 min-h-0">
        {pairEvents.length > 0 ? (
          <div className="space-y-0 overflow-y-auto max-h-48">
            {pairEvents.map((ev) => {
              const col =
                EVT_COLORS[
                  (ev.level === "critical"
                    ? "critical"
                    : ev.level === "warn"
                      ? "warn"
                      : ev.level === "error"
                        ? "error"
                        : ev.level === "success"
                          ? "success"
                          : ev.category) as keyof typeof EVT_COLORS
                ] || "var(--text-tertiary)";
              const icon = EVT_ICONS[ev.category as keyof typeof EVT_ICONS] || "\u00B7";
              const zeit = new Date(ev.timestamp).toLocaleTimeString("de-DE", {
                hour: "2-digit",
                minute: "2-digit",
              });
              return (
                <div
                  key={ev.id}
                  className="flex items-start gap-1.5 text-[9px] py-1 px-1 rounded hover:bg-[var(--bg-secondary)] transition-colors"
                >
                  <span
                    className="shrink-0 w-3.5 h-3.5 rounded flex items-center justify-center text-[7px] font-bold mt-px"
                    style={{
                      background: `color-mix(in srgb, ${col} 15%, transparent)`,
                      color: col,
                    }}
                  >
                    {icon}
                  </span>
                  <p className="min-w-0 flex-1 text-[var(--text-secondary)] leading-snug truncate">
                    {ev.message}
                  </p>
                  <span className="shrink-0 text-[8px] text-[var(--text-quaternary)] font-mono mt-px">
                    {zeit}
                  </span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-[9px] text-[var(--text-quaternary)]">
            Keine Aktivitaet
          </div>
        )}
      </div>
    </div>
  );
}

export function PairInfoCard(props: PairInfoCardProps) {
  return (
    <ErrorBoundary>
      <PairInfoCardBase {...props} />
    </ErrorBoundary>
  );
}
