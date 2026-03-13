"use client";

import { zeitAgo } from "@/lib/format";

interface StatusBadgeProps {
  status: string;
  hb: string;
}

export function StatusBadge({ status, hb }: StatusBadgeProps) {
  const sec = Math.floor((Date.now() - new Date(hb).getTime()) / 1000);
  const offline = sec > 120;
  const label =
    offline ? "Offline"
    : status === "running" ? "Aktiv"
    : status === "paused" ? "Pausiert"
    : "Gestoppt";
  const color =
    offline ? "var(--down)"
    : status === "running" ? "var(--up)"
    : "var(--warn)";

  return (
    <span
      className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold"
      style={{ background: `color-mix(in srgb, ${color} 12%, transparent)`, color }}
    >
      <span
        className={`w-2 h-2 rounded-full ${!offline && status === "running" ? "pulse-live" : ""}`}
        style={{ background: color }}
      />
      {label}
      <span className="font-normal opacity-60">{zeitAgo(hb)}</span>
    </span>
  );
}
