"use client";

import { useState, useEffect, useCallback, useMemo } from "react";

/* ---- Pi System Types ---- */

interface PiSystem {
  cpu_temp?: number;
  cpu_percent?: number;
  ram_total_mb?: number;
  ram_used_mb?: number;
  ram_percent?: number;
  load_1m?: number;
  load_5m?: number;
  load_15m?: number;
  disk_total_gb?: number;
  disk_used_gb?: number;
  disk_percent?: number;
  hostname?: string;
  arch?: string;
  python?: string;
  rss_kb?: number;
  public_ip?: string;
}

interface PiStatus {
  connected: boolean;
  lastSeen?: string;
  uptime?: number;
  system: PiSystem | null;
}

/* ---- Bot Config Types ---- */

interface BotConfigData {
  exchange?: { name?: string; sandbox?: boolean };
  pairs?: string[];
  grid?: {
    grid_count?: number;
    spacing_percent?: number;
    amount_per_order?: number;
    range_multiplier?: number;
    infinity_mode?: boolean;
    trail_trigger_percent?: number;
  };
  atr?: { period?: number; timeframe?: string; multiplier?: number };
  risk?: {
    kelly_fraction?: number;
    max_drawdown_percent?: number;
    trailing_stop_percent?: number;
    max_position_percent?: number;
    min_order_amount?: number;
    volatility_scaling?: boolean;
  };
  ml?: {
    enabled?: boolean;
    confidence_threshold?: number;
    retrain_interval_hours?: number;
    lookback_days?: number;
    prediction_interval_minutes?: number;
    timeframes?: string[];
  };
  telegram?: {
    enabled?: boolean;
    alert_on_fill?: boolean;
    alert_on_range_shift?: boolean;
    alert_on_drawdown?: boolean;
    daily_report?: boolean;
  };
  websocket?: {
    enabled?: boolean;
    reconnect_delay?: number;
    max_reconnect_attempts?: number;
    ping_interval?: number;
  };
  cloud?: {
    enabled?: boolean;
    heartbeat_interval?: number;
    command_poll_interval?: number;
    sync_trades?: boolean;
    sync_equity?: boolean;
  };
}

type SaveStatus = "idle" | "saving" | "saved" | "error";

const DEFAULTS: BotConfigData = {
  exchange: { name: "binance", sandbox: true },
  pairs: ["BTC/USDT", "ETH/USDT"],
  grid: {
    grid_count: 20,
    spacing_percent: 0.5,
    amount_per_order: 0.001,
    range_multiplier: 1.0,
    infinity_mode: true,
    trail_trigger_percent: 1.5,
  },
  atr: { period: 14, timeframe: "1h", multiplier: 2.0 },
  risk: {
    kelly_fraction: 0.25,
    max_drawdown_percent: 8.0,
    trailing_stop_percent: 1.0,
    max_position_percent: 30.0,
    min_order_amount: 0.0001,
    volatility_scaling: true,
  },
  ml: {
    enabled: true,
    confidence_threshold: 0.7,
    retrain_interval_hours: 24,
    lookback_days: 90,
    prediction_interval_minutes: 15,
    timeframes: ["1h", "4h"],
  },
  telegram: {
    enabled: true,
    alert_on_fill: true,
    alert_on_range_shift: true,
    alert_on_drawdown: true,
    daily_report: true,
  },
  websocket: {
    enabled: true,
    reconnect_delay: 5,
    max_reconnect_attempts: 50,
    ping_interval: 30,
  },
  cloud: {
    enabled: true,
    heartbeat_interval: 30,
    command_poll_interval: 5,
    sync_trades: true,
    sync_equity: true,
  },
};

// -- Field Components --

function Zahl({ label, value, onChange, step, min, max, hint }: {
  label: string; value: number | undefined; onChange: (v: number) => void;
  step?: number; min?: number; max?: number; hint?: string;
}) {
  const [local, setLocal] = useState(value?.toString() ?? "");
  useEffect(() => { setLocal(value?.toString() ?? ""); }, [value]);

  return (
    <div className="space-y-1.5">
      <label className="block text-[10px] text-[var(--text-tertiary)] uppercase tracking-[0.1em] font-medium">{label}</label>
      <input
        type="number"
        value={local}
        onChange={(e) => {
          setLocal(e.target.value);
          const v = parseFloat(e.target.value);
          if (!isNaN(v)) onChange(v);
        }}
        onBlur={() => {
          const v = parseFloat(local);
          if (!isNaN(v)) { onChange(v); setLocal(v.toString()); }
          else if (value !== undefined) setLocal(value.toString());
        }}
        step={step || 1}
        min={min}
        max={max}
        className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-xl px-3.5 py-2.5 text-sm font-mono text-[var(--text-primary)] placeholder:text-[var(--text-quaternary)] focus:border-[var(--accent)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)]/20 transition-all"
      />
      {hint && <p className="text-[10px] text-[var(--text-tertiary)] leading-relaxed">{hint}</p>}
    </div>
  );
}

