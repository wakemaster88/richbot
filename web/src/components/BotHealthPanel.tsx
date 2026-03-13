"use client";

import type { BotStatus, PairMetrics, PiStatus, RLStats } from "@/lib/types";
import { laufzeit } from "@/lib/format";
import { ErrorBoundary } from "./ErrorBoundary";

export interface BotHealthPanelProps {
  pi: PiStatus | null;
  status: BotStatus;
  rlStats: RLStats | null;
  sentimentEnabled: boolean;
}

function BotHealthPanelBase({
  pi,
  status,
  rlStats,
  sentimentEnabled,
}: BotHealthPanelProps) {
  const sys = pi?.system;
  const pairs = Object.entries(status.pairStatuses || {}).filter(
    ([k]) => !k.startsWith("__")
  );
  const sentSource =
    pairs.length > 0
      ? (pairs[0][1] as PairMetrics)?.regime?.sentiment_confidence !== undefined
        ? "aktiv"
        : "—"
      : "—";
  const rlEp = rlStats?.episodes ?? 0;
  const rlExpl = rlStats?.explorationRate ?? 0;
  const rlAvg = rlStats?.rewards?.length
    ? rlStats.rewards
        .slice(-20)
        .reduce((s, r) => s + r.reward, 0) /
      Math.min(rlStats.rewards.length, 20)
    : 0;

  return (
    <div className="card p-4 sm:p-5 fade-in">
      <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
        Bot-Gesundheit
      </h3>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-2">
        <div className="card-inner px-3 py-2 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">Uptime</p>
          <p className="text-[13px] font-bold font-mono mt-0.5">
            {laufzeit(status.startedAt)}
          </p>
        </div>
        <div className="card-inner px-3 py-2 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">Memory</p>
          <p
            className="text-[13px] font-bold font-mono mt-0.5"
            style={{
              color:
                sys?.ram_percent && sys.ram_percent > 80
                  ? "var(--down)"
                  : sys?.ram_percent && sys.ram_percent > 60
                    ? "var(--warn)"
                    : "var(--text-primary)",
            }}
          >
            {sys?.rss_kb
              ? `${Math.round(sys.rss_kb / 1024)} MB`
              : sys?.ram_used_mb
                ? `${sys.ram_used_mb} MB`
                : "\u2014"}
          </p>
        </div>
        <div className="card-inner px-3 py-2 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">CPU / Temp</p>
          <p
            className="text-[13px] font-bold font-mono mt-0.5"
            style={{
              color:
                sys?.cpu_temp && sys.cpu_temp > 75
                  ? "var(--down)"
                  : sys?.cpu_temp && sys.cpu_temp > 60
                    ? "var(--warn)"
                    : "var(--text-primary)",
            }}
          >
            {sys?.cpu_percent != null ? `${sys.cpu_percent}%` : "\u2014"}
            {sys?.cpu_temp != null ? ` / ${sys.cpu_temp}\u00B0C` : ""}
          </p>
        </div>
        <div className="card-inner px-3 py-2 text-center">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">Version</p>
          <p className="text-[13px] font-bold font-mono mt-0.5">
            {status.version && status.version.length >= 7
              ? status.version.slice(0, 7)
              : status.version || "\u2014"}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div className="card-inner px-3 py-2">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">Sentiment</p>
          <p
            className="text-[11px] font-bold font-mono mt-0.5"
            style={{
              color:
                sentimentEnabled && sentSource === "aktiv"
                  ? "var(--up)"
                  : "var(--text-tertiary)",
            }}
          >
            {sentimentEnabled ? `${sentSource} (local)` : "aus"}
          </p>
        </div>
        <div className="card-inner px-3 py-2">
          <p className="text-[8px] text-[var(--text-quaternary)] uppercase">RL</p>
          {rlEp > 0 ? (
            <div>
              <p className="text-[11px] font-bold font-mono mt-0.5">
                Ep {rlEp}, {(rlExpl * 100).toFixed(1)}%
              </p>
              <p
                className="text-[8px] font-mono mt-0.5"
                style={{ color: rlAvg >= 0 ? "var(--up)" : "var(--down)" }}
              >
                Avg: {rlAvg >= 0 ? "+" : ""}
                {rlAvg.toFixed(2)}
              </p>
            </div>
          ) : (
            <p className="text-[11px] font-bold font-mono mt-0.5 text-[var(--text-tertiary)]">
              {"\u2014"}
            </p>
          )}
        </div>
      </div>

      {sys?.public_ip && (
        <div className="mt-2 flex items-center gap-2 text-[9px] text-[var(--text-quaternary)]">
          <span>
            IP:{" "}
            <span className="font-mono text-[var(--text-tertiary)] select-all">
              {sys.public_ip}
            </span>
          </span>
          {sys.disk_percent != null && (
            <span className="ml-auto">Disk: {sys.disk_percent}%</span>
          )}
        </div>
      )}
    </div>
  );
}

export function BotHealthPanel(props: BotHealthPanelProps) {
  return (
    <ErrorBoundary>
      <BotHealthPanelBase {...props} />
    </ErrorBoundary>
  );
}
