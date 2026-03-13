"use client";

import {
  Area,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  ComposedChart,
  ReferenceLine,
} from "recharts";
import type { EquityPoint } from "@/lib/types";
import { fmt } from "@/lib/format";
import { ErrorBoundary } from "./ErrorBoundary";

export interface EquityChartProps {
  data: EquityPoint[];
  quote?: string;
}

const SHARPE_WINDOW = 20;

function computeDrawdown(equity: number[]): number[] {
  const dd: number[] = [];
  let peak = equity[0] ?? 0;
  for (let i = 0; i < equity.length; i++) {
    const v = equity[i] ?? 0;
    peak = Math.max(peak, v);
    dd.push(peak > 0 ? ((v - peak) / peak) * 100 : 0);
  }
  return dd;
}

function computeRollingSharpe(equity: number[], window: number): number[] {
  const sharpe: number[] = [];
  for (let i = 0; i < equity.length; i++) {
    if (i < window) {
      sharpe.push(NaN);
      continue;
    }
    const slice = equity.slice(i - window, i + 1);
    const returns: number[] = [];
    for (let j = 1; j < slice.length; j++) {
      const prev = slice[j - 1]!;
      if (prev > 0) returns.push((slice[j]! - prev) / prev);
    }
    if (returns.length < 2) {
      sharpe.push(NaN);
      continue;
    }
    const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
    const variance =
      returns.reduce((s, r) => s + (r - mean) ** 2, 0) / returns.length;
    const std = Math.sqrt(variance) || 1e-10;
    sharpe.push((mean / std) * Math.sqrt(252));
  }
  return sharpe;
}

function EquityChartBase({ data, quote = "USDC" }: EquityChartProps) {
  const equityValues = data.map((d) => d.equity);
  const initialEquity = equityValues[0] ?? 0;
  const drawdown = computeDrawdown(equityValues);
  const sharpe = computeRollingSharpe(equityValues, SHARPE_WINDOW);

  const cd = data.map((d, i) => ({
    t: new Date(d.timestamp).toLocaleTimeString("de-DE", {
      hour: "2-digit",
      minute: "2-digit",
    }),
    equity: d.equity,
    pnl: initialEquity > 0 ? d.equity - initialEquity : 0,
    drawdown: drawdown[i] ?? 0,
    sharpe: Number.isFinite(sharpe[i]) ? sharpe[i]! : null,
  }));

  const up = cd.length > 1 && (cd[cd.length - 1]?.equity ?? 0) >= (cd[0]?.equity ?? 0);
  const col = up ? "var(--up)" : "var(--down)";
  const mn = Math.min(...equityValues, initialEquity);
  const mx = Math.max(...equityValues, initialEquity);
  return (
    <div className="card p-4 sm:p-5 h-full">
      <div className="flex items-center justify-between mb-0.5">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold">
          Kapitalverlauf
        </h3>
        <span className="text-[9px] text-[var(--text-quaternary)] font-mono">
          {fmt(mn)} – {fmt(mx)}
        </span>
      </div>
      <div className="flex items-baseline gap-2 mb-3 flex-wrap">
        <span className="text-xl font-bold font-mono">
          {fmt(cd[cd.length - 1]?.equity || 0)}
        </span>
        <span className="text-[10px] text-[var(--text-tertiary)]">{quote}</span>
        <span
          className={`text-[11px] font-mono font-semibold ml-1 ${up ? "text-[var(--up)]" : "text-[var(--down)]"}`}
        >
          {up ? "+" : ""}
          {fmt((cd[cd.length - 1]?.equity || 0) - initialEquity)} (
          {fmt(
            initialEquity > 0
              ? (((cd[cd.length - 1]?.equity || 0) / initialEquity - 1) * 100)
              : 0
          )}
          %)
        </span>
        <span className="text-[9px] text-[var(--text-quaternary)] ml-2">
          PnL | Drawdown | Sharpe(20)
        </span>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={cd} margin={{ top: 0, right: 0, left: -15, bottom: 0 }}>
          <defs>
            <linearGradient id="eqG" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={col} stopOpacity={0.15} />
              <stop offset="100%" stopColor={col} stopOpacity={0} />
            </linearGradient>
            <linearGradient id="ddG" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--down)" stopOpacity={0.2} />
              <stop offset="100%" stopColor="var(--down)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="t"
            tick={{ fill: "var(--text-quaternary)", fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            yAxisId="equity"
            tick={{ fill: "var(--text-quaternary)", fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            domain={[mn * 0.999, mx * 1.001]}
            width={50}
            tickFormatter={(v) => fmt(v, 0)}
          />
          <YAxis
            yAxisId="drawdown"
            orientation="right"
            tick={{ fill: "var(--down)", fontSize: 8 }}
            axisLine={false}
            tickLine={false}
            width={36}
            domain={[(dataMin: number) => Math.min(dataMin, -0.5), 0]}
            tickFormatter={(v) => v.toFixed(1) + "%"}
          />
          <YAxis
            yAxisId="sharpe"
            orientation="right"
            tick={{ fill: "var(--warn)", fontSize: 8 }}
            axisLine={false}
            tickLine={false}
            width={28}
            domain={[-1, 2]}
            tickFormatter={(v) => v.toFixed(1)}
          />
          <Tooltip
            contentStyle={{
              background: "var(--bg-elevated)",
              border: "1px solid var(--border-accent)",
              borderRadius: 10,
              padding: "6px 10px",
              fontSize: 11,
            }}
            labelStyle={{ color: "var(--text-tertiary)", marginBottom: 2, fontSize: 10 }}
            formatter={(v: number, name: string) => {
              if (name === "equity") return [fmt(v) + " " + quote, "Kapital"];
              if (name === "pnl") return [(v >= 0 ? "+" : "") + fmt(v, 4), "PnL"];
              if (name === "drawdown") return [fmt(v, 2) + "%", "Drawdown"];
              if (name === "sharpe") return [v != null ? v.toFixed(2) : "—", "Sharpe"];
              return [String(v), name];
            }}
            itemStyle={{ fontFamily: "JetBrains Mono, monospace" }}
          />
          <ReferenceLine
            yAxisId="equity"
            y={initialEquity}
            stroke="var(--text-quaternary)"
            strokeDasharray="4 4"
            strokeOpacity={0.4}
          />
          <Area
            yAxisId="equity"
            type="monotone"
            dataKey="equity"
            stroke={col}
            strokeWidth={1.5}
            fill="url(#eqG)"
            dot={false}
            activeDot={{ r: 3, fill: col, strokeWidth: 0 }}
          />
          <Line
            yAxisId="equity"
            type="monotone"
            dataKey="pnl"
            stroke="var(--accent)"
            strokeWidth={1}
            dot={false}
            activeDot={{ r: 2 }}
          />
          <Area
            yAxisId="drawdown"
            type="monotone"
            dataKey="drawdown"
            stroke="var(--down)"
            strokeWidth={1}
            fill="url(#ddG)"
            dot={false}
            baseLine={0}
            isAnimationActive={false}
          />
          {cd.some((d) => d.sharpe != null) && (
            <Line
              yAxisId="sharpe"
              type="monotone"
              dataKey="sharpe"
              stroke="var(--warn)"
              strokeWidth={1}
              dot={false}
              connectNulls
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export function EquityChart(props: EquityChartProps) {
  return (
    <ErrorBoundary>
      <EquityChartBase {...props} />
    </ErrorBoundary>
  );
}