function Schalter({ label, value, onChange, hint }: {
  label: string; value: boolean | undefined; onChange: (v: boolean) => void; hint?: string;
}) {
  return (
    <div className="flex items-center justify-between py-2.5 gap-4">
      <div className="min-w-0">
        <p className="text-sm text-[var(--text-primary)]">{label}</p>
        {hint && <p className="text-[10px] text-[var(--text-tertiary)] mt-0.5">{hint}</p>}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={!!value}
        onClick={() => onChange(!value)}
        className={`relative inline-flex h-[22px] w-10 shrink-0 cursor-pointer rounded-full border transition-colors duration-200 ${
          value ? "bg-[var(--up)] border-[var(--up)]" : "bg-[var(--bg-elevated)] border-[var(--border)]"
        }`}
      >
        <span
          className={`pointer-events-none absolute top-[3px] left-[3px] h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
            value ? "translate-x-[18px]" : "translate-x-0"
          }`}
        />
      </button>
    </div>
  );
}

function Auswahl({ label, value, options, onChange }: {
  label: string; value: string | undefined; options: string[]; onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <label className="block text-[10px] text-[var(--text-tertiary)] uppercase tracking-[0.1em] font-medium">{label}</label>
      <select
        value={value || options[0]}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-xl px-3.5 py-2.5 text-sm text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)]/20 transition-all appearance-none cursor-pointer"
      >
        {options.map((o) => (
          <option key={o} value={o} className="bg-[var(--bg-card)]">{o}</option>
        ))}
      </select>
    </div>
  );
}

function Tags({ label, value, onChange, hint, placeholder }: {
  label: string; value: string[] | undefined; onChange: (v: string[]) => void; hint?: string; placeholder?: string;
}) {
  const [input, setInput] = useState("");
  const items = value || [];

  const add = () => {
    const trimmed = input.trim().toUpperCase();
    if (trimmed && !items.includes(trimmed)) onChange([...items, trimmed]);
    setInput("");
  };

  return (
    <div className="space-y-2">
      <label className="block text-[10px] text-[var(--text-tertiary)] uppercase tracking-[0.1em] font-medium">{label}</label>
      <div className="flex flex-wrap gap-2 min-h-[28px]">
        {items.map((tag) => (
          <span key={tag} className="group flex items-center gap-1.5 bg-[var(--bg-elevated)] border border-[var(--border)] text-[var(--text-primary)] text-xs px-2.5 py-1.5 rounded-lg font-mono">
            {tag}
            <button
              onClick={() => onChange(items.filter((t) => t !== tag))}
              className="text-[var(--text-quaternary)] hover:text-[var(--down)] transition-colors text-[10px]"
            >
              x
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), add())}
          placeholder={placeholder || "BTC/USDT"}
          className="flex-1 bg-[var(--bg-secondary)] border border-[var(--border)] rounded-xl px-3.5 py-2 text-sm font-mono text-[var(--text-primary)] placeholder:text-[var(--text-quaternary)] focus:border-[var(--accent)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)]/20 transition-all"
        />
        <button onClick={add} className="px-4 py-2 bg-[var(--bg-elevated)] border border-[var(--border)] text-[var(--text-secondary)] rounded-xl text-sm hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-primary)] transition-all active:scale-95">
          +
        </button>
      </div>
      {hint && <p className="text-[10px] text-[var(--text-tertiary)]">{hint}</p>}
    </div>
  );
}

function Sektion({ titel, beschreibung, children }: {
  titel: string; beschreibung?: string; children: React.ReactNode;
}) {
  return (
    <div className="card card-hover p-5 sm:p-6 transition-all">
      <div className="mb-5">
        <h3 className="text-sm font-semibold text-[var(--text-primary)] mb-0.5">{titel}</h3>
        {beschreibung && <p className="text-[11px] text-[var(--text-tertiary)]">{beschreibung}</p>}
      </div>
      <div className="space-y-4">{children}</div>
    </div>
  );
}

/* ---- Pi Gauge ---- */

function Gauge({ label, value, max, unit, color, icon }: {
  label: string; value: number; max: number; unit: string; color: string; icon: string;
}) {
  const pct = Math.min((value / max) * 100, 100);
  const warn = pct > 80;
  const crit = pct > 90;
  const barColor = crit ? "var(--down)" : warn ? "var(--warn)" : color;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-[var(--text-tertiary)] uppercase tracking-[0.1em] font-medium flex items-center gap-1.5">
          <span className="text-xs opacity-60">{icon}</span>
          {label}
        </span>
        <span className="text-xs font-mono font-semibold" style={{ color: barColor }}>
          {value}{unit}
        </span>
      </div>
      <div className="h-1.5 bg-[var(--bg-secondary)] rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700 ease-out"
          style={{ width: `${pct}%`, background: barColor }}
        />
      </div>
      <div className="flex justify-between text-[9px] text-[var(--text-quaternary)]">
        <span>0</span>
        <span>{max}{unit}</span>
      </div>
    </div>
  );
}

