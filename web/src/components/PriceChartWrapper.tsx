"use client";

import dynamic from "next/dynamic";
import type { PriceChartProps } from "./PriceChartTradingView";

const PriceChartLazy = dynamic(
  () =>
    import("./PriceChartTradingView").then((mod) => ({
      default: mod.PriceChartTradingView,
    })),
  {
    ssr: false,
    loading: () => (
      <div className="card p-5 h-full flex flex-col">
        <div className="flex items-center justify-between mb-3">
          <div className="h-3 w-24 rounded bg-[var(--bg-elevated)] animate-pulse" />
          <div className="flex gap-1">
            {["15m", "1h", "4h", "1d"].map((iv) => (
              <div key={iv} className="h-4 w-6 rounded bg-[var(--bg-elevated)] animate-pulse" />
            ))}
          </div>
        </div>
        <div className="h-5 w-32 rounded bg-[var(--bg-elevated)] animate-pulse mb-3" />
        <div className="flex-1 rounded-lg bg-[var(--bg-elevated)] animate-pulse min-h-[300px]" />
      </div>
    ),
  }
);

export function PriceChart(props: PriceChartProps) {
  return <PriceChartLazy {...props} />;
}
