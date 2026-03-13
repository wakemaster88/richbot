/** Dashboard constants — colors, styles, labels. */

export const COIN_COLORS: Record<string, string> = {
  USDC: "var(--up)",
  BTC: "#f7931a",
  SOL: "#9945ff",
  ETH: "#627eea",
};

export const REGIME_STYLE: Record<string, { label: string; color: string; bg: string }> = {
  ranging: { label: "SEITW\u00C4RTS", color: "var(--up)", bg: "var(--up-bg)" },
  trend_up: { label: "AUFW\u00C4RTS", color: "#3b82f6", bg: "rgba(59,130,246,0.12)" },
  trend_down: { label: "ABW\u00C4RTS", color: "var(--warn)", bg: "var(--warn-bg)" },
  volatile: { label: "VOLATIL", color: "var(--down)", bg: "var(--down-bg)" },
};

export const CB_COLORS: Record<string, string> = {
  GREEN: "var(--up)",
  YELLOW: "#eab308",
  ORANGE: "#f97316",
  RED: "var(--down)",
};

export const CB_BG: Record<string, string> = {
  GREEN: "color-mix(in srgb, var(--up) 12%, transparent)",
  YELLOW: "color-mix(in srgb, #eab308 15%, transparent)",
  ORANGE: "color-mix(in srgb, #f97316 18%, transparent)",
  RED: "color-mix(in srgb, var(--down) 18%, transparent)",
};

export const EVT_ICONS: Record<string, string> = {
  trade: "T",
  grid: "G",
  error: "!",
  config: "C",
  system: "S",
  monitoring: "\u26A1",
  regime: "R",
  optimization: "\u2699",
  trailing_tp: "\u2197",
  memory: "M",
  sentiment: "\uD83D\uDCF0",
  rl_optimization: "\uD83E\uDDE0",
};

export const EVT_COLORS: Record<string, string> = {
  trade: "var(--accent)",
  grid: "var(--cyan)",
  error: "var(--down)",
  warn: "var(--warn)",
  critical: "#ef4444",
  success: "var(--up)",
  config: "var(--text-secondary)",
  system: "var(--text-tertiary)",
  monitoring: "var(--warn)",
  regime: "#3b82f6",
  optimization: "var(--accent)",
  trailing_tp: "var(--up)",
  memory: "var(--warn)",
  sentiment: "#8b5cf6",
  rl_optimization: "var(--accent)",
};