function PiStatKarte({ label, wert, einheit, icon }: {
  label: string; wert: string | number; einheit?: string; icon: string;
}) {
  return (
    <div className="bg-[var(--bg-secondary)] rounded-xl p-3.5 border border-[var(--border-subtle)]">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-sm opacity-60">{icon}</span>
        <span className="text-[10px] text-[var(--text-tertiary)] uppercase tracking-[0.1em] font-medium">{label}</span>
      </div>
      <div className="flex items-baseline gap-1">
        <span className="text-lg font-bold font-mono text-[var(--text-primary)]">{wert}</span>
        {einheit && <span className="text-[10px] text-[var(--text-tertiary)]">{einheit}</span>}
      </div>
    </div>
  );
}

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}T ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

/* ---- Secrets Section ---- */

const SECRET_FIELDS = [
  { key: "BINANCE_API_KEY", label: "Binance API-Key", hint: "Spot-Trading muss aktiviert sein", placeholder: "z.B. aB3d...xY9z" },
  { key: "BINANCE_SECRET", label: "Binance Secret", hint: "Geheimer Schlüssel zum API-Key", placeholder: "z.B. kL7m...pQ2r" },
  { key: "TELEGRAM_TOKEN", label: "Telegram Bot-Token", hint: "Für Benachrichtigungen und AI-Chat", placeholder: "z.B. 123456:ABC-DEF..." },
  { key: "TELEGRAM_CHAT_ID", label: "Telegram Chat-ID", hint: "Deine Chat-ID für Alerts und Befehle", placeholder: "z.B. 987654321" },
  { key: "XAI_API_KEY", label: "xAI API-Key (Grok)", hint: "Für KI-gestützte Telegram-Antworten und Analysen", placeholder: "xai-..." },
];

function SecretsSektion() {
  const [meta, setMeta] = useState<Record<string, { set: boolean; updatedAt: string }>>({});
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<"idle" | "saved" | "error">("idle");

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/secrets", { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        setMeta(data.secrets || {});
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleChange = (key: string, val: string) => {
    setValues((prev) => ({ ...prev, [key]: val }));
    setStatus("idle");
  };

  const save = async () => {
    const toSave: Record<string, string> = {};
    for (const [k, v] of Object.entries(values)) {
      if (v.trim() !== "") toSave[k] = v;
    }
    if (Object.keys(toSave).length === 0) return;

    setSaving(true);
    try {
      const res = await fetch("/api/secrets", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ secrets: toSave }),
      });
      if (res.ok) {
        setStatus("saved");
        setValues({});
        await load();
        setTimeout(() => setStatus("idle"), 3000);
      } else {
        setStatus("error");
      }
    } catch {
      setStatus("error");
    }
    setSaving(false);
  };

  const hasChanges = Object.values(values).some((v) => v.trim() !== "");

  return (
    <div className="card card-hover p-5 sm:p-6 transition-all">
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{
            background: "var(--warn-bg)",
            border: "1px solid color-mix(in srgb, var(--warn) 15%, transparent)",
          }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--warn)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
              <path d="M7 11V7a5 5 0 0110 0v4" />
            </svg>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">API-Schlüssel & Secrets</h3>
            <p className="text-[11px] text-[var(--text-tertiary)]">Werden in der Datenbank gespeichert — der Pi lädt sie beim Start</p>
          </div>
        </div>
        {hasChanges && (
          <button
            onClick={save}
            disabled={saving}
            className="px-4 py-2 rounded-xl text-[11px] font-semibold transition-all active:scale-95"
            style={{
              background: status === "saved" ? "var(--up-bg-strong)" : status === "error" ? "var(--down-bg-strong)" : "var(--warn-bg)",
              color: status === "saved" ? "var(--up)" : status === "error" ? "var(--down)" : "var(--warn)",
              border: `1px solid ${status === "saved" ? "var(--up)" : status === "error" ? "var(--down)" : "var(--warn)"}20`,
            }}
          >
            {saving ? "Speichere..." : status === "saved" ? "Gespeichert" : status === "error" ? "Fehler" : "Secrets speichern"}
          </button>
        )}
      </div>

      <div className="space-y-4">
        {SECRET_FIELDS.map((f) => {
          const m = meta[f.key];
          return (
            <div key={f.key} className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="block text-[10px] text-[var(--text-tertiary)] uppercase tracking-[0.1em] font-medium">{f.label}</label>
                {m?.set && (
                  <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-md bg-[var(--up-bg)] text-[var(--up)]">
                    Gesetzt
                  </span>
                )}
              </div>
              <input
                type="password"
                value={values[f.key] ?? ""}
                onChange={(e) => handleChange(f.key, e.target.value)}
                placeholder={m?.set ? "••••••••  (neuen Wert eingeben zum Ändern)" : f.placeholder}
                className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-xl px-3.5 py-2.5 text-sm font-mono text-[var(--text-primary)] placeholder:text-[var(--text-quaternary)] focus:border-[var(--warn)] focus:outline-none focus:ring-1 focus:ring-[var(--warn)]/20 transition-all"
              />
              {f.hint && <p className="text-[10px] text-[var(--text-tertiary)]">{f.hint}</p>}
            </div>
          );
        })}
      </div>

      <div className="mt-4 bg-[var(--bg-secondary)] rounded-xl p-3.5 border border-[var(--border-subtle)]">
        <p className="text-[10px] text-[var(--text-tertiary)] leading-relaxed">
          Secrets werden verschlüsselt in der Neon-Datenbank gespeichert. Der Pi lädt sie automatisch beim Start
          und setzt sie als Umgebungsvariablen. So brauchst du auf dem Pi nur <span className="font-mono text-[var(--text-secondary)]">NEON_DATABASE_URL</span> in der .env Datei.
        </p>
      </div>
    </div>
  );
}

