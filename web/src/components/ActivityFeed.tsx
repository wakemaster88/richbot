"use client";

import { useState } from "react";
import type { BotEvent } from "@/lib/types";
import { EVT_ICONS, EVT_COLORS } from "@/lib/constants";
import { ErrorBoundary } from "./ErrorBoundary";

export interface ActivityFeedProps {
  events: BotEvent[];
}

function ActivityFeedBase({ events }: ActivityFeedProps) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? events : events.slice(0, 12);

  if (!events.length) return null;

  return (
    <div className="card p-4 sm:p-5 h-full">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold">
          Aktivitaet
        </h3>
        <span className="text-[9px] text-[var(--text-quaternary)]">{events.length}</span>
      </div>
      <div className="space-y-0.5 max-h-80 overflow-y-auto">
        {shown.map((ev) => {
          const levelColor =
            ev.level === "critical"
              ? "critical"
              : ev.level === "warn"
                ? "warn"
                : ev.level === "error"
                  ? "error"
                  : ev.level === "success"
                    ? "success"
                    : "";
          const col =
            EVT_COLORS[(levelColor || ev.category) as keyof typeof EVT_COLORS] ||
            "var(--text-tertiary)";
          const icon = EVT_ICONS[ev.category as keyof typeof EVT_ICONS] || "\u00B7";
          const isCritical = ev.level === "critical";
          const zeit = new Date(ev.timestamp).toLocaleTimeString("de-DE", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          });
          return (
            <div
              key={ev.id}
              className="flex items-start gap-2 text-[10px] py-1.5 px-2 rounded hover:bg-[var(--bg-secondary)] transition-colors"
              style={
                isCritical
                  ? {
                      background: "rgba(239,68,68,0.08)",
                      border: "1px solid rgba(239,68,68,0.2)",
                    }
                  : undefined
              }
            >
              <span
                className="shrink-0 w-4 h-4 rounded flex items-center justify-center text-[8px] font-bold mt-0.5"
                style={{
                  background: `color-mix(in srgb, ${col} 15%, transparent)`,
                  color: col,
                }}
              >
                {icon}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[var(--text-primary)] leading-snug">{ev.message}</p>
                {ev.detail && (
                  <p className="text-[9px] text-[var(--text-quaternary)] mt-0.5 font-mono truncate">
                    {Object.entries(ev.detail as Record<string, unknown>)
                      .filter(([k]) => !["pair"].includes(k))
                      .slice(0, 4)
                      .map(([k, v]) =>
                        `${k}: ${typeof v === "number" ? (Number.isInteger(v) ? v : (v as number).toFixed(4)) : v}`
                      )
                      .join(" \u00B7 ")}
                  </p>
                )}
              </div>
              <span className="shrink-0 text-[9px] text-[var(--text-quaternary)] font-mono mt-0.5">
                {zeit}
              </span>
            </div>
          );
        })}
      </div>
      {events.length > 12 && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-2 text-[10px] text-[var(--accent)] hover:underline w-full text-center"
        >
          {expanded ? "Weniger anzeigen" : `Alle ${events.length} Events anzeigen`}
        </button>
      )}
    </div>
  );
}

export function ActivityFeed(props: ActivityFeedProps) {
  return (
    <ErrorBoundary>
      <ActivityFeedBase {...props} />
    </ErrorBoundary>
  );
}
