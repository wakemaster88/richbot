/** Dashboard formatting utilities. */

export function zeitAgo(d: string): string {
  const s = Math.floor((Date.now() - new Date(d).getTime()) / 1000);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function laufzeit(start: string): string {
  const s = Math.floor((Date.now() - new Date(start).getTime()) / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}T ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function fmt(n: number, d = 2): string {
  return n.toLocaleString("de-DE", {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

export function fmtAmount(n: number, base: string): string {
  if (base === "BTC") {
    const sats = Math.round(n * 1e8);
    return sats.toLocaleString("de-DE") + " sat";
  }
  return n.toLocaleString("de-DE", {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  }) + " " + base;
}