/* ---- Install Guide ---- */

const INSTALL_STEPS = [
  {
    nr: 1,
    titel: "Raspberry Pi einrichten",
    beschreibung: "Per SSH auf den Pi verbinden, Repository klonen und Setup ausfuehren:",
    code: `ssh pi@raspberrypi\nsudo apt-get update && sudo apt-get install -y git\ngit clone https://github.com/wakemaster88/richbot.git ~/richbot\ncd ~/richbot\nsudo bash scripts/setup_pi.sh`,
  },
  {
    nr: 2,
    titel: "Umgebungsvariablen konfigurieren",
    beschreibung: "Nur eine Variable nötig — API-Keys werden über das Dashboard oben gesetzt:",
    code: `# .env auf dem Pi — nur diese 3 Zeilen:\nNEON_DATABASE_URL=postgresql://...deine-neon-url...\nCLOUD_ENABLED=true\nCLOUD_BOT_ID=richbot-pi\n\n# Binance & Telegram Keys werden automatisch\n# aus der Datenbank geladen (siehe "API-Schlüssel" oben)`,
  },
  {
    nr: 3,
    titel: "Bot starten",
    beschreibung: "Manuell oder als SystemD-Service für Auto-Start:",
    code: `# Manuell starten\ncd ~/richbot && source venv/bin/activate\npython main.py --config config_pi.json\n\n# Oder als Service (empfohlen)\nsudo systemctl enable richbot\nsudo systemctl start richbot\nsudo journalctl -u richbot -f  # Logs anzeigen`,
  },
];

/* ---- Raspberry Pi Section ---- */

