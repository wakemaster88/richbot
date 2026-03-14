"use client";

import { useState, useEffect } from "react";
import type { CommandRecord } from "@/lib/types";
import { MonteCarloPanel } from "./MonteCarloPanel";
import { ErrorBoundary } from "./ErrorBoundary";

export interface ControlPanelProps {
  status: string;
  commands: CommandRecord[];
  onCommand: (t: string) => void;
  botConfig: Record<string, unknown> | null;
}

function ControlPanelBase({
  status,
  commands,
  onCommand,
  botConfig,
}: ControlPanelProps) {
  const [logLoading, setLogLoading] = useState(false);
  const sentCfg = botConfig?.sentiment as Record<string, unknown> | undefined;
  const rlCfg = botConfig?.rl as Record<string, unknown> | undefined;
  const [sentEnabled, setSentEnabled] = useState(true);
  const [rlEnabled, setRlEnabled] = useState(false);
  const [sentProvider, setSentProvider] = useState("local");
  const [configLoaded, setConfigLoaded] = useState(false);

  useEffect(() => {
    if (!botConfig || configLoaded) return;
    if (sentCfg) {
      if (typeof sentCfg.enabled === "boolean") setSentEnabled(sentCfg.enabled);
      if (typeof sentCfg.provider === "string") setSentProvider(sentCfg.provider);
    }
    if (rlCfg) {
      if (typeof rlCfg.enabled === "boolean") setRlEnabled(rlCfg.enabled);
    }
    setConfigLoaded(true);
  }, [botConfig, configLoaded, sentCfg, rlCfg]);

  const laeuft = status === "running";
  const gestoppt = status === "stopped" || status === "paused";
  const stLabels: Record<string, string> = {
    completed: "OK",
    failed: "Fehler",
    pending: "...",
  };
  const labels: Record<string, string> = {
    stop: "Stoppen",
    resume: "Fortsetzen",
    pause: "Pausieren",
    status: "Status",
    performance: "Performance",
    update_config: "Config",
    update_software: "Update",
    fetch_logs: "Logs",
    reset_rl: "RL Reset",
  };

  const sendConfig = (section: string, key: string, value: unknown) => {
    fetch("/api/commands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "update_config",
        payload: { [section]: { [key]: value } },
      }),
    });
  };

  const btn = (t: string, lbl: string, col: string) => (
    <button
      key={t}
      onClick={() => onCommand(t)}
      className="px-3 py-2 rounded-lg text-[10px] font-semibold transition-all active:scale-95"
      style={{
        background: `color-mix(in srgb, ${col} 10%, transparent)`,
        color: col,
        border: `1px solid color-mix(in srgb, ${col} 15%, transparent)`,
      }}
    >
      {lbl}
    </button>
  );

  const downloadLogs = async () => {
    setLogLoading(true);
    try {
      const res = await fetch("/api/commands", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: "fetch_logs" }),
      });
      if (!res.ok) {
        setLogLoading(false);
        return;
      }
      const cmd = await res.json();
      const cmdId = cmd.id;

      for (let i = 0; i < 20; i++) {
        await new Promise((r) => setTimeout(r, 1500));
        const check = await fetch(`/api/commands?limit=10`, { cache: "no-store" });
        const list = await check.json();
        const found = (list as CommandRecord[]).find((c) => c.id === cmdId);
        if (!found || found.status === "pending") continue;
        if (found.status === "completed" && found.result) {
          const logs = (found.result as { logs?: string }).logs || "Keine Logs";
          const blob = new Blob([logs], { type: "text/plain" });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `richbot-logs-${new Date().toISOString().slice(0, 16).replace(/[T:]/g, "-")}.txt`;
          a.click();
          URL.revokeObjectURL(url);
          break;
        }
        if (found.status === "failed") break;
      }
    } catch {
      /* ignore */
    }
    setLogLoading(false);
  };

  return (
    <div className="card p-4 sm:p-5 h-full">
      <h3 className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.12em] font-semibold mb-3">
        Steuerung
      </h3>
      <div className="flex flex-wrap gap-1.5 mb-4">
        {laeuft && (
          <>
            {btn("pause", "Pausieren", "var(--warn)")}
            {btn("stop", "Stoppen", "var(--down)")}
          </>
        )}
        {gestoppt && btn("resume", "Fortsetzen", "var(--up)")}
        {btn("status", "Status", "var(--accent)")}
        {btn("update_software", "Update", "var(--cyan)")}
        <button
          onClick={downloadLogs}
          disabled={logLoading}
          className="px-3 py-2 rounded-lg text-[10px] font-semibold transition-all active:scale-95 disabled:opacity-50"
          style={{
            background: "color-mix(in srgb, var(--text-secondary) 10%, transparent)",
            color: "var(--text-secondary)",
            border: "1px solid color-mix(in srgb, var(--text-secondary) 15%, transparent)",
          }}
        >
          {logLoading ? "Lade..." : "Logs"}
        </button>
      </div>

      <div
        className="rounded-lg p-2.5 mb-3"
        style={{ background: "var(--bg-secondary)" }}
      >
        <div className="text-[8px] text-[var(--text-quaternary)] uppercase tracking-wider mb-2 font-semibold">
          KI-Features
        </div>
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-[var(--text-secondary)]">
              Sentiment
            </span>
            <div className="flex items-center gap-2">
              <select
                value={sentProvider}
                onChange={(e) => {
                  setSentProvider(e.target.value);
                  sendConfig("sentiment", "provider", e.target.value);
                }}
                className="text-[9px] bg-transparent border border-[var(--border)] rounded px-1.5 py-0.5 text-[var(--text-secondary)] outline-none"
              >
                <option value="local">Local</option>
                <option value="grok">Grok</option>
                <option value="openai">OpenAI</option>
              </select>
              <button
                onClick={() => {
                  const v = !sentEnabled;
                  setSentEnabled(v);
                  sendConfig("sentiment", "enabled", v);
                }}
                className="w-8 h-4 rounded-full relative transition-all"
                style={{
                  background: sentEnabled ? "var(--up)" : "var(--bg-elevated)",
                }}
              >
                <span
                  className="absolute w-3 h-3 rounded-full bg-white top-0.5 transition-all"
                  style={{ left: sentEnabled ? 17 : 2 }}
                />
              </button>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-[var(--text-secondary)]">
              RL-Optimizer
            </span>
            <button
              onClick={() => {
                const v = !rlEnabled;
                setRlEnabled(v);
                sendConfig("rl", "enabled", v);
              }}
              className="w-8 h-4 rounded-full relative transition-all"
              style={{
                background: rlEnabled ? "var(--up)" : "var(--bg-elevated)",
              }}
            >
              <span
                className="absolute w-3 h-3 rounded-full bg-white top-0.5 transition-all"
                style={{ left: rlEnabled ? 17 : 2 }}
              />
            </button>
          </div>
        </div>
      </div>

      <MonteCarloPanel />

      {commands.length > 0 && (
        <div className="space-y-1 max-h-28 overflow-y-auto">
          {commands.slice(0, 8).map((c) => (
            <div
              key={c.id}
              className="flex items-center gap-2 text-[10px] py-1 px-2 rounded hover:bg-[var(--bg-secondary)]"
            >
              <span className="text-[var(--text-quaternary)] font-mono w-9 shrink-0">
                {new Date(c.createdAt).toLocaleTimeString("de-DE", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
              <span className="text-[var(--text-tertiary)]">
                {labels[c.type] || c.type}
              </span>
              <span
                className="ml-auto text-[8px] font-bold px-1 py-0.5 rounded"
                style={{
                  background:
                    c.status === "completed"
                      ? "var(--up-bg)"
                      : c.status === "failed"
                        ? "var(--down-bg)"
                        : "var(--warn-bg)",
                  color:
                    c.status === "completed"
                      ? "var(--up)"
                      : c.status === "failed"
                        ? "var(--down)"
                        : "var(--warn)",
                }}
              >
                {stLabels[c.status] || c.status}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function ControlPanel(props: ControlPanelProps) {
  return (
    <ErrorBoundary>
      <ControlPanelBase {...props} />
    </ErrorBoundary>
  );
}
