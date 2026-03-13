"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Cell,
} from "recharts";
import { fmt } from "@/lib/format";
import { ErrorBoundary } from "./ErrorBoundary";

export interface PnlChartProps {
  data: { zeit: string; pnl: number }[];
  quote?: string;
}

function PnlChartBase({ data, quote = "USDC" }: PnlChartProps) {
  return (
    <div className="card p-4 sm:p-5 h-full">
      <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
        PnL pro Stunde
      </h3>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} margin={{ top: 0, right: 0, left: -15, bottom: 0 }}>
          <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="zeit"
            tick={{ fill: "var(--text-quaternary)", fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            interval={3}
          />
          <YAxis
            tick={{ fill: "var(--text-quaternary)", fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            width={35}
          />
          <Tooltip
            contentStyle={{
              background: "var(--bg-elevated)",
              border: "1px solid var(--border-accent)",
              borderRadius: 10,
              padding: "6px 10px",
              fontSize: 11,
            }}
            formatter={(v: number) => [`${v >= 0 ? "+" : ""}${fmt(v, 4)} ${quote}`, "PnL"]}
          />
          <Bar dataKey="pnl" radius={[3, 3, 0, 0]} maxBarSize={14}>
            {data.map((d, i) => (
              <Cell
                key={i}
                fill={d.pnl >= 0 ? "var(--up)" : "var(--down)"}
                fillOpacity={0.65}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function PnlChart(props: PnlChartProps) {
  return (
    <ErrorBoundary>
      <PnlChartBase {...props} />
    </ErrorBoundary>
  );
}
