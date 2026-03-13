"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { fmt } from "@/lib/format";
import { ErrorBoundary } from "@/components/ErrorBoundary";

const PAIRS = ["BTC/USDC", "ETH/USDC", "SOL/USDC"];

interface BacktestResult {
  pair: string;
  days: number;
  initial_capital: number;
  total_pnl: number;
  total_trades: number;
  win_rate: number;
  sharpe_ratio: number;
  max_drawdown: number;
  profit_factor: number;
  equity_curve: [number, number][];
  trades: Array<{
    candle: number;
    ts: number;
    side: string;
    price: number;
    amount: number;
    fee: number;
    pnl: number;
    slippage_bps: number;
    equity: number;
  }>;
  regime_changes: Array<{ candle: number; ts: number; from: string; to: string }>;
  monthly_returns: number[];
  error?: string;
}

interface MCResult {
  pair: string;
  days_forward: number;
  n_simulations: number;
  median_pnl: number;
  mean_pnl: number;
  std_pnl: number;
  percentile_5: number;
  percentile_1: number;
  max_loss: number;
  best_case: number;
  probability_of_loss: number;
  value_at_risk_95: number;
  conditional_var_95: number;
  distribution: number[];
}

function BacktestViewerContent() {
  const [pair, setPair] = useState("BTC/USDC");
  const [days, setDays] = useState(30);
  const [capital, setCapital] = useState(200);
  const [paramOverrides, setParamOverrides] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);

  const runBacktest = useCallback(async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const body: Record<string, unknown> = { pair, days, capital };
      if (paramOverrides.trim()) {
        try {
          body.param_overrides = JSON.parse(paramOverrides.trim());
        } catch {
          setError("Ungültige JSON-Parameter");
          setLoading(false);
          return;
        }
      }
      const res = await fetch("/api/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        setError("Backtest starten fehlgeschlagen");
        setLoading(false);
        return;
      }
      const { id } = await res.json();
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const poll = await fetch(`/api/backtest?id=${id}`);
        const data = await poll.json();
        if (data.status === "completed" && data.result) {
          const r = data.result as BacktestResult;
          if (r.error) {
            setError(r.error);
          } else {
            setResult(r);
          }
          setLoading(false);
          return;
        }
        if (data.status === "failed") {
          setError("Backtest fehlgeschlagen");
          setLoading(false);
          return;
        }
      }
      setError("Timeout");
    } catch {
      setError("Netzwerkfehler");
    }
    setLoading(false);
  }, [pair, days, capital, paramOverrides]);

  const equityData =
    result?.equity_curve?.map(([ts, eq]) => ({
      ts: new Date(ts * 1000).toISOString(),
      equity: eq,
      label: new Date(ts * 1000).toLocaleDateString("de-DE", {
        day: "2-digit",
        month: "2-digit",
      }),
    })) ?? [];

  const regimeData =
    result?.regime_changes?.map((r) => ({
      ...r,
      label: new Date(r.ts * 1000).toLocaleDateString("de-DE", {
        day: "2-digit",
        month: "2-digit",
      }),
    })) ?? [];

  return (
    <div className="max-w-[1400px] mx-auto px-4 sm:px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold">Backtest Viewer</h1>
        <Link
          href="/"
          className="text-sm text-[var(--text-tertiary)] hover:text-[var(--accent)]"
        >
          ← Dashboard
        </Link>
      </div>

      {/* Form */}
      <div className="card p-4 mb-6">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
          Backtest starten
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div>
            <label className="text-[9px] text-[var(--text-quaternary)] uppercase">Pair</label>
            <select
              value={pair}
              onChange={(e) => setPair(e.target.value)}
              className="w-full mt-0.5 px-3 py-2 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] text-sm"
            >
              {PAIRS.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[9px] text-[var(--text-quaternary)] uppercase">Tage</label>
            <input
              type="number"
              min={1}
              max={365}
              value={days}
              onChange={(e) => setDays(parseInt(e.target.value) || 30)}
              className="w-full mt-0.5 px-3 py-2 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] text-sm"
            />
          </div>
          <div>
            <label className="text-[9px] text-[var(--text-quaternary)] uppercase">Kapital (USDC)</label>
            <input
              type="number"
              min={10}
              max={100000}
              value={capital}
              onChange={(e) => setCapital(parseFloat(e.target.value) || 200)}
              className="w-full mt-0.5 px-3 py-2 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] text-sm"
            />
          </div>
          <div className="sm:col-span-2 lg:col-span-1 flex items-end">
            <button
              onClick={runBacktest}
              disabled={loading}
              className="w-full py-2.5 rounded-lg font-semibold text-sm transition-all disabled:opacity-50"
              style={{
                background: "var(--accent)",
                color: "white",
              }}
            >
              {loading ? "Läuft..." : "Start"}
            </button>
          </div>
        </div>
        <div className="mt-3">
          <label className="text-[9px] text-[var(--text-quaternary)] uppercase">Parameter-Overrides (JSON, optional)</label>
          <textarea
            value={paramOverrides}
            onChange={(e) => setParamOverrides(e.target.value)}
            placeholder='{"spacing_percent": 0.5}'
            className="w-full mt-0.5 px-3 py-2 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] text-sm font-mono text-[11px] h-14 resize-none"
          />
        </div>
      </div>

      {error && (
        <div className="mb-4 px-4 py-2 rounded-lg bg-[color-mix(in_srgb,var(--down)_15%,transparent)] text-[var(--down)] text-sm">
          {error}
        </div>
      )}

      {/* Results */}
      {result && !result.error && (
        <div className="space-y-6">
          {/* Key Metrics */}
          <div className="card p-4">
            <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
              Key Metrics
            </h3>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <div>
                <p className="text-[9px] text-[var(--text-quaternary)]">Sharpe</p>
                <p className="text-lg font-bold font-mono">{result.sharpe_ratio.toFixed(2)}</p>
              </div>
              <div>
                <p className="text-[9px] text-[var(--text-quaternary)]">Max Drawdown</p>
                <p className="text-lg font-bold font-mono text-[var(--down)]">{result.max_drawdown.toFixed(2)}%</p>
              </div>
              <div>
                <p className="text-[9px] text-[var(--text-quaternary)]">Win Rate</p>
                <p className="text-lg font-bold font-mono">{result.win_rate.toFixed(1)}%</p>
              </div>
              <div>
                <p className="text-[9px] text-[var(--text-quaternary)]">Profit Factor</p>
                <p className="text-lg font-bold font-mono">{result.profit_factor.toFixed(2)}</p>
              </div>
            </div>
          </div>

          {/* Equity Curve */}
          {equityData.length > 1 && (
            <div className="card p-4">
              <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
                Equity Curve
              </h3>
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={equityData} margin={{ top: 0, right: 0, left: -15, bottom: 0 }}>
                  <defs>
                    <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.3} />
                      <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="label" tick={{ fill: "var(--text-quaternary)", fontSize: 9 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
                  <YAxis tick={{ fill: "var(--text-quaternary)", fontSize: 9 }} axisLine={false} tickLine={false} width={50} tickFormatter={(v) => fmt(v, 0)} />
                  <Tooltip
                    contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border-accent)", borderRadius: 8, fontSize: 11 }}
                    formatter={(v: number) => [fmt(v, 2), "Equity"]}
                  />
                  <Area type="monotone" dataKey="equity" stroke="var(--accent)" fill="url(#eqGrad)" strokeWidth={1.5} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Monthly Returns */}
          {result.monthly_returns && result.monthly_returns.length > 0 && (
            <div className="card p-4">
              <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
                Monthly Returns
              </h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-[var(--border)]">
                      <th className="text-left py-2 text-[var(--text-quaternary)] font-semibold">Monat</th>
                      {result.monthly_returns.map((_, i) => (
                        <th key={i} className="text-right py-2 text-[var(--text-quaternary)] font-semibold">
                          {i + 1}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td className="py-2 text-[var(--text-secondary)]">Return %</td>
                      {result.monthly_returns.map((r, i) => (
                        <td key={i} className={`text-right py-2 font-mono ${r >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"}`}>
                          {r >= 0 ? "+" : ""}{r.toFixed(2)}%
                        </td>
                      ))}
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Trades */}
          {result.trades && result.trades.length > 0 && (
            <div className="card p-4">
              <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
                Trades ({result.trades.length})
              </h3>
              <div className="max-h-64 overflow-y-auto">
                <table className="w-full text-[11px]">
                  <thead className="sticky top-0 bg-[var(--bg-primary)]">
                    <tr className="border-b border-[var(--border)]">
                      <th className="text-left py-1.5 text-[var(--text-quaternary)]">Zeit</th>
                      <th className="text-left py-1.5 text-[var(--text-quaternary)]">Side</th>
                      <th className="text-right py-1.5 text-[var(--text-quaternary)]">Price</th>
                      <th className="text-right py-1.5 text-[var(--text-quaternary)]">Amount</th>
                      <th className="text-right py-1.5 text-[var(--text-quaternary)]">PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...result.trades].reverse().map((t, i) => (
                      <tr key={i} className="border-b border-[var(--border-subtle)]">
                        <td className="py-1 font-mono text-[var(--text-tertiary)]">
                          {new Date(t.ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
                        </td>
                        <td className={`py-1 font-semibold ${t.side === "buy" ? "text-[var(--up)]" : "text-[var(--accent)]"}`}>
                          {t.side}
                        </td>
                        <td className="py-1 text-right font-mono">{fmt(t.price, 2)}</td>
                        <td className="py-1 text-right font-mono">{t.amount.toFixed(8)}</td>
                        <td className={`py-1 text-right font-mono ${t.pnl >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"}`}>
                          {t.pnl >= 0 ? "+" : ""}{fmt(t.pnl, 4)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Regime Timeline */}
          {regimeData.length > 0 && (
            <div className="card p-4">
              <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
                Regime-Timeline
              </h3>
              <div className="flex flex-wrap gap-2">
                {regimeData.map((r, i) => (
                  <div
                    key={i}
                    className="px-2 py-1 rounded text-[10px] font-mono"
                    style={{ background: "var(--bg-secondary)" }}
                  >
                    <span className="text-[var(--text-quaternary)]">{r.label}</span>
                    <span className="mx-1">→</span>
                    <span className="font-semibold">{r.from}</span>
                    <span className="mx-1">→</span>
                    <span className="font-semibold" style={{ color: "var(--accent)" }}>{r.to}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Walk-Forward */}
      <div className="card p-4 mb-6 mt-8">
        <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
          Walk-Forward Ergebnisse
        </h3>
        <p className="text-sm text-[var(--text-tertiary)] mb-3">
          Walk-Forward-Optimierung läuft nachts automatisch auf dem Pi. Hier werden zukünftig In-Sample vs. Out-of-Sample Performance, Robustness Score und Overfitting-Warnungen angezeigt.
        </p>
        <div className="rounded-lg p-4 text-center" style={{ background: "var(--bg-secondary)" }}>
          <p className="text-[var(--text-quaternary)] text-sm">Keine WF-Daten verfügbar</p>
        </div>
      </div>

      {/* Monte Carlo */}
      <MonteCarloSection />
    </div>
  );
}

function MonteCarloSection() {
  const [pair, setPair] = useState("BTC/USDC");
  const [days, setDays] = useState(30);
  const [sims, setSims] = useState(200);
  const [capital, setCapital] = useState(200);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<MCResult | null>(null);

  const runMC = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("/api/monte-carlo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pair, days, simulations: sims, capital }),
      });
      if (!res.ok) {
        setError("Start fehlgeschlagen");
        setLoading(false);
        return;
      }
      const { id } = await res.json();
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 5000));
        const poll = await fetch(`/api/monte-carlo?id=${id}`);
        const data = await poll.json();
        if (data.status === "completed" && data.result) {
          setResult(data.result as MCResult);
          setLoading(false);
          return;
        }
        if (data.status === "failed") {
          setError("Simulation fehlgeschlagen");
          setLoading(false);
          return;
        }
      }
      setError("Timeout");
    } catch {
      setError("Netzwerkfehler");
    }
    setLoading(false);
  };

  const fmtMC = (v: number) => (v >= 0 ? `+${v.toFixed(2)}` : v.toFixed(2));

  const histogramBins = (() => {
    if (!result?.distribution?.length || result.distribution.length < 5) return null;
    const d = [...result.distribution].sort((a, b) => a - b);
    const bins = 20;
    const mn = d[0]!;
    const mx = d[d.length - 1]!;
    const step = (mx - mn) / bins || 1;
    const counts = new Array(bins).fill(0);
    for (const v of d) {
      const b = Math.min(Math.max(0, Math.floor((v - mn) / step)), bins - 1);
      counts[b]++;
    }
    const maxC = Math.max(...counts, 1);
    const zeroIdx = mn < 0 && mx > 0 ? Math.floor((0 - mn) / step) : -1;
    return { counts, maxC, mn, mx, step, zeroIdx };
  })();

  return (
    <div className="card p-4">
      <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
        Monte Carlo Stress-Test
      </h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
        <div>
          <label className="text-[9px] text-[var(--text-quaternary)]">Pair</label>
          <select
            value={pair}
            onChange={(e) => setPair(e.target.value)}
            className="w-full mt-0.5 px-3 py-2 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] text-sm"
          >
            {PAIRS.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-[9px] text-[var(--text-quaternary)]">Tage</label>
          <input
            type="number"
            min={1}
            max={90}
            value={days}
            onChange={(e) => setDays(parseInt(e.target.value) || 30)}
            className="w-full mt-0.5 px-3 py-2 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] text-sm"
          />
        </div>
        <div>
          <label className="text-[9px] text-[var(--text-quaternary)]">Simulationen</label>
          <input
            type="number"
            min={10}
            max={1000}
            value={sims}
            onChange={(e) => setSims(parseInt(e.target.value) || 200)}
            className="w-full mt-0.5 px-3 py-2 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] text-sm"
          />
        </div>
        <div className="flex items-end">
          <button
            onClick={runMC}
            disabled={loading}
            className="w-full py-2.5 rounded-lg font-semibold text-sm disabled:opacity-50"
            style={{ background: "var(--accent)", color: "white" }}
          >
            {loading ? "Läuft..." : "Monte Carlo starten"}
          </button>
        </div>
      </div>
      {error && (
        <div className="mb-3 text-sm text-[var(--down)]">{error}</div>
      )}
      {result && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div className="text-center p-3 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
              <p className="text-[9px] text-[var(--text-quaternary)] uppercase">VaR 95%</p>
              <p className="font-mono font-bold text-[var(--down)]">{fmtMC(result.value_at_risk_95)}</p>
            </div>
            <div className="text-center p-3 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
              <p className="text-[9px] text-[var(--text-quaternary)] uppercase">CVaR 95%</p>
              <p className="font-mono font-bold text-[var(--down)]">{fmtMC(result.conditional_var_95)}</p>
            </div>
            <div className="text-center p-3 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
              <p className="text-[9px] text-[var(--text-quaternary)] uppercase">P(Verlust)</p>
              <p className={`font-mono font-bold ${result.probability_of_loss > 50 ? "text-[var(--down)]" : "text-[var(--up)]"}`}>
                {result.probability_of_loss.toFixed(0)}%
              </p>
            </div>
            <div className="text-center p-3 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
              <p className="text-[9px] text-[var(--text-quaternary)] uppercase">Median PnL</p>
              <p className={`font-mono font-bold ${result.median_pnl >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"}`}>
                {fmtMC(result.median_pnl)}
              </p>
            </div>
          </div>
          {histogramBins && (
            <div>
              <p className="text-[9px] text-[var(--text-quaternary)] uppercase mb-2">PnL-Distribution</p>
              <div className="flex items-end gap-px" style={{ height: 80 }}>
                {histogramBins.counts.map((c, i) => (
                  <div
                    key={i}
                    className="flex-1 rounded-t-sm transition-all min-w-[4px]"
                    style={{
                      height: `${(c / histogramBins.maxC) * 100}%`,
                      minHeight: c > 0 ? 2 : 0,
                      background:
                        i < histogramBins.zeroIdx
                          ? "var(--down)"
                          : i === histogramBins.zeroIdx
                            ? "var(--warn)"
                            : "var(--up)",
                      opacity: 0.8,
                    }}
                    title={`${(histogramBins.mn + i * histogramBins.step).toFixed(2)} – ${(histogramBins.mn + (i + 1) * histogramBins.step).toFixed(2)}: ${c}`}
                  />
                ))}
              </div>
              <div className="flex justify-between text-[9px] text-[var(--text-quaternary)] mt-1">
                <span>{fmtMC(histogramBins.mn)}</span>
                <span>{fmtMC(histogramBins.mx)}</span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function BacktestPage() {
  return (
    <ErrorBoundary>
      <BacktestViewerContent />
    </ErrorBoundary>
  );
}
