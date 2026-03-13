"use client";

import { useEffect, useRef, useState } from "react";
import {
  createChart,
  ColorType,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import type { Trade, OpenOrder, Kline } from "@/lib/types";
import { fmt } from "@/lib/format";
import { bollingerBands, ema, parseRange } from "@/lib/chartUtils";
import { ErrorBoundary } from "./ErrorBoundary";

export interface PriceChartGridMeta {
  levels: number;
  configured?: number;
  buyCount?: number;
  sellCount?: number;
  range: string;
  issue?: string;
  unplaced?: number;
}

export interface PriceChartProps {
  pair: string;
  orders?: OpenOrder[];
  trades?: Trade[];
  gridMeta?: PriceChartGridMeta;
  quote?: string;
  regime?: string;
}

const INTERVALS = ["15m", "1h", "4h", "1d"] as const;
const REGIME_COLORS: Record<string, string> = {
  ranging: "rgba(34, 197, 94, 0.06)",
  trend_up: "rgba(59, 130, 246, 0.06)",
  trend_down: "rgba(245, 158, 11, 0.08)",
  volatile: "rgba(239, 68, 68, 0.08)",
};

function PriceChartBase({
  pair,
  orders = [],
  trades: pairTrades = [],
  gridMeta,
  quote = "USDC",
  regime = "ranging",
}: PriceChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [klines, setKlines] = useState<Kline[]>([]);
  const [interval, setInterval_] = useState<string>("15m");
  const [error, setError] = useState(false);
  const retryRef = useRef(0);
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const sym = pair.replace("/", "");
        const url = `https://api.binance.com/api/v3/klines?symbol=${sym}&interval=${interval}&limit=200`;
        const res = await fetch(url);
        if (!res.ok) throw new Error("API error");
        const raw = await res.json();
        if (!active) return;
        const parsed: Kline[] = raw.map((k: unknown[]) => ({
          t: Number(k[0]),
          o: parseFloat(k[1] as string),
          h: parseFloat(k[2] as string),
          l: parseFloat(k[3] as string),
          c: parseFloat(k[4] as string),
          v: parseFloat(k[5] as string),
        }));
        setKlines(parsed);
        setError(false);
        retryRef.current = 0;
      } catch {
        if (!active) return;
        retryRef.current++;
        if (retryRef.current <= 3) {
          setTimeout(load, 2000 * retryRef.current);
          return;
        }
        try {
          const sym = pair.replace("/", "");
          const res = await fetch(
            `/api/klines?symbol=${sym}&interval=${interval}&limit=200`
          );
          if (res.ok && active) {
            setKlines(await res.json());
            setError(false);
            return;
          }
        } catch {
          /* ignore */
        }
        if (active) setError(true);
      }
    };
    load();
    const iv = window.setInterval(load, 60000);
    return () => {
      active = false;
      clearInterval(iv);
    };
  }, [pair, interval]);

  useEffect(() => {
    if (!chartContainerRef.current || !klines.length) return;

    const cs = getComputedStyle(chartContainerRef.current);
    const resolveVar = (v: string) => cs.getPropertyValue(v).trim() || v;

    const textQuat = resolveVar("--text-quaternary");
    const borderSubtle = resolveVar("--border-subtle");
    const accent = resolveVar("--accent");

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: textQuat,
        fontFamily: "JetBrains Mono, system-ui, sans-serif",
      },
      grid: {
        vertLines: { color: borderSubtle, style: 1 },
        horzLines: { color: borderSubtle, style: 1 },
      },
      crosshair: {
        vertLine: {
          color: accent,
          width: 1,
          labelBackgroundColor: accent,
        },
        horzLine: {
          color: accent,
          width: 1,
          labelBackgroundColor: accent,
        },
      },
      rightPriceScale: {
        borderColor: borderSubtle,
        scaleMargins: { top: 0.1, bottom: 0.2 },
        textColor: textQuat,
      },
      timeScale: {
        borderColor: borderSubtle,
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: { vertTouchDrag: true, horzTouchDrag: true },
    });

    chartRef.current = chart;

    const candleData = klines.map((k) => ({
      time: Math.floor(k.t / 1000) as Time,
      open: k.o,
      high: k.h,
      low: k.l,
      close: k.c,
    }));

    const volumeData = klines.map((k) => ({
      time: Math.floor(k.t / 1000) as Time,
      value: k.v,
      color: k.c >= k.o ? "rgba(34, 197, 94, 0.5)" : "rgba(239, 68, 68, 0.5)",
    }));

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderUpColor: "#22c55e",
      borderDownColor: "#ef4444",
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });
    candleSeries.setData(candleData);

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
      borderVisible: false,
    });
    volumeSeries.setData(volumeData);

    const closes = klines.map((k) => k.c);
    const bb = bollingerBands(closes, 20, 2);
    const ema9 = ema(closes, 9);
    const ema21 = ema(closes, 21);

    const bbUpperData = klines
      .map((k, i) =>
        Number.isFinite(bb.upper[i])
          ? { time: Math.floor(k.t / 1000) as Time, value: bb.upper[i]! }
          : null
      )
      .filter(Boolean) as { time: Time; value: number }[];
    const bbLowerData = klines
      .map((k, i) =>
        Number.isFinite(bb.lower[i])
          ? { time: Math.floor(k.t / 1000) as Time, value: bb.lower[i]! }
          : null
      )
      .filter(Boolean) as { time: Time; value: number }[];

    const bbUpperSeries = chart.addLineSeries({
      color: "rgba(139, 92, 246, 0.6)",
      lineWidth: 1,
    });
    bbUpperSeries.setData(bbUpperData);
    const bbLowerSeries = chart.addLineSeries({
      color: "rgba(139, 92, 246, 0.6)",
      lineWidth: 1,
    });
    bbLowerSeries.setData(bbLowerData);

    const ema9Data = klines
      .map((k, i) =>
        Number.isFinite(ema9[i])
          ? { time: Math.floor(k.t / 1000) as Time, value: ema9[i]! }
          : null
      )
      .filter(Boolean) as { time: Time; value: number }[];
    const ema9Series = chart.addLineSeries({
      color: "#f59e0b",
      lineWidth: 2,
    });
    ema9Series.setData(ema9Data);

    const ema21Data = klines
      .map((k, i) =>
        Number.isFinite(ema21[i])
          ? { time: Math.floor(k.t / 1000) as Time, value: ema21[i]! }
          : null
      )
      .filter(Boolean) as { time: Time; value: number }[];
    const ema21Series = chart.addLineSeries({
      color: "#3b82f6",
      lineWidth: 2,
    });
    ema21Series.setData(ema21Data);

    const chartStart = klines[0]?.t ?? 0;
    const chartEnd = klines[klines.length - 1]?.t ?? 0;
    const bucketMs = klines.length > 1 ? klines[1]!.t - klines[0]!.t : 300000;
    const visibleTrades = (pairTrades || []).filter((t) => {
      const ts = new Date(t.timestamp).getTime();
      return ts >= chartStart && ts <= chartEnd + bucketMs;
    });

    const markers = visibleTrades.map((t) => ({
      time: Math.floor(new Date(t.timestamp).getTime() / 1000) as Time,
      position: t.side === "buy" ? ("belowBar" as const) : ("aboveBar" as const),
      color: t.side === "buy" ? "#22c55e" : "#ef4444",
      shape: t.side === "buy" ? ("arrowUp" as const) : ("arrowDown" as const),
      text: `${t.side === "buy" ? "K" : "V"} ${fmt(t.price, 0)}`,
    }));
    candleSeries.setMarkers(markers);

    orders.forEach((o) => {
      candleSeries.createPriceLine({
        price: o.price,
        color: o.side === "buy" ? "#22c55e" : "#ef4444",
        lineWidth: o.status === "partially_filled" ? 2 : 1,
        lineStyle: o.status === "partially_filled" ? 2 : 0,
        axisLabelVisible: true,
        title: `${o.side === "buy" ? "K" : "V"} ${fmt(o.price, 0)}`,
      });
    });

    const range = gridMeta?.range ? parseRange(gridMeta.range) : null;
    if (range) {
      candleSeries.createPriceLine({
        price: range.low,
        color: "rgba(34, 197, 94, 0.4)",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: false,
      });
      candleSeries.createPriceLine({
        price: range.high,
        color: "rgba(239, 68, 68, 0.4)",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: false,
      });
    }

    const handleResize = () => chart.applyOptions({ width: chartContainerRef.current?.clientWidth });
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
    };
  }, [klines, orders, pairTrades, gridMeta?.range]);

  if (error) {
    return (
      <div className="card p-5 h-full flex flex-col items-center justify-center gap-2 text-center">
        <p className="text-xs text-[var(--text-tertiary)]">Preisdaten nicht verfuegbar</p>
        <button
          onClick={() => {
            setError(false);
            retryRef.current = 0;
          }}
          className="text-[10px] text-[var(--accent)] underline"
        >
          Erneut versuchen
        </button>
      </div>
    );
  }

  if (!klines.length) {
    return (
      <div className="card p-5 h-full flex flex-col">
        <div className="flex items-center justify-between mb-3">
          <div className="h-3 w-24 rounded bg-[var(--bg-elevated)] animate-pulse" />
          <div className="flex gap-1">
            {INTERVALS.map((iv) => (
              <div key={iv} className="h-4 w-6 rounded bg-[var(--bg-elevated)] animate-pulse" />
            ))}
          </div>
        </div>
        <div className="h-5 w-32 rounded bg-[var(--bg-elevated)] animate-pulse mb-3" />
        <div className="flex-1 rounded-lg bg-[var(--bg-elevated)] animate-pulse min-h-[300px]" />
      </div>
    );
  }

  const last = klines[klines.length - 1]!;
  const first = klines[0]!;
  const chg = first.c > 0 ? ((last.c - first.c) / first.c) * 100 : 0;

  return (
    <div
      className="card p-4 sm:p-5 h-full overflow-hidden"
      style={{
        background: REGIME_COLORS[regime] || "transparent",
      }}
    >
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold">
          {pair}
        </h3>
        <div className="flex items-center gap-0.5">
          {INTERVALS.map((iv) => (
            <button
              key={iv}
              onClick={() => setInterval_(iv)}
              className="px-1.5 py-0.5 rounded text-[9px] font-mono transition-all"
              style={{
                background: interval === iv ? "var(--accent-bg)" : "transparent",
                color: interval === iv ? "var(--accent)" : "var(--text-quaternary)",
              }}
            >
              {iv}
            </button>
          ))}
        </div>
      </div>
      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-xl font-bold font-mono">{fmt(last.c)}</span>
        <span className="text-[10px] text-[var(--text-tertiary)]">{quote}</span>
        <span
          className="text-[11px] font-mono font-semibold ml-1"
          style={{ color: chg >= 0 ? "var(--up)" : "var(--down)" }}
        >
          {chg >= 0 ? "+" : ""}
          {chg.toFixed(2)}%
        </span>
        <span className="text-[8px] text-[var(--text-quaternary)] ml-2">
          EMA9 / EMA21 / BB20
        </span>
      </div>
      <div className="relative" style={{ height: 320 }}>
        <div ref={chartContainerRef} className="w-full h-full" />
      </div>
      {gridMeta && orders.length > 0 && (
        <div
          className="mt-2 pt-2 flex flex-wrap gap-2 text-[9px]"
          style={{ borderColor: "var(--border-subtle)" }}
        >
          <span className="text-[var(--text-quaternary)]">
            Grid: {gridMeta.buyCount ?? 0}K / {gridMeta.sellCount ?? 0}V = {orders.length}/
            {gridMeta.configured || gridMeta.levels}
          </span>
          {gridMeta.range && (
            <span className="text-[var(--text-quaternary)] font-mono">{gridMeta.range}</span>
          )}
        </div>
      )}
    </div>
  );
}

export function PriceChartTradingView(props: PriceChartProps) {
  return (
    <ErrorBoundary>
      <PriceChartBase {...props} />
    </ErrorBoundary>
  );
}
