"use client";

import { useState, useEffect, useMemo } from "react";
import Link from "next/link";
import type { Trade } from "@/lib/types";
import { fmt, fmtAmount } from "@/lib/format";


function getMonthDays(year: number, month: number) {
  const first = new Date(year, month, 1);
  const last = new Date(year, month + 1, 0);
  const days: { date: Date; day: number; isCurrentMonth: boolean }[] = [];
  const startPad = first.getDay();
  for (let i = 0; i < startPad; i++) {
    const d = new Date(year, month, -startPad + i + 1);
    days.push({ date: d, day: d.getDate(), isCurrentMonth: false });
  }
  for (let i = 1; i <= last.getDate(); i++) {
    days.push({
      date: new Date(year, month, i),
      day: i,
      isCurrentMonth: true,
    });
  }
  const remaining = 42 - days.length;
  for (let i = 1; i <= remaining; i++) {
    const d = new Date(year, month + 1, i);
    days.push({ date: d, day: d.getDate(), isCurrentMonth: false });
  }
  return days;
}

function dateKey(d: Date) {
  return d.toISOString().slice(0, 10);
}

export default function JournalPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);
  const [today] = useState(() => new Date());
  const [viewMonth, setViewMonth] = useState(today.getMonth());
  const [viewYear, setViewYear] = useState(today.getFullYear());
  const [selectedDate, setSelectedDate] = useState<string | null>(
    dateKey(today)
  );
  const [pairFilter, setPairFilter] = useState<string>("");
  const [pnlFilter, setPnlFilter] = useState<"all" | "win" | "loss">("all");

  const fromDate = useMemo(
    () => new Date(viewYear, viewMonth, 1),
    [viewYear, viewMonth]
  );
  const toDate = useMemo(
    () => new Date(viewYear, viewMonth + 1, 0, 23, 59, 59),
    [viewYear, viewMonth]
  );

  useEffect(() => {
    let active = true;
    setLoading(true);
    const params = new URLSearchParams({
      from: fromDate.toISOString(),
      to: toDate.toISOString(),
      limit: "2000",
    });
    if (pairFilter) params.set("pair", pairFilter);
    fetch(`/api/trades?${params}`, { cache: "no-store" })
      .then((r) => r.json())
      .then((data) => {
        if (active) setTrades(Array.isArray(data) ? data : []);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [fromDate.toISOString(), toDate.toISOString(), pairFilter]);

  const byDay = useMemo(() => {
    const map = new Map<
      string,
      { trades: Trade[]; pnl: number; count: number }
    >();
    for (const t of trades) {
      const key = dateKey(new Date(t.timestamp));
      const exist = map.get(key) || {
        trades: [],
        pnl: 0,
        count: 0,
      };
      exist.trades.push(t);
      exist.pnl += t.pnl || 0;
      exist.count++;
      map.set(key, exist);
    }
    return map;
  }, [trades]);

  const pairs = useMemo(() => {
    const set = new Set(trades.map((t) => t.pair));
    return Array.from(set).sort();
  }, [trades]);

  const selectedTrades = useMemo(() => {
    if (!selectedDate) return [];
    const dayData = byDay.get(selectedDate);
    if (!dayData) return [];
    let list = [...dayData.trades].sort(
      (a, b) =>
        new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
    );
    if (pnlFilter === "win") list = list.filter((t) => (t.pnl ?? 0) >= 0);
    if (pnlFilter === "loss") list = list.filter((t) => (t.pnl ?? 0) < 0);
    return list;
  }, [selectedDate, byDay, pnlFilter]);

  const stats = useMemo(() => {
    const now = new Date();
    const todayStart = new Date(now);
    todayStart.setHours(0, 0, 0, 0);
    const weekStart = new Date(todayStart);
    weekStart.setDate(weekStart.getDate() - weekStart.getDay());
    const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);

    let dayPnl = 0,
      weekPnl = 0,
      monthPnl = 0;
    for (const t of trades) {
      const ts = new Date(t.timestamp).getTime();
      const p = t.pnl ?? 0;
      if (ts >= todayStart.getTime()) dayPnl += p;
      if (ts >= weekStart.getTime()) weekPnl += p;
      if (ts >= monthStart.getTime()) monthPnl += p;
    }
    return { dayPnl, weekPnl, monthPnl };
  }, [trades]);

  const days = useMemo(
    () => getMonthDays(viewYear, viewMonth),
    [viewYear, viewMonth]
  );

  const handleExport = () => {
    const from = fromDate.toISOString().slice(0, 10);
    const to = toDate.toISOString().slice(0, 10);
    const params = new URLSearchParams({
      format: "csv",
      from: from,
      to: to,
    });
    if (pairFilter) params.set("pair", pairFilter);
    window.open(`/api/trades/export?${params}`, "_blank");
  };

  return (
    <div className="max-w-[1200px] mx-auto px-4 py-6 pb-12">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold tracking-tight">Trade Journal</h1>
        <Link
          href="/"
          className="text-[12px] text-[var(--text-quaternary)] hover:text-[var(--accent)] transition-colors"
        >
          ← Dashboard
        </Link>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-2 mb-6">
        <div className="card p-4">
          <p className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-semibold">
            Heute
          </p>
          <p
            className={`text-lg font-bold font-mono mt-0.5 ${
              stats.dayPnl >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"
            }`}
          >
            {stats.dayPnl >= 0 ? "+" : ""}
            {fmt(stats.dayPnl, 4)}
          </p>
        </div>
        <div className="card p-4">
          <p className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-semibold">
            Diese Woche
          </p>
          <p
            className={`text-lg font-bold font-mono mt-0.5 ${
              stats.weekPnl >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"
            }`}
          >
            {stats.weekPnl >= 0 ? "+" : ""}
            {fmt(stats.weekPnl, 4)}
          </p>
        </div>
        <div className="card p-4">
          <p className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-semibold">
            Dieser Monat
          </p>
          <p
            className={`text-lg font-bold font-mono mt-0.5 ${
              stats.monthPnl >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"
            }`}
          >
            {stats.monthPnl >= 0 ? "+" : ""}
            {fmt(stats.monthPnl, 4)}
          </p>
        </div>
      </div>

      {/* Filters */}
      <div className="card p-4 mb-4 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <label className="text-[10px] text-[var(--text-quaternary)] uppercase font-semibold">
            Pair
          </label>
          <select
            value={pairFilter}
            onChange={(e) => setPairFilter(e.target.value)}
            className="text-sm bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-2 py-1.5 text-[var(--text-primary)] outline-none"
          >
            <option value="">Alle</option>
            {pairs.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-[10px] text-[var(--text-quaternary)] uppercase font-semibold">
            PnL
          </label>
          <select
            value={pnlFilter}
            onChange={(e) =>
              setPnlFilter(e.target.value as "all" | "win" | "loss")
            }
            className="text-sm bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-2 py-1.5 text-[var(--text-primary)] outline-none"
          >
            <option value="all">Alle</option>
            <option value="win">Gewinn</option>
            <option value="loss">Verlust</option>
          </select>
        </div>
        <button
          onClick={handleExport}
          className="ml-auto px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-all"
          style={{
            background: "var(--accent-bg)",
            color: "var(--accent)",
            border: "1px solid color-mix(in srgb, var(--accent) 25%, transparent)",
          }}
        >
          Export CSV
        </button>
      </div>

      {/* Calendar */}
      <div className="card p-4 mb-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold">
            {new Date(viewYear, viewMonth).toLocaleString("de-DE", {
              month: "long",
              year: "numeric",
            })}
          </h2>
          <div className="flex gap-1">
            <button
              onClick={() => {
                if (viewMonth === 0) {
                  setViewYear((y) => y - 1);
                  setViewMonth(11);
                } else setViewMonth((m) => m - 1);
              }}
              className="p-1.5 rounded text-[var(--text-quaternary)] hover:bg-[var(--bg-secondary)] hover:text-[var(--text-primary)]"
            >
              ←
            </button>
            <button
              onClick={() => {
                if (viewMonth === 11) {
                  setViewYear((y) => y + 1);
                  setViewMonth(0);
                } else setViewMonth((m) => m + 1);
              }}
              className="p-1.5 rounded text-[var(--text-quaternary)] hover:bg-[var(--bg-secondary)] hover:text-[var(--text-primary)]"
            >
              →
            </button>
          </div>
        </div>
        <div className="grid grid-cols-7 gap-px text-center text-[9px] text-[var(--text-quaternary)] font-semibold mb-1">
          {["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"].map((d) => (
            <div key={d}>{d}</div>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-1">
          {days.map(({ date, day, isCurrentMonth }) => {
            const key = dateKey(date);
            const dayData = byDay.get(key);
            const pnl = dayData?.pnl ?? 0;
            const count = dayData?.count ?? 0;
            const isSelected = selectedDate === key;
            const isToday = key === dateKey(today);
            return (
              <button
                key={key}
                onClick={() => setSelectedDate(key)}
                className={`aspect-square rounded-lg text-[12px] font-mono flex flex-col items-center justify-center transition-all ${
                  !isCurrentMonth ? "opacity-40" : ""
                } ${
                  isSelected
                    ? "ring-2 ring-[var(--accent)] bg-[var(--accent-bg)]"
                    : "hover:bg-[var(--bg-secondary)]"
                } ${isToday ? "ring-1 ring-[var(--accent)]" : ""}`}
                style={
                  count > 0
                    ? {
                        background: isSelected
                          ? undefined
                          : pnl >= 0
                            ? "color-mix(in srgb, var(--up) 18%, transparent)"
                            : "color-mix(in srgb, var(--down) 18%, transparent)",
                      }
                    : undefined
                }
              >
                <span>{day}</span>
                {count > 0 && (
                  <span className="text-[8px] text-[var(--text-quaternary)]">
                    {count}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Day view */}
      <div className="card overflow-hidden">
        <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
          <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold">
            {selectedDate
              ? new Date(selectedDate + "T12:00:00").toLocaleDateString(
                  "de-DE",
                  { weekday: "long", day: "numeric", month: "long" }
                )
              : "Tag waehlen"}
          </h3>
          {selectedDate && byDay.has(selectedDate) && (
            <span
              className={`text-[11px] font-bold font-mono ${
                (byDay.get(selectedDate)?.pnl ?? 0) >= 0
                  ? "text-[var(--up)]"
                  : "text-[var(--down)]"
              }`}
            >
              {(byDay.get(selectedDate)?.pnl ?? 0) >= 0 ? "+" : ""}
              {fmt(byDay.get(selectedDate)?.pnl ?? 0, 4)}
            </span>
          )}
        </div>
        {loading ? (
          <div className="p-8 text-center text-[var(--text-quaternary)] text-sm">
            Lade...
          </div>
        ) : selectedTrades.length === 0 ? (
          <div className="p-8 text-center text-[var(--text-quaternary)] text-sm">
            {selectedDate
              ? "Keine Trades an diesem Tag"
              : "Waehle einen Tag im Kalender"}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider border-b border-[var(--border-subtle)]">
                  <th className="text-left px-4 py-2 font-medium">Zeit</th>
                  <th className="text-left px-4 py-2 font-medium">Typ</th>
                  <th className="text-left px-4 py-2 font-medium">Pair</th>
                  <th className="text-right px-4 py-2 font-medium">Preis</th>
                  <th className="text-right px-4 py-2 font-medium">Menge</th>
                  <th className="text-right px-4 py-2 font-medium">Wert</th>
                  <th className="text-right px-4 py-2 font-medium">Fee</th>
                  <th className="text-right px-4 py-2 font-medium">PnL</th>
                </tr>
              </thead>
              <tbody>
                {selectedTrades.map((t) => (
                  <tr
                    key={t.id}
                    className="border-b border-[var(--border-subtle)] hover:bg-[var(--bg-card-hover)]"
                  >
                    <td className="px-4 py-2 font-mono text-[10px] text-[var(--text-tertiary)]">
                      {new Date(t.timestamp).toLocaleTimeString("de-DE", {
                        hour: "2-digit",
                        minute: "2-digit",
                        second: "2-digit",
                      })}
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className="inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold"
                        style={{
                          background:
                            t.side === "buy" ? "var(--up-bg)" : "var(--down-bg)",
                          color: t.side === "buy" ? "var(--up)" : "var(--down)",
                        }}
                      >
                        {t.side === "buy" ? "KAUF" : "VERK."}
                      </span>
                    </td>
                    <td className="px-4 py-2 font-mono">{t.pair}</td>
                    <td className="px-4 py-2 text-right font-mono">
                      {fmt(t.price, 0)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-[var(--text-tertiary)]">
                      {fmtAmount(t.amount, t.pair.split("/")[0])}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-[var(--text-tertiary)]">
                      {fmt(t.amount * t.price)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-[var(--text-quaternary)]">
                      {fmt(t.fee ?? 0, 4)}
                    </td>
                    <td
                      className={`px-4 py-2 text-right font-mono font-semibold ${
                        (t.pnl ?? 0) >= 0 ? "text-[var(--up)]" : "text-[var(--down)]"
                      }`}
                    >
                      {(t.pnl ?? 0) >= 0 ? "+" : ""}
                      {fmt(t.pnl ?? 0, 4)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
