"use client";

import type { PairMetrics, BotEvent, RLStats } from "@/lib/types";
import { zeitAgo } from "@/lib/format";
import { REGIME_STYLE } from "@/lib/constants";
import { ErrorBoundary } from "./ErrorBoundary";

export interface OptimizationPanelOptData {
  optimizations: BotEvent[];
  regimes: BotEvent[];
  pairRegimes: Record<
    string,
    {
      regime: PairMetrics["regime"];
      allocation: PairMetrics["allocation"];
      trailing_tp_count: number;
      trailing_tp_active: boolean;
    }
  >;
}

export interface OptimizationPanelProps {
  optData: OptimizationPanelOptData;
  pairs: [string, PairMetrics][];
  rlStats: RLStats | null;
  onCommand: (t: string) => void;
}

function OptimizationPanelBase({
  optData,
  pairs,
  rlStats,
  onCommand,
}: OptimizationPanelProps) {
  const lastOpt = optData.optimizations[0];

  const rlRewards = rlStats?.rewards ?? [];
  const last30 = rlRewards.slice(-30);
  const rewardTrend =
    last30.length >= 2 ? last30[last30.length - 1].reward - last30[0].reward : 0;
  const avgReward =
    last30.length > 0
      ? last30.reduce((s, r) => s + r.reward, 0) / last30.length
      : 0;
  const rewardMin = last30.length > 0 ? Math.min(...last30.map((r) => r.reward)) : 0;
  const rewardMax = last30.length > 0 ? Math.max(...last30.map((r) => r.reward)) : 1;
  const rewardRange = Math.max(rewardMax - rewardMin, 0.01);

  const explorationPct = (rlStats?.explorationRate ?? 0.15) * 100;
  const explorationProgress = Math.max(
    0,
    Math.min(100, ((0.15 - (rlStats?.explorationRate ?? 0.15)) / (0.15 - 0.03)) * 100)
  );

  const la = rlStats?.latestAction;
  const laAction = la?.action as {
    spacing_delta?: number;
    size_delta?: number;
    range_delta?: number;
    distance_delta?: number;
    was_exploration?: boolean;
  } | null;

  const fmtDelta = (v: number | undefined) => {
    if (v === undefined || v === 0) return "0%";
    return `${v > 0 ? "+" : ""}${(v * 100).toFixed(0)}%`;
  };

  return (
    <div className="card p-4 sm:p-5 fade-in">
      <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
        Selbst-Optimierung
      </h3>

      {pairs.length > 1 && (
        <div className="mb-4">
          <div className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider mb-1.5 font-semibold">
            Kapital-Allokation
          </div>
          <div
            className="flex gap-1.5 h-5 rounded-md overflow-hidden"
            style={{ background: "var(--bg-secondary)" }}
          >
            {pairs.map(([p, m], i) => {
              const eq = m.allocation?.equity ?? m.current_equity ?? 0;
              const totalEq = pairs.reduce(
                (s, [, pm]) => s + (pm.allocation?.equity ?? pm.current_equity ?? 0),
                0
              );
              const pct = totalEq > 0 ? (eq / totalEq) * 100 : 100 / pairs.length;
              const colors = ["#3b82f6", "var(--up)", "var(--warn)", "var(--accent)"];
              return (
                <div
                  key={p}
                  className="flex items-center justify-center text-[8px] font-bold text-white transition-all"
                  style={{
                    width: `${pct}%`,
                    background: colors[i % colors.length],
                    minWidth: 30,
                  }}
                >
                  {p.split("/")[0]} {pct.toFixed(0)}%
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-4">
        {pairs.map(([p, m]) => {
          const regimeKey = m.regime?.regime || "ranging";
          const rs = REGIME_STYLE[regimeKey] || REGIME_STYLE.ranging;
          const rsi = m.regime?.rsi ?? 50;
          const ef = optData.pairRegimes[p];
          const ttp = ef?.trailing_tp_active;
          const allowBuys =
            regimeKey === "ranging" ||
            regimeKey === "trend_up" ||
            (regimeKey === "trend_down" && rsi < 40) ||
            (regimeKey === "volatile" && rsi < 30);
          const allowSells =
            regimeKey === "ranging" ||
            regimeKey === "trend_down" ||
            (regimeKey === "trend_up" && rsi > 60) ||
            (regimeKey === "volatile" && rsi > 70);

          const sentScore = m.regime?.sentiment_score ?? 0;
          const sentConf = m.regime?.sentiment_confidence ?? 0;
          const sentIcon =
            sentScore > 0.3 ? "\u25B2" : sentScore < -0.3 ? "\u25BC" : "\u2500";
          const sentColor =
            sentScore > 0.3 ? "var(--up)" : sentScore < -0.3 ? "var(--down)" : "var(--text-tertiary)";

          const conf = m.regime?.confidence ?? 0;
          const tp = m.regime?.transition_pending;
          const tpCount = m.regime?.transition_countdown ?? 0;

          return (
            <div
              key={p}
              className="rounded-lg p-3"
              style={{ background: "var(--bg-secondary)" }}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-[11px] font-bold">{p}</span>
                <div className="flex items-center gap-1.5">
                  <span
                    className="inline-flex px-1.5 py-0.5 rounded text-[8px] font-bold"
                    style={{ background: rs.bg, color: rs.color }}
                  >
                    {rs.label}
                    {conf > 0 && (
                      <span style={{ opacity: 0.8, marginLeft: 3, fontSize: "7px" }}>
                        ({conf.toFixed(0)}%)
                      </span>
                    )}
                  </span>
                  {tp && (
                    <span
                      className="text-[7px] px-1 py-px rounded font-semibold"
                      style={{
                        background: "var(--bg-elevated)",
                        color: "var(--text-tertiary)",
                      }}
                    >
                      {"\u2192"} {tp} ({tpCount})
                    </span>
                  )}
                  <span
                    title={`News: ${sentScore > 0 ? "+" : ""}${sentScore.toFixed(2)} (Conf ${(sentConf * 100).toFixed(0)}%)`}
                    className="text-[10px] font-bold cursor-default"
                    style={{ color: sentColor }}
                  >
                    {sentIcon}
                  </span>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-1 text-[9px] mb-2">
                <div className="text-center">
                  <span className="text-[var(--text-quaternary)]">RSI</span>{" "}
                  <span
                    className="font-mono font-bold"
                    style={{
                      color:
                        rsi > 70 ? "var(--down)" : rsi < 30 ? "var(--up)" : "var(--text-secondary)",
                    }}
                  >
                    {rsi.toFixed(0)}
                  </span>
                </div>
                <div className="text-center">
                  <span className="text-[var(--text-quaternary)]">ADX</span>{" "}
                  <span className="font-mono font-bold">
                    {(m.regime?.adx ?? 0).toFixed(0)}
                  </span>
                </div>
                <div className="text-center">
                  <span className="text-[var(--text-quaternary)]">BollW</span>{" "}
                  <span className="font-mono font-bold">
                    {(m.regime?.boll_width ?? 0).toFixed(3)}
                  </span>
                </div>
              </div>
              <div className="space-y-1 mb-2">
                {[
                  { label: "Trend", val: m.regime?.trend_score ?? 0, bipolar: true },
                  { label: "Volatil.", val: m.regime?.volatility_score ?? 0, bipolar: false },
                  { label: "Ranging", val: m.regime?.ranging_score ?? 0, bipolar: false },
                ].map(({ label, val, bipolar }) => (
                  <div key={label} className="flex items-center gap-1.5 text-[8px]">
                    <span className="w-10 text-right text-[var(--text-quaternary)] shrink-0">
                      {label}
                    </span>
                    <div
                      className="flex-1 h-1.5 rounded-full overflow-hidden relative"
                      style={{ background: "var(--bg-tertiary)" }}
                    >
                      {bipolar ? (
                        <>
                          <div
                            className="absolute top-0 bottom-0 left-1/2 w-px"
                            style={{ background: "var(--border-subtle)" }}
                          />
                          <div
                            className="absolute top-0 bottom-0 rounded-full transition-all duration-500"
                            style={{
                              background: val >= 0 ? "var(--up)" : "var(--down)",
                              left: val >= 0 ? "50%" : `${50 + val * 50}%`,
                              width: `${Math.abs(val) * 50}%`,
                            }}
                          />
                        </>
                      ) : (
                        <div
                          className="absolute top-0 bottom-0 left-0 rounded-full transition-all duration-500"
                          style={{
                            width: `${val * 100}%`,
                            background: val > 0.7 ? "var(--warn)" : "var(--accent)",
                          }}
                        />
                      )}
                    </div>
                    <span className="w-8 text-right font-mono text-[var(--text-tertiary)] shrink-0">
                      {bipolar
                        ? (val > 0 ? "+" : "") + val.toFixed(2)
                        : (val * 100).toFixed(0) + "%"}
                    </span>
                  </div>
                ))}
              </div>
              <div className="flex flex-wrap gap-1">
                <span
                  className="px-1.5 py-0.5 rounded text-[8px] font-semibold"
                  style={{
                    background: allowBuys ? "var(--up-bg)" : "var(--down-bg)",
                    color: allowBuys ? "var(--up)" : "var(--down)",
                  }}
                >
                  Buys {allowBuys ? "\u2713" : "\u2717"}
                </span>
                <span
                  className="px-1.5 py-0.5 rounded text-[8px] font-semibold"
                  style={{
                    background: allowSells ? "var(--up-bg)" : "var(--down-bg)",
                    color: allowSells ? "var(--up)" : "var(--down)",
                  }}
                >
                  Sells {allowSells ? "\u2713" : "\u2717"}
                </span>
                {ttp !== undefined && (
                  <span
                    className="px-1.5 py-0.5 rounded text-[8px] font-semibold"
                    style={{
                      background: ttp ? "rgba(59,130,246,0.12)" : "var(--bg-elevated)",
                      color: ttp ? "#3b82f6" : "var(--text-tertiary)",
                    }}
                  >
                    Trail-TP {ttp ? "AN" : "AUS"}
                    {ef?.trailing_tp_count ? ` (${ef.trailing_tp_count})` : ""}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {lastOpt && (
        <div
          className="rounded-lg p-2.5 mb-3"
          style={{ background: "var(--bg-secondary)" }}
        >
          <div className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider mb-1 font-semibold">
            Letzte Anpassung
          </div>
          <div className="text-[11px] text-[var(--text-secondary)]">{lastOpt.message}</div>
          <div className="text-[9px] text-[var(--text-tertiary)] mt-0.5 font-mono">
            {new Date(lastOpt.timestamp).toLocaleString("de-DE", {
              day: "2-digit",
              month: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </div>
        </div>
      )}
      {optData.regimes.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-4">
          <span className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-semibold self-center">
            Regime-Verlauf:
          </span>
          {optData.regimes.slice(0, 5).map((ev) => {
            const d = ev.detail as Record<string, string> | null;
            const newR = d?.new || "ranging";
            const s = REGIME_STYLE[newR] || REGIME_STYLE.ranging;
            return (
              <span
                key={ev.id}
                className="inline-flex px-1.5 py-0.5 rounded text-[8px] font-bold"
                style={{ background: s.bg, color: s.color }}
              >
                {d?.pair?.split("/")[0]} {"\u2192"} {s.label}
                <span className="ml-1 font-normal opacity-60">
                  {new Date(ev.timestamp).toLocaleString("de-DE", {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </span>
            );
          })}
        </div>
      )}

      {rlStats && rlStats.episodes > 0 && (
        <div
          className="rounded-lg p-3 mt-1"
          style={{ background: "var(--bg-secondary)" }}
        >
          <div className="flex items-center justify-between mb-2.5">
            <div className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-semibold">
              Lern-Fortschritt
            </div>
            <span
              className="text-[8px] font-mono px-1.5 py-0.5 rounded font-bold"
              style={{
                background:
                  rewardTrend >= 0 ? "var(--up-bg)" : "var(--down-bg)",
                color: rewardTrend >= 0 ? "var(--up)" : "var(--down)",
              }}
            >
              {rewardTrend >= 0 ? "\u2191" : "\u2193"} {avgReward >= 0 ? "+" : ""}
              {avgReward.toFixed(2)} avg
            </span>
          </div>

          {last30.length >= 2 && (
            <div className="mb-2.5">
              <div className="text-[8px] text-[var(--text-quaternary)] mb-1">
                Reward (letzte {last30.length} Episoden)
              </div>
              <svg
                width="100%"
                height="36"
                viewBox={`0 0 ${last30.length - 1} 36`}
                preserveAspectRatio="none"
                style={{
                  display: "block",
                  borderRadius: 4,
                  background: "var(--bg-primary)",
                }}
              >
                <defs>
                  <linearGradient id="rlSparkGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop
                      offset="0%"
                      stopColor={rewardTrend >= 0 ? "#10b981" : "#ef4444"}
                      stopOpacity={0.3}
                    />
                    <stop
                      offset="100%"
                      stopColor={rewardTrend >= 0 ? "#10b981" : "#ef4444"}
                      stopOpacity={0.02}
                    />
                  </linearGradient>
                </defs>
                <path
                  d={
                    last30
                      .map((r, i) => {
                        const x = i;
                        const y = 34 - ((r.reward - rewardMin) / rewardRange) * 30;
                        return `${i === 0 ? "M" : "L"}${x},${y}`;
                      })
                      .join(" ") + ` L${last30.length - 1},36 L0,36 Z`
                  }
                  fill="url(#rlSparkGrad)"
                />
                <path
                  d={last30
                    .map((r, i) => {
                      const x = i;
                      const y = 34 - ((r.reward - rewardMin) / rewardRange) * 30;
                      return `${i === 0 ? "M" : "L"}${x},${y}`;
                    })
                    .join(" ")}
                  fill="none"
                  stroke={rewardTrend >= 0 ? "#10b981" : "#ef4444"}
                  strokeWidth="1.5"
                  vectorEffect="non-scaling-stroke"
                />
              </svg>
            </div>
          )}

          <div className="mb-2.5">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[8px] text-[var(--text-quaternary)]">
                Episode {rlStats.episodes} — {explorationPct.toFixed(1)}% Erkundung
              </span>
              <span
                className="text-[8px] font-mono"
                style={{ color: explorationPct > 10 ? "var(--warn)" : "var(--up)" }}
              >
                {explorationPct > 10 ? "Lernt" : "Nutzt"}
              </span>
            </div>
            <div
              className="h-1.5 rounded-full overflow-hidden"
              style={{ background: "var(--bg-primary)" }}
            >
              <div
                className="h-full rounded-full transition-all"
                style={{
                  width: `${explorationProgress}%`,
                  background: "linear-gradient(90deg, var(--warn), var(--up))",
                }}
              />
            </div>
            <div className="flex justify-between text-[7px] text-[var(--text-quaternary)] mt-0.5">
              <span>15% Exploration</span>
              <span>3% Exploitation</span>
            </div>
          </div>

          {la && laAction && (
            <div className="mb-2.5 p-2 rounded" style={{ background: "var(--bg-primary)" }}>
              <div className="flex items-center gap-1.5 mb-1">
                <span className="text-[8px] text-[var(--text-quaternary)]">
                  Letzte Aktion:
                </span>
                <span
                  className="text-[7px] font-bold px-1 py-0.5 rounded"
                  style={{
                    background: la.was_exploration
                      ? "rgba(234,179,8,0.12)"
                      : "var(--up-bg)",
                    color: la.was_exploration ? "#eab308" : "var(--up)",
                  }}
                >
                  {la.was_exploration ? "EXPLORATION" : "EXPLOITATION"}
                </span>
              </div>
              <div className="text-[9px] font-mono text-[var(--text-secondary)]">
                Spacing {fmtDelta(laAction.spacing_delta)}, Size {fmtDelta(laAction.size_delta)},
                Range {fmtDelta(laAction.range_delta)}
              </div>
              <div className="flex items-center gap-2 mt-1">
                <span
                  className="text-[9px] font-mono"
                  style={{
                    color: la.reward >= 0 ? "var(--up)" : "var(--down)",
                  }}
                >
                  Reward: {la.reward >= 0 ? "+" : ""}
                  {la.reward.toFixed(3)}
                </span>
                <span className="text-[8px] text-[var(--text-quaternary)]">
                  {zeitAgo(la.timestamp)}
                </span>
              </div>
            </div>
          )}

          <div className="flex justify-end">
            <button
              onClick={() => {
                if (confirm("RL-Policy wirklich zurücksetzen?")) onCommand("reset_rl");
              }}
              className="px-2 py-1 rounded text-[8px] font-semibold transition-all active:scale-95"
              style={{
                background: "var(--down-bg)",
                color: "var(--down)",
                border: "1px solid color-mix(in srgb, var(--down) 15%, transparent)",
              }}
            >
              Policy zurücksetzen
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function OptimizationPanel(props: OptimizationPanelProps) {
  return (
    <ErrorBoundary>
      <OptimizationPanelBase {...props} />
    </ErrorBoundary>
  );
}
