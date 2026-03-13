"use client";

import type { SentimentData } from "@/lib/types";

export interface SentimentPanelProps {
  data: SentimentData | null;
  enabled?: boolean;
}

const SOURCE_LABELS: Record<string, string> = {
  news: "News",
  twitter: "Twitter",
  reddit: "Reddit",
  fear_greed: "Fear & Greed",
};

function FearGreedGauge({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value));
  const deg = (pct / 100) * 180 - 90;
  const color =
    pct <= 25 ? "var(--down)" : pct <= 50 ? "var(--warn)" : pct <= 75 ? "var(--up)" : "var(--up)";
  return (
    <div className="relative w-20 h-12 mx-auto">
      <svg viewBox="0 0 100 50" className="w-full h-full">
        <path
          d="M 10 40 A 35 35 0 0 1 90 40"
          fill="none"
          stroke="var(--bg-tertiary)"
          strokeWidth="6"
          strokeLinecap="round"
        />
        <path
          d="M 10 40 A 35 35 0 0 1 90 40"
          fill="none"
          stroke={color}
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray={`${(pct / 100) * 55} 55`}
        />
        <line
          x1="50"
          y1="40"
          x2={50 + 28 * Math.cos((deg * Math.PI) / 180)}
          y2={40 + 28 * Math.sin((deg * Math.PI) / 180)}
          stroke="var(--text-primary)"
          strokeWidth="2"
        />
        <text x="50" y="32" textAnchor="middle" className="text-[10px] font-bold" fill="var(--text-primary)">
          {value}
        </text>
      </svg>
      <div className="flex justify-between text-[8px] text-[var(--text-quaternary)] mt-0.5 px-1">
        <span>Fear</span>
        <span>Greed</span>
      </div>
    </div>
  );
}

export function SentimentPanel({ data, enabled = true }: SentimentPanelProps) {
  if (!enabled) return null;
  if (!data) {
    return (
      <div className="card p-4">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
          Sentiment
        </h3>
        <p className="text-[11px] text-[var(--text-tertiary)]">Keine Daten</p>
      </div>
    );
  }

  const sources = data.sources ?? {};
  const consensusLabel =
    data.consensus === "bullish" ? "Bullisch" : data.consensus === "bearish" ? "Bearisch" : "Gemischt";

  return (
    <div className="card p-4">
      <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
        Social Sentiment
      </h3>

      <div className="flex flex-col sm:flex-row gap-4">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[9px] text-[var(--text-quaternary)]">Score</span>
            <span
              className="text-sm font-bold font-mono"
              style={{
                color:
                  data.score > 0.3 ? "var(--up)" : data.score < -0.3 ? "var(--down)" : "var(--text-secondary)",
              }}
            >
              {data.score >= 0 ? "+" : ""}
              {data.score.toFixed(2)}
            </span>
            <span className="text-[9px] text-[var(--text-quaternary)]">
              ({Math.round((data.confidence ?? 0) * 100)}%)
            </span>
          </div>

          <div className="space-y-1.5 mb-3">
            {Object.entries(sources).map(([k, v]) => (
              <div key={k}>
                <div className="flex justify-between text-[9px] mb-0.5">
                  <span className="text-[var(--text-quaternary)]">{SOURCE_LABELS[k] ?? k}</span>
                  <span
                    className="font-mono"
                    style={{
                      color: v.score > 0 ? "var(--up)" : v.score < 0 ? "var(--down)" : "var(--text-tertiary)",
                    }}
                  >
                    {v.score >= 0 ? "+" : ""}
                    {v.score.toFixed(2)}
                  </span>
                </div>
                <div className="h-1.5 rounded-full overflow-hidden bg-[var(--bg-secondary)]">
                  <div
                    className="h-full rounded-full transition-all min-w-[4px]"
                    style={{
                      width: `${((v.score + 1) / 2) * 100}%`,
                      background: v.score >= 0 ? "var(--up)" : "var(--down)",
                    }}
                  />
                </div>
              </div>
            ))}
          </div>

          <div className="flex items-center gap-1.5 text-[10px]">
            <span
              className="px-2 py-0.5 rounded font-semibold"
              style={{
                background:
                  data.consensus === "bullish"
                    ? "var(--up-bg)"
                    : data.consensus === "bearish"
                      ? "var(--down-bg)"
                      : "var(--warn-bg)",
                color:
                  data.consensus === "bullish"
                    ? "var(--up)"
                    : data.consensus === "bearish"
                      ? "var(--down)"
                      : "var(--warn)",
              }}
            >
              {consensusLabel}
            </span>
          </div>
        </div>

        {data.fear_greed != null && (
          <div className="border-l border-[var(--border-subtle)] pl-4">
            <p className="text-[9px] text-[var(--text-quaternary)] uppercase mb-1">Fear & Greed</p>
            <FearGreedGauge value={data.fear_greed} />
          </div>
        )}
      </div>

      {data.headlines && data.headlines.length > 0 && (
        <div className="mt-3 pt-3 border-t border-[var(--border-subtle)]">
          <p className="text-[9px] text-[var(--text-quaternary)] uppercase mb-1">Top Headlines</p>
          <ul className="space-y-0.5 max-h-16 overflow-y-auto">
            {data.headlines.slice(0, 5).map((h, i) => (
              <li key={i} className="text-[10px] text-[var(--text-secondary)] truncate">
                {h}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
