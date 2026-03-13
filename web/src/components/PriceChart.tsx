"use client";

import { useState, useEffect, useRef } from "react";
import {
  ComposedChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  ReferenceLine,
  Scatter,
} from "recharts";
import type { Trade, OpenOrder, Kline } from "@/lib/types";
import { fmt, fmtAmount } from "@/lib/format";
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
}

function PriceChartBase({ pair, orders, trades: pairTrades, gridMeta, quote = "USDC" }: PriceChartProps) {
  const [klines, setKlines] = useState<Kline[]>([]);
  const [interval, setInterval_] = useState("5m");
  const [error, setError] = useState(false);
  const retryRef = useRef(0);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const sym = pair.replace("/", "");
        const url = `https://api.binance.com/api/v3/klines?symbol=${sym}&interval=${interval}&limit=120`;
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
          const res = await fetch(`/api/klines?symbol=${sym}&interval=${interval}&limit=120`);
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
    const iv = window.setInterval(load, 30000);
    return () => {
      active = false;
      clearInterval(iv);
    };
  }, [pair, interval]);

  const intervals = ["1m", "5m", "15m", "1h", "4h", "1d"];

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
            {intervals.map((iv) => (
              <div
                key={iv}
                className="h-4 w-6 rounded bg-[var(--bg-elevated)] animate-pulse"
              />
            ))}
          </div>
        </div>
        <div className="h-5 w-32 rounded bg-[var(--bg-elevated)] animate-pulse mb-3" />
        <div
          className="flex-1 rounded-lg bg-[var(--bg-elevated)] animate-pulse"
          style={{ minHeight: 180 }}
        />
      </div>
    );
  }

  const chartStart = klines[0]?.t || 0;
  const chartEnd = klines[klines.length - 1]?.t || 0;
  const bucketMs = klines.length > 1 ? klines[1].t - klines[0].t : 300000;

  const visibleTrades = (pairTrades || []).filter((t) => {
    const ts = new Date(t.timestamp).getTime();
    return ts >= chartStart && ts <= chartEnd + bucketMs;
  });

  const tradeMap = new Map<number, Trade[]>();
  for (const t of visibleTrades) {
    const ts = new Date(t.timestamp).getTime();
    let bestIdx = 0;
    let bestDist = Infinity;
    for (let i = 0; i < klines.length; i++) {
      const dist = Math.abs(klines[i].t - ts);
      if (dist < bestDist) {
        bestDist = dist;
        bestIdx = i;
      }
    }
    const arr = tradeMap.get(bestIdx) || [];
    arr.push(t);
    tradeMap.set(bestIdx, arr);
  }

  const data = klines.map((k, i) => {
    const trades = tradeMap.get(i);
    const buys = trades?.filter((t) => t.side === "buy") || [];
    const sells = trades?.filter((t) => t.side === "sell") || [];
    const avgBuy = buys.length ? buys.reduce((s, t) => s + t.price, 0) / buys.length : undefined;
    const avgSell =
      sells.length ? sells.reduce((s, t) => s + t.price, 0) / sells.length : undefined;
    return {
      zeit: new Date(k.t).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }),
      preis: k.c,
      hoch: k.h,
      tief: k.l,
      buyMarker: avgBuy,
      sellMarker: avgSell,
      _buyCount: buys.length,
      _sellCount: sells.length,
      _trades: trades || undefined,
    };
  });

  const allPrices = klines.flatMap((k) => [k.h, k.l]);
  const orderPrices = (orders || []).map((o) => o.price);
  const allVals = [...allPrices, ...orderPrices];
  const mn = Math.min(...allVals);
  const mx = Math.max(...allVals);
  const pad = (mx - mn) * 0.05;
  const last = klines[klines.length - 1]?.c || 0;
  const first = klines[0]?.c || last;
  const up = last >= first;
  const col = up ? "var(--up)" : "var(--down)";
  const chg = first > 0 ? ((last - first) / first) * 100 : 0;

  const buyCount = visibleTrades.filter((t) => t.side === "buy").length;
  const sellCount = visibleTrades.filter((t) => t.side === "sell").length;
  const tradePnl = visibleTrades.reduce((s, t) => s + (t.pnl || 0), 0);

  const uid = pair.replace(/\W/g, "");
  const gId = `prG-${uid}`;
  const gbId = `glB-${uid}`;
  const gsId = `glS-${uid}`;

  return (
    <div className="card p-4 sm:p-5 h-full">
      <div className="flex items-center justify-between mb-0.5">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold">
          {pair}
        </h3>
        <div className="flex items-center gap-0.5">
          {intervals.map((iv) => (
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
      <div className="flex items-baseline gap-2 mb-3">
        <span className="text-xl font-bold font-mono">{fmt(last)}</span>
        <span className="text-[10px] text-[var(--text-tertiary)]">{quote}</span>
        <span className="text-[11px] font-mono font-semibold ml-1" style={{ color: col }}>
          {chg >= 0 ? "+" : ""}
          {chg.toFixed(2)}%
        </span>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={data} margin={{ top: 4, right: 0, left: -15, bottom: 0 }}>
          <defs>
            <linearGradient id={gId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={col} stopOpacity={0.12} />
              <stop offset="100%" stopColor={col} stopOpacity={0} />
            </linearGradient>
            <filter id={gbId} x="-50%" y="-50%" width="200%" height="200%">
              <feDropShadow
                dx="0"
                dy="0"
                stdDeviation="1.2"
                floodColor="#10b981"
                floodOpacity="0.5"
              />
            </filter>
            <filter id={gsId} x="-50%" y="-50%" width="200%" height="200%">
              <feDropShadow
                dx="0"
                dy="0"
                stdDeviation="1.2"
                floodColor="#ef4444"
                floodOpacity="0.5"
              />
            </filter>
          </defs>
          <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="zeit"
            tick={{ fill: "var(--text-quaternary)", fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fill: "var(--text-quaternary)", fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            domain={[mn - pad, mx + pad]}
            width={55}
            tickFormatter={(v) => fmt(v, 0)}
          />
          <Tooltip
            contentStyle={{
              background: "var(--bg-elevated)",
              border: "1px solid var(--border-accent)",
              borderRadius: 12,
              padding: "8px 12px",
              fontSize: 11,
              boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
            }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const entry = payload[0]?.payload;
              const tdList = entry?._trades as Trade[] | undefined;
              return (
                <div
                  style={{
                    background: "var(--bg-elevated)",
                    border: "1px solid var(--border-accent)",
                    borderRadius: 12,
                    padding: "8px 12px",
                    fontSize: 11,
                    boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
                    minWidth: 160,
                  }}
                >
                  <div
                    style={{
                      color: "var(--text-tertiary)",
                      marginBottom: 4,
                      fontSize: 10,
                      fontWeight: 500,
                    }}
                  >
                    {entry?.zeit}
                  </div>
                  <div
                    style={{
                      fontFamily: "JetBrains Mono, monospace",
                      fontSize: 13,
                      fontWeight: 700,
                      color: col,
                    }}
                  >
                    {fmt(entry?.preis || 0)} {quote}
                  </div>
                  {entry?.hoch && (
                    <div
                      style={{
                        display: "flex",
                        gap: 10,
                        marginTop: 4,
                        fontSize: 9,
                        color: "var(--text-quaternary)",
                      }}
                    >
                      <span>H: {fmt(entry.hoch)}</span>
                      <span>T: {fmt(entry.tief)}</span>
                    </div>
                  )}
                  {tdList &&
                    tdList.map((t, i) => (
                      <div
                        key={i}
                        style={{
                          marginTop: 8,
                          paddingTop: 8,
                          borderTop: "1px solid var(--border-subtle)",
                        }}
                      >
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                          <span
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 3,
                              padding: "2px 6px",
                              borderRadius: 4,
                              fontSize: 9,
                              fontWeight: 700,
                              letterSpacing: "0.04em",
                              background:
                                t.side === "buy"
                                  ? "rgba(16,185,129,0.15)"
                                  : "rgba(239,68,68,0.15)",
                              color: t.side === "buy" ? "#34d399" : "#fca5a5",
                            }}
                          >
                            <span style={{ fontSize: 11 }}>
                              {t.side === "buy" ? "▲" : "▼"}
                            </span>
                            {t.side === "buy" ? "KAUF" : "VERKAUF"}
                          </span>
                          <span
                            style={{
                              fontFamily: "JetBrains Mono, monospace",
                              fontSize: 11,
                              fontWeight: 600,
                            }}
                          >
                            {fmt(t.price)}
                          </span>
                        </div>
                        <div
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                            marginTop: 4,
                            fontSize: 10,
                          }}
                        >
                          <span style={{ color: "var(--text-quaternary)" }}>
                            {fmtAmount(t.amount, pair.split("/")[0])}
                          </span>
                          <span
                            style={{
                              fontFamily: "JetBrains Mono, monospace",
                              fontWeight: 700,
                              fontSize: 11,
                              padding: "1px 5px",
                              borderRadius: 4,
                              background:
                                t.pnl >= 0 ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.1)",
                              color: t.pnl >= 0 ? "#34d399" : "#fca5a5",
                            }}
                          >
                            {t.pnl >= 0 ? "+" : ""}
                            {t.pnl.toFixed(4)} {quote}
                          </span>
                        </div>
                      </div>
                    ))}
                </div>
              );
            }}
          />
          {(orders || []).map((o) => {
            const isPartial = o.status === "partially_filled";
            const color = isPartial ? "#f59e0b" : o.side === "buy" ? "#22c55e" : "#ef4444";
            const label = isPartial
              ? `${o.side === "buy" ? "K" : "V"} ${fmt(o.price, 0)} (${o.fill_pct ?? 0}%)`
              : `${o.side === "buy" ? "K" : "V"} ${fmt(o.price, 0)}`;
            return (
              <ReferenceLine
                key={o.id}
                y={o.price}
                stroke={color}
                strokeDasharray={isPartial ? "6 3" : "3 4"}
                strokeOpacity={isPartial ? 0.55 : 0.25}
                strokeWidth={isPartial ? 1.5 : 1}
                label={{
                  value: label,
                  fill: color,
                  fontSize: 7,
                  position: o.side === "buy" ? "left" : "right",
                  offset: 4,
                }}
              />
            );
          })}
          <Area
            type="monotone"
            dataKey="preis"
            stroke={col}
            strokeWidth={1.5}
            fill={`url(#${gId})`}
            dot={false}
            activeDot={{ r: 3, fill: col, strokeWidth: 0 }}
          />
          <Scatter
            dataKey="buyMarker"
            fill="#10b981"
            isAnimationActive={false}
            shape={(props: { cx?: number; cy?: number; payload?: Record<string, unknown> }) => {
              if (!props.cx || !props.cy) return <></>;
              const cnt = (props.payload?._buyCount as number) || 1;
              const r = cnt > 1 ? 7 : 4.5;
              return (
                <g filter={`url(#${gbId})`} style={{ cursor: "pointer" }}>
                  <circle
                    cx={props.cx}
                    cy={props.cy}
                    r={r}
                    fill="#10b981"
                    stroke="#065f46"
                    strokeWidth={1}
                  />
                  {cnt === 1 ? (
                    <polygon
                      points={`${props.cx - 2},${props.cy + 0.8} ${props.cx},${props.cy - 2} ${props.cx + 2},${props.cy + 0.8}`}
                      fill="#fff"
                      fillOpacity={0.85}
                    />
                  ) : (
                    <text
                      x={props.cx}
                      y={props.cy + 3}
                      textAnchor="middle"
                      fill="#fff"
                      fontSize={8}
                      fontWeight={700}
                      style={{ fontFamily: "JetBrains Mono, monospace" }}
                    >
                      {cnt}
                    </text>
                  )}
                </g>
              );
            }}
          />
          <Scatter
            dataKey="sellMarker"
            fill="#ef4444"
            isAnimationActive={false}
            shape={(props: { cx?: number; cy?: number; payload?: Record<string, unknown> }) => {
              if (!props.cx || !props.cy) return <></>;
              const cnt = (props.payload?._sellCount as number) || 1;
              const r = cnt > 1 ? 7 : 4.5;
              return (
                <g filter={`url(#${gsId})`} style={{ cursor: "pointer" }}>
                  <circle
                    cx={props.cx}
                    cy={props.cy}
                    r={r}
                    fill="#ef4444"
                    stroke="#7f1d1d"
                    strokeWidth={1}
                  />
                  {cnt === 1 ? (
                    <polygon
                      points={`${props.cx - 2},${props.cy - 0.8} ${props.cx},${props.cy + 2} ${props.cx + 2},${props.cy - 0.8}`}
                      fill="#fff"
                      fillOpacity={0.85}
                    />
                  ) : (
                    <text
                      x={props.cx}
                      y={props.cy + 3}
                      textAnchor="middle"
                      fill="#fff"
                      fontSize={8}
                      fontWeight={700}
                      style={{ fontFamily: "JetBrains Mono, monospace" }}
                    >
                      {cnt}
                    </text>
                  )}
                </g>
              );
            }}
          />
        </ComposedChart>
      </ResponsiveContainer>
      {/* Footer: Grid info + Trade summary */}
      <div
        className="mt-2.5 pt-2 border-t flex flex-col gap-1.5"
        style={{ borderColor: "var(--border-subtle)" }}
      >
        {gridMeta && (orders || []).length > 0 && (
          <>
            <div className="flex items-center gap-2 text-[9px]">
              <span
                className="text-[var(--text-quaternary)] uppercase tracking-wider font-semibold"
                style={{ fontSize: 8 }}
              >
                Grid
              </span>
              <div className="flex items-center gap-1">
                <span
                  className="inline-block w-2.5 h-[3px] rounded-full"
                  style={{ background: "var(--up)" }}
                />
                <span className="text-[var(--text-quaternary)]">
                  {gridMeta.buyCount ?? 0}K
                </span>
              </div>
              <div className="flex items-center gap-1">
                <span
                  className="inline-block w-2.5 h-[3px] rounded-full"
                  style={{ background: "var(--down)" }}
                />
                <span className="text-[var(--text-quaternary)]">
                  {gridMeta.sellCount ?? 0}V
                </span>
              </div>
              <span className="text-[var(--text-quaternary)] font-mono">
                = {(orders || []).length}/{gridMeta.configured || gridMeta.levels}
              </span>
              {(orders || []).some((o) => o.status === "partially_filled") && (
                <span className="text-[#f59e0b] font-mono">
                  {(orders || []).filter((o) => o.status === "partially_filled").length} partial
                </span>
              )}
              <span className="text-[var(--text-quaternary)] ml-auto font-mono">
                {gridMeta.range}
              </span>
            </div>
            {(orders || []).filter((o) => o.status === "partially_filled").length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-0.5">
                {(orders || [])
                  .filter((o) => o.status === "partially_filled")
                  .map((o) => (
                    <div
                      key={o.id}
                      className="flex items-center gap-1 px-1.5 py-0.5 rounded"
                      style={{
                        background: "rgba(245,158,11,0.08)",
                        border: "1px solid rgba(245,158,11,0.2)",
                      }}
                    >
                      <span
                        className="text-[8px] font-bold"
                        style={{
                          color: o.side === "buy" ? "var(--up)" : "var(--down)",
                        }}
                      >
                        {o.side === "buy" ? "K" : "V"}
                      </span>
                      <span className="text-[8px] font-mono text-[var(--text-tertiary)]">
                        {fmt(o.price, 0)}
                      </span>
                      <div
                        className="w-8 h-1.5 rounded-full overflow-hidden"
                        style={{ background: "rgba(245,158,11,0.15)" }}
                      >
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${o.fill_pct ?? 0}%`,
                            background: "#f59e0b",
                            transition: "width 0.5s",
                          }}
                        />
                      </div>
                      <span className="text-[7px] font-mono text-[#f59e0b]">
                        {(o.fill_pct ?? 0).toFixed(0)}%
                      </span>
                    </div>
                  ))}
              </div>
            )}
          </>
        )}
        {gridMeta?.issue && (
          <div
            className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[8px]"
            style={{
              background: "var(--warn-bg)",
              color: "var(--warn)",
              border: "1px solid color-mix(in srgb, var(--warn) 15%, transparent)",
            }}
          >
            <span className="font-bold shrink-0">
              {gridMeta.unplaced || "?"} blockiert
            </span>
            <span className="truncate">
              {gridMeta.issue.length > 60 ? gridMeta.issue.slice(0, 57) + "..." : gridMeta.issue}
            </span>
          </div>
        )}
        {visibleTrades.length > 0 && (
          <div className="flex items-center gap-3 text-[9px]">
            <span
              className="text-[var(--text-quaternary)] uppercase tracking-wider font-semibold"
              style={{ fontSize: 8 }}
            >
              Trades
            </span>
            <div className="flex items-center gap-1.5">
              <span
                className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full"
                style={{ background: "rgba(16,185,129,0.15)" }}
              >
                <span style={{ color: "#34d399", fontSize: 7, lineHeight: 1 }}>▲</span>
              </span>
              <span className="text-[var(--text-quaternary)] font-mono">{buyCount}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span
                className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full"
                style={{ background: "rgba(239,68,68,0.15)" }}
              >
                <span style={{ color: "#fca5a5", fontSize: 7, lineHeight: 1 }}>▼</span>
              </span>
              <span className="text-[var(--text-quaternary)] font-mono">{sellCount}</span>
            </div>
            <span
              className="text-[10px] font-mono font-bold px-1.5 py-0.5 rounded ml-auto"
              style={{
                background:
                  tradePnl >= 0 ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.1)",
                color: tradePnl >= 0 ? "#34d399" : "#fca5a5",
              }}
            >
              {tradePnl >= 0 ? "+" : ""}
              {tradePnl.toFixed(4)} {quote}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

export function PriceChart(props: PriceChartProps) {
  return (
    <ErrorBoundary>
      <PriceChartBase {...props} />
    </ErrorBoundary>
  );
}