function RaspberryPiSektion() {
  const [pi, setPi] = useState<PiStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [showGuide, setShowGuide] = useState(false);

  const loadPi = useCallback(async () => {
    try {
      const res = await fetch("/api/pi", { cache: "no-store" });
      if (res.ok) {
        setPi(await res.json());
      } else {
        setPi({ connected: false, system: null });
      }
    } catch {
      setPi({ connected: false, system: null });
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadPi();
    const iv = setInterval(loadPi, 15000);
    return () => clearInterval(iv);
  }, [loadPi]);

  const tempColor = useMemo(() => {
    if (!pi?.system?.cpu_temp) return "var(--cyan)";
    if (pi.system.cpu_temp >= 75) return "var(--down)";
    if (pi.system.cpu_temp >= 60) return "var(--warn)";
    return "var(--up)";
  }, [pi?.system?.cpu_temp]);

  if (loading) {
    return (
      <div className="card p-6">
        <div className="flex items-center gap-3">
          <div className="w-5 h-5 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-[var(--text-secondary)]">Raspberry Pi Status laden...</span>
        </div>
      </div>
    );
  }

  const sys = pi?.system;
  const isOnline = pi?.connected;

  return (
    <div className="space-y-4">
      {/* Status Card */}
      <div className="card card-hover p-5 sm:p-6 transition-all">
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{
              background: isOnline ? "var(--up-bg)" : "var(--down-bg)",
              border: `1px solid ${isOnline ? "var(--up)" : "var(--down)"}15`,
            }}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={isOnline ? "var(--up)" : "var(--down)"} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="3" width="20" height="14" rx="2" />
                <path d="M8 21h8M12 17v4" />
              </svg>
            </div>
            <div>
              <h3 className="text-sm font-semibold text-[var(--text-primary)] flex items-center gap-2">
                Raspberry Pi
                <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                  isOnline
                    ? "bg-[var(--up-bg-strong)] text-[var(--up)]"
                    : "bg-[var(--down-bg-strong)] text-[var(--down)]"
                }`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${isOnline ? "bg-[var(--up)] animate-pulse" : "bg-[var(--down)]"}`} />
                  {isOnline ? "Online" : "Offline"}
                </span>
              </h3>
              <p className="text-[11px] text-[var(--text-tertiary)]">
                {isOnline && sys?.hostname ? sys.hostname : "Nicht verbunden"}
                {isOnline && sys?.arch ? ` — ${sys.arch}` : ""}
                {isOnline && pi?.lastSeen ? ` — Zuletzt: ${new Date(pi.lastSeen).toLocaleString("de-DE")}` : ""}
              </p>
            </div>
          </div>
          {isOnline && pi?.uptime != null && (
            <div className="hidden sm:block text-right">
              <p className="text-[10px] text-[var(--text-tertiary)] uppercase tracking-wider">Laufzeit</p>
              <p className="text-sm font-mono font-bold text-[var(--text-primary)]">{formatUptime(pi.uptime)}</p>
            </div>
          )}
        </div>

        {isOnline && sys ? (
          <>
            {/* Public IP Banner */}
            {sys.public_ip && (
              <div className="flex items-center gap-3 p-3 mb-5 rounded-lg border"
                style={{ background: "var(--accent-bg)", borderColor: "var(--accent)20" }}>
                <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
                  style={{ background: "var(--accent)15" }}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                  </svg>
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-[10px] uppercase tracking-wider text-[var(--text-tertiary)] mb-0.5">
                    Oeffentliche IP — fuer Binance API Whitelist
                  </p>
                  <p className="text-sm font-mono font-bold text-[var(--text-primary)] select-all">
                    {sys.public_ip}
                  </p>
                </div>
                <button
                  onClick={() => { navigator.clipboard.writeText(sys.public_ip!); }}
                  className="px-3 py-1.5 text-[11px] font-semibold rounded-md transition-colors shrink-0"
                  style={{ background: "var(--accent)", color: "var(--bg-primary)" }}
                  title="IP kopieren"
                >
                  Kopieren
                </button>
              </div>
            )}

            {/* Quick Stats */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
              <PiStatKarte
                icon="🌡️"
                label="Temperatur"
                wert={sys.cpu_temp ?? "—"}
                einheit="°C"
              />
              <PiStatKarte
                icon="⚡"
                label="CPU"
                wert={sys.cpu_percent ?? "—"}
                einheit="%"
              />
              <PiStatKarte
                icon="🧠"
                label="RAM"
                wert={sys.ram_used_mb ?? "—"}
                einheit={`/ ${sys.ram_total_mb ?? "?"} MB`}
              />
              <PiStatKarte
                icon="💾"
                label="Speicher"
                wert={sys.disk_used_gb ?? "—"}
                einheit={`/ ${sys.disk_total_gb ?? "?"} GB`}
              />
            </div>

            {/* Gauges */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-5 mb-5">
              <Gauge
                icon="🌡️" label="CPU Temperatur"
                value={sys.cpu_temp ?? 0} max={85} unit="°C"
                color={tempColor}
              />
              <Gauge
                icon="🧠" label="RAM Auslastung"
                value={sys.ram_percent ?? 0} max={100} unit="%"
                color="var(--cyan)"
              />
              <Gauge
                icon="💾" label="Speicher"
                value={sys.disk_percent ?? 0} max={100} unit="%"
                color="var(--accent)"
              />
            </div>

            {/* System Load */}
            <div className="bg-[var(--bg-secondary)] rounded-xl p-4 border border-[var(--border-subtle)]">
              <p className="text-[10px] text-[var(--text-tertiary)] uppercase tracking-[0.1em] font-medium mb-3">System-Last (Load Average)</p>
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <p className="text-[10px] text-[var(--text-quaternary)] mb-0.5">1 Min</p>
                  <p className="text-sm font-mono font-semibold text-[var(--text-primary)]">{sys.load_1m?.toFixed(2) ?? "—"}</p>
                </div>
                <div>
                  <p className="text-[10px] text-[var(--text-quaternary)] mb-0.5">5 Min</p>
                  <p className="text-sm font-mono font-semibold text-[var(--text-primary)]">{sys.load_5m?.toFixed(2) ?? "—"}</p>
                </div>
                <div>
                  <p className="text-[10px] text-[var(--text-quaternary)] mb-0.5">15 Min</p>
                  <p className="text-sm font-mono font-semibold text-[var(--text-primary)]">{sys.load_15m?.toFixed(2) ?? "—"}</p>
                </div>
              </div>
              {sys.python && (
                <p className="text-[10px] text-[var(--text-quaternary)] mt-3 border-t border-[var(--border-subtle)] pt-3">
                  Python {sys.python} — Bot-Prozess: {sys.rss_kb ? `${Math.round(sys.rss_kb / 1024)} MB RSS` : "—"}
                </p>
              )}
            </div>
          </>
        ) : (
          <div className="bg-[var(--bg-secondary)] rounded-xl p-5 border border-[var(--border-subtle)] text-center">
            <p className="text-sm text-[var(--text-secondary)] mb-1">Kein Raspberry Pi verbunden</p>
            <p className="text-[11px] text-[var(--text-tertiary)]">
              Folge der Installationsanleitung unten, um deinen Pi mit dem Dashboard zu verbinden.
            </p>
          </div>
        )}
      </div>

      {/* Secrets */}
      <SecretsSektion />

      {/* Installation Guide */}
      <div className="card card-hover p-5 sm:p-6 transition-all">
        <button
          type="button"
          onClick={() => setShowGuide(!showGuide)}
          className="w-full flex items-center justify-between"
        >
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{
              background: "var(--accent-bg)",
              border: "1px solid color-mix(in srgb, var(--accent) 15%, transparent)",
            }}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 19.5A2.5 2.5 0 016.5 17H20" />
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" />
              </svg>
            </div>
            <div className="text-left">
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">Installationsanleitung</h3>
              <p className="text-[11px] text-[var(--text-tertiary)]">Schritt-für-Schritt: Pi einrichten & verbinden</p>
            </div>
          </div>
          <svg
            width="16" height="16" viewBox="0 0 24 24" fill="none"
            stroke="var(--text-tertiary)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
            className={`transition-transform duration-300 ${showGuide ? "rotate-180" : ""}`}
          >
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>

        {showGuide && (
          <div className="mt-5 space-y-5 border-t border-[var(--border-subtle)] pt-5">
            {/* Prerequisites */}
            <div className="bg-[var(--bg-secondary)] rounded-xl p-4 border border-[var(--border-subtle)]">
              <p className="text-[10px] text-[var(--text-tertiary)] uppercase tracking-[0.1em] font-medium mb-2">Voraussetzungen</p>
              <ul className="space-y-1.5 text-[12px] text-[var(--text-secondary)]">
                <li className="flex items-center gap-2"><span className="text-[var(--accent)]">›</span> Raspberry Pi 4/5 mit Raspberry Pi OS (64-bit empfohlen)</li>
                <li className="flex items-center gap-2"><span className="text-[var(--accent)]">›</span> SSH-Zugang zum Pi aktiviert</li>
                <li className="flex items-center gap-2"><span className="text-[var(--accent)]">›</span> Internetverbindung (WLAN oder Ethernet)</li>
                <li className="flex items-center gap-2"><span className="text-[var(--accent)]">›</span> Binance API-Key (Spot-Trading aktiviert)</li>
                <li className="flex items-center gap-2"><span className="text-[var(--accent)]">›</span> Neon.tech Account (kostenlos)</li>
                <li className="flex items-center gap-2"><span className="text-[var(--accent)]">›</span> Vercel Account (kostenlos)</li>
              </ul>
            </div>

            {/* Steps */}
            {INSTALL_STEPS.map((step) => (
              <div key={step.nr} className="relative pl-8">
                <div className="absolute left-0 top-0.5 w-5 h-5 rounded-lg flex items-center justify-center text-[10px] font-bold" style={{
                  background: "var(--accent-bg)",
                  color: "var(--accent)",
                  border: "1px solid color-mix(in srgb, var(--accent) 20%, transparent)",
                }}>
                  {step.nr}
                </div>
                <h4 className="text-[13px] font-semibold text-[var(--text-primary)] mb-1">{step.titel}</h4>
                <p className="text-[11px] text-[var(--text-tertiary)] mb-2">{step.beschreibung}</p>
                <pre className="bg-[var(--bg-primary)] border border-[var(--border)] rounded-xl p-3.5 text-[11px] font-mono text-[var(--text-secondary)] overflow-x-auto leading-relaxed whitespace-pre-wrap">{step.code}</pre>
              </div>
            ))}

            {/* Success check */}
            <div className="bg-[var(--up-bg)] rounded-xl p-4 border" style={{ borderColor: "color-mix(in srgb, var(--up) 15%, transparent)" }}>
              <p className="text-[12px] font-semibold text-[var(--up)] mb-1">Verbindung prüfen</p>
              <p className="text-[11px] text-[var(--text-secondary)]">
                Sobald der Bot auf dem Pi läuft, wechselt der Status oben zu &quot;Online&quot;.
                Die Systemdaten werden alle 15 Sekunden aktualisiert.
                Im Dashboard siehst du dann Live-Daten statt Demo-Daten.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---- Helpers ---- */

function set<T>(obj: T, path: string, value: unknown): T {
  const clone = JSON.parse(JSON.stringify(obj));
  const parts = path.split(".");
  let cur: Record<string, unknown> = clone;
  for (let i = 0; i < parts.length - 1; i++) {
    if (!(parts[i] in cur)) cur[parts[i]] = {};
    cur = cur[parts[i]] as Record<string, unknown>;
  }
  cur[parts[parts.length - 1]] = value;
  return clone;
}

// -- Page --

export default function SettingsPage() {
  const [config, setConfig] = useState<BotConfigData | null>(null);
  const [loading, setLoading] = useState(true);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/config", { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        if (data.config) {
          setConfig(data.config);
          setUpdatedAt(data.updatedAt);
        } else {
          setConfig(structuredClone(DEFAULTS));
        }
      } else {
        setConfig(structuredClone(DEFAULTS));
      }
    } catch {
      setConfig(structuredClone(DEFAULTS));
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const update = (path: string, value: unknown) => {
    if (!config) return;
    setConfig(set(config, path, value));
    setSaveStatus("idle");
  };

  const save = async () => {
    if (!config) return;
    setSaveStatus("saving");
    try {
      const res = await fetch("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config }),
      });
      if (res.ok) {
        const data = await res.json();
        setUpdatedAt(data.updatedAt);
        setSaveStatus("saved");
        setTimeout(() => setSaveStatus("idle"), 3000);
      } else {
        setSaveStatus("error");
      }
    } catch {
      setSaveStatus("error");
    }
  };

  if (loading) {
    return (
      <div className="min-h-[85vh] flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-8 h-8 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-[var(--text-secondary)]">Lade Konfiguration...</p>
        </div>
      </div>
    );
  }

  if (!config) return null;

  return (
    <div className="max-w-4xl mx-auto px-4 py-5 sm:px-6 pb-20">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-xl font-bold">Einstellungen</h1>
          {updatedAt && (
            <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">
              Zuletzt synchronisiert: {new Date(updatedAt).toLocaleString("de-DE")}
            </p>
          )}
        </div>
        <button
          onClick={save}
          disabled={saveStatus === "saving"}
          className="px-5 py-2.5 rounded-xl text-xs font-semibold transition-all active:scale-95"
          style={{
            background: saveStatus === "saved" ? "var(--up-bg-strong)" : saveStatus === "error" ? "var(--down-bg-strong)" : "var(--accent-bg)",
            color: saveStatus === "saved" ? "var(--up)" : saveStatus === "error" ? "var(--down)" : "var(--accent)",
            border: `1px solid ${saveStatus === "saved" ? "var(--up)" : saveStatus === "error" ? "var(--down)" : "var(--accent)"}20`,
          }}
        >
          {saveStatus === "saving" ? "Speichere..." :
           saveStatus === "saved" ? "Gespeichert & an Pi gesendet" :
           saveStatus === "error" ? "Fehler — Erneut versuchen" :
           "Speichern & Anwenden"}
        </button>
      </div>

      <div className="card px-4 py-3 mb-6 text-[11px] text-[var(--text-tertiary)] leading-relaxed">
        Anderungen werden in der Datenbank gespeichert und als Befehl an den Raspberry Pi gesendet.
        Der Bot ubernimmt sie beim nachsten Abruf (~5 Sekunden). Sensible Daten (API-Schlussel, Tokens) konnen hier nicht bearbeitet werden.
      </div>

      <div className="space-y-4 sm:space-y-5">
        {/* Raspberry Pi */}
        <RaspberryPiSektion />

        <div className="flex items-center gap-3 pt-2">
          <div className="h-px flex-1 bg-[var(--border)]" />
          <span className="text-[10px] text-[var(--text-quaternary)] uppercase tracking-[0.15em] font-medium">Bot-Konfiguration</span>
          <div className="h-px flex-1 bg-[var(--border)]" />
        </div>

        {/* Handelspaare */}
        <Sektion titel="Handelspaare" beschreibung="Aktive Trading-Paare auf Binance">
          <Tags
            label="Paare"
            value={config.pairs}
            onChange={(v) => update("pairs", v)}
            hint="Binance-Format, z.B. BTC/USDT, ETH/USDT, SOL/USDT"
          />
        </Sektion>

        {/* Grid */}
        <Sektion titel="Grid-Einstellungen" beschreibung="Parameter fur das Grid-Trading">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Zahl label="Anzahl Grid-Level" value={config.grid?.grid_count} onChange={(v) => update("grid.grid_count", v)} min={4} max={100} hint="Mehr Level = engeres Grid, mehr Trades" />
            <Zahl label="Abstand %" value={config.grid?.spacing_percent} onChange={(v) => update("grid.spacing_percent", v)} step={0.1} min={0.1} max={5} hint="Prozent-Abstand zwischen den Leveln" />
            <Zahl label="Betrag pro Order (BTC)" value={config.grid?.amount_per_order} onChange={(v) => update("grid.amount_per_order", v)} step={0.00001} min={0.00001} hint="Menge in BTC pro Grid-Order (z.B. 0.0001 = ~$7 bei $70k)" />
            <Zahl label="Range-Multiplikator" value={config.grid?.range_multiplier} onChange={(v) => update("grid.range_multiplier", v)} step={0.1} min={0.5} max={5} />
            <Zahl label="Trail-Schwelle %" value={config.grid?.trail_trigger_percent} onChange={(v) => update("grid.trail_trigger_percent", v)} step={0.1} min={0.5} max={10} hint="Ausbruch-Schwelle fur Grid-Verschiebung" />
          </div>
          <Schalter label="Infinity-Modus" value={config.grid?.infinity_mode} onChange={(v) => update("grid.infinity_mode", v)} hint="Grid verschiebt sich bei Ausbruch statt zu stoppen" />
        </Sektion>

        {/* ATR */}
        <Sektion titel="ATR-Range" beschreibung="Average True Range berechnet die Grid-Grenzen">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <Zahl label="Periode" value={config.atr?.period} onChange={(v) => update("atr.period", v)} min={5} max={50} />
            <Auswahl label="Zeitrahmen" value={config.atr?.timeframe} options={["5m", "15m", "30m", "1h", "4h", "1d"]} onChange={(v) => update("atr.timeframe", v)} />
            <Zahl label="Multiplikator" value={config.atr?.multiplier} onChange={(v) => update("atr.multiplier", v)} step={0.1} min={0.5} max={5} />
          </div>
        </Sektion>

        {/* Risiko */}
        <Sektion titel="Risikomanagement" beschreibung="Positionsgrosse, Drawdown-Limits und Trailing Stops">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Zahl label="Kelly-Faktor" value={config.risk?.kelly_fraction} onChange={(v) => update("risk.kelly_fraction", v)} step={0.05} min={0.05} max={1} hint="0.25 = Viertel-Kelly (konservativ)" />
            <Zahl label="Max. Drawdown %" value={config.risk?.max_drawdown_percent} onChange={(v) => update("risk.max_drawdown_percent", v)} step={0.5} min={1} max={50} hint="Bot pausiert bei diesem Drawdown" />
            <Zahl label="Trailing Stop %" value={config.risk?.trailing_stop_percent} onChange={(v) => update("risk.trailing_stop_percent", v)} step={0.1} min={0.1} max={10} />
            <Zahl label="Max. Position %" value={config.risk?.max_position_percent} onChange={(v) => update("risk.max_position_percent", v)} step={1} min={5} max={100} hint="Maximaler %-Anteil des Kapitals" />
            <Zahl label="Min. Orderbetrag" value={config.risk?.min_order_amount} onChange={(v) => update("risk.min_order_amount", v)} step={0.0001} min={0.0001} />
          </div>
          <Schalter label="Volatilitats-Skalierung" value={config.risk?.volatility_scaling} onChange={(v) => update("risk.volatility_scaling", v)} hint="Ordergrosse passt sich an Volatilitat an" />
        </Sektion>

        {/* ML */}
        <Sektion titel="LSTM-Vorhersage" beschreibung="Neuronales Netz fur Range-Prognosen">
          <Schalter label="ML aktiviert" value={config.ml?.enabled} onChange={(v) => update("ml.enabled", v)} />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Zahl label="Konfidenz-Schwelle" value={config.ml?.confidence_threshold} onChange={(v) => update("ml.confidence_threshold", v)} step={0.05} min={0.5} max={0.99} hint="Mindest-Konfidenz fur Aktionen" />
            <Zahl label="Vorhersage-Intervall (Min.)" value={config.ml?.prediction_interval_minutes} onChange={(v) => update("ml.prediction_interval_minutes", v)} min={5} max={120} />
            <Zahl label="Neutraining-Intervall (Std.)" value={config.ml?.retrain_interval_hours} onChange={(v) => update("ml.retrain_interval_hours", v)} min={1} max={168} />
            <Zahl label="Lookback (Tage)" value={config.ml?.lookback_days} onChange={(v) => update("ml.lookback_days", v)} min={14} max={365} />
          </div>
          <Tags
            label="Zeitrahmen"
            value={config.ml?.timeframes}
            onChange={(v) => update("ml.timeframes", v.map((t) => t.toLowerCase()))}
            hint="z.B. 1h, 4h"
            placeholder="1h"
          />
        </Sektion>

        {/* Telegram */}
        <Sektion titel="Telegram-Benachrichtigungen" beschreibung="Alerts und Berichte per Telegram">
          <Schalter label="Telegram aktiviert" value={config.telegram?.enabled} onChange={(v) => update("telegram.enabled", v)} />
          <div className="border-t border-[var(--border-subtle)] pt-3 space-y-1">
            <Schalter label="Alert bei Order-Fill" value={config.telegram?.alert_on_fill} onChange={(v) => update("telegram.alert_on_fill", v)} />
            <Schalter label="Alert bei Range-Verschiebung" value={config.telegram?.alert_on_range_shift} onChange={(v) => update("telegram.alert_on_range_shift", v)} />
            <Schalter label="Alert bei Drawdown" value={config.telegram?.alert_on_drawdown} onChange={(v) => update("telegram.alert_on_drawdown", v)} />
            <Schalter label="Taglicher Bericht" value={config.telegram?.daily_report} onChange={(v) => update("telegram.daily_report", v)} />
          </div>
        </Sektion>

        {/* WebSocket */}
        <Sektion titel="WebSocket" beschreibung="Echtzeit-Verbindung zur Borse">
          <Schalter label="WebSocket aktiviert" value={config.websocket?.enabled} onChange={(v) => update("websocket.enabled", v)} />
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <Zahl label="Reconnect-Verzog. (Sek.)" value={config.websocket?.reconnect_delay} onChange={(v) => update("websocket.reconnect_delay", v)} min={1} max={30} />
            <Zahl label="Max. Reconnect-Versuche" value={config.websocket?.max_reconnect_attempts} onChange={(v) => update("websocket.max_reconnect_attempts", v)} min={5} max={200} />
            <Zahl label="Ping-Intervall (Sek.)" value={config.websocket?.ping_interval} onChange={(v) => update("websocket.ping_interval", v)} min={10} max={120} />
          </div>
        </Sektion>

        {/* Cloud */}
        <Sektion titel="Cloud-Synchronisation" beschreibung="Verbindung zwischen Pi und Vercel Dashboard">
          <Schalter label="Cloud-Sync aktiviert" value={config.cloud?.enabled} onChange={(v) => update("cloud.enabled", v)} />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Zahl label="Heartbeat-Intervall (Sek.)" value={config.cloud?.heartbeat_interval} onChange={(v) => update("cloud.heartbeat_interval", v)} min={10} max={120} hint="Wie oft der Pi ein Lebenszeichen sendet" />
            <Zahl label="Befehl-Abruf (Sek.)" value={config.cloud?.command_poll_interval} onChange={(v) => update("cloud.command_poll_interval", v)} min={2} max={30} hint="Wie oft der Pi auf neue Befehle pruft" />
          </div>
          <Schalter label="Trades synchronisieren" value={config.cloud?.sync_trades} onChange={(v) => update("cloud.sync_trades", v)} />
          <Schalter label="Kapital synchronisieren" value={config.cloud?.sync_equity} onChange={(v) => update("cloud.sync_equity", v)} />
        </Sektion>
      </div>
    </div>
  );
}
