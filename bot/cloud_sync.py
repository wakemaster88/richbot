"""Cloud sync: bridges Pi bot with Neon Postgres for Vercel dashboard control.

The Pi bot writes heartbeats, trades, equity snapshots to Neon DB.
The Vercel dashboard reads this data and writes commands back.
Both sides share the same Postgres database (Neon serverless).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

from bot.config import CloudConfig

logger = logging.getLogger(__name__)

try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False


class CloudSync:
    """Bidirectional sync between Pi bot and Neon Postgres."""

    def __init__(self, config: CloudConfig):
        self.config = config
        self.bot_id = config.bot_id
        self._pool = None
        self._running = False
        self._start_time = time.time()
        self._command_handlers: dict = {}
        self._tasks: list[asyncio.Task] = []
        self._status = "starting"
        self._pairs: list[str] = []
        self._metrics: dict = {}

    @property
    def connected(self) -> bool:
        return self._pool is not None and self._running

    async def start(self):
        if not HAS_ASYNCPG:
            logger.warning("asyncpg not installed — cloud sync disabled")
            return
        if not self.config.enabled or not self.config.database_url:
            logger.info("Cloud sync disabled (not configured)")
            return

        try:
            self._pool = await asyncpg.create_pool(
                dsn=self.config.database_url,
                min_size=1,
                max_size=3,
                ssl=True,
                command_timeout=15,
            )
            await self._ensure_schema()
            self._running = True
            self._tasks = [
                asyncio.create_task(self._heartbeat_loop()),
                asyncio.create_task(self._command_loop()),
            ]
            logger.info("Cloud sync connected (bot_id=%s)", self.bot_id)
        except Exception as e:
            logger.error("Cloud sync start failed: %s", e)

    async def fetch_env(self, force: bool = False) -> dict[str, str]:
        """Fetch secrets from Neon and inject into os.environ. Returns loaded keys."""
        if not self._pool:
            return {}
        import os
        loaded = {}
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT key, value FROM bot_secrets WHERE bot_id = $1",
                    self.bot_id,
                )
                for row in rows:
                    k, v = row["key"], row["value"]
                    if v:
                        v = v.strip()
                    if v and (force or not os.environ.get(k)):
                        os.environ[k] = v
                        loaded[k] = "***"
                if loaded:
                    logger.info("Loaded %d secrets from cloud: %s", len(loaded), list(loaded.keys()))
        except Exception as e:
            logger.warning("Failed to fetch secrets: %s", e)
        return loaded

    async def stop(self):
        self._running = False
        self._status = "stopped"
        try:
            await self.send_heartbeat()
        except Exception:
            pass
        for task in self._tasks:
            task.cancel()
        if self._pool:
            await self._pool.close()
            self._pool = None
        logger.info("Cloud sync stopped")

    def update_status(self, status: str, pairs: list[str], metrics: dict,
                       wallet: dict | None = None):
        """Called by MultiPairBot to push current state for next heartbeat."""
        self._status = status
        self._pairs = pairs
        self._metrics = metrics
        if wallet is not None:
            self._metrics["__wallet__"] = wallet

    def on_command(self, command_type: str, handler):
        """Register a handler for a command type (sync or async callable)."""
        self._command_handlers[command_type] = handler

    async def send_heartbeat(self):
        if not self._pool:
            return
        try:
            uptime = int(time.time() - self._start_time)
            mem = await asyncio.to_thread(self._get_system_info)
            git_ver = mem.get("git_commit", "?")
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO heartbeats (id, bot_id, status, pairs, uptime, memory, metrics) "
                    "VALUES ($1, $2, $3, $4::jsonb, $5, $6::jsonb, $7::jsonb)",
                    _uid(), self.bot_id, self._status,
                    json.dumps(self._pairs), uptime,
                    json.dumps(mem), json.dumps(self._metrics),
                )
                await conn.execute(
                    "INSERT INTO bot_statuses (id, bot_id, status, last_heartbeat, pairs, pair_statuses, started_at, version) "
                    "VALUES ($1, $2, $3, NOW(), $4::jsonb, $5::jsonb, to_timestamp($6), $7) "
                    "ON CONFLICT (bot_id) DO UPDATE SET "
                    "status=EXCLUDED.status, last_heartbeat=NOW(), "
                    "pairs=EXCLUDED.pairs, pair_statuses=EXCLUDED.pair_statuses, "
                    "version=EXCLUDED.version",
                    _uid(), self.bot_id, self._status,
                    json.dumps(self._pairs), json.dumps(self._metrics),
                    self._start_time, git_ver,
                )
        except Exception as e:
            logger.warning("Heartbeat failed: %s", e)

    async def sync_trade(self, trade):
        """Sync a TradeRecord to Neon."""
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO trades (id, bot_id, timestamp, pair, side, price, amount, fee, pnl, grid_level, order_id) "
                    "VALUES ($1, $2, to_timestamp($3), $4, $5, $6, $7, $8, $9, $10, $11)",
                    _uid(), self.bot_id, trade.timestamp,
                    trade.pair, trade.side, trade.price, trade.amount,
                    trade.fee, trade.pnl, trade.grid_level, trade.order_id,
                )
        except Exception as e:
            logger.warning("Trade sync failed: %s", e)

    async def sync_equity(self, pair: str, equity: float, unrealized_pnl: float = 0.0):
        """Sync an equity snapshot to Neon."""
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO equity_snapshots (id, bot_id, pair, equity, unrealized_pnl) "
                    "VALUES ($1, $2, $3, $4, $5)",
                    _uid(), self.bot_id, pair, equity, unrealized_pnl,
                )
        except Exception as e:
            logger.warning("Equity sync failed: %s", e)

    async def sync_config(self, config_dict: dict):
        """Upsert bot config to Neon."""
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO bot_configs (id, bot_id, config) VALUES ($1, $2, $3::jsonb) "
                    "ON CONFLICT (bot_id) DO UPDATE SET config=EXCLUDED.config, updated_at=NOW()",
                    _uid(), self.bot_id, json.dumps(config_dict),
                )
        except Exception as e:
            logger.warning("Config sync failed: %s", e)

    async def fetch_config_update(self) -> dict | None:
        """Check if config was updated from the dashboard."""
        if not self._pool:
            return None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT config, updated_at FROM bot_configs WHERE bot_id=$1",
                    self.bot_id,
                )
                if row:
                    return json.loads(row["config"])
        except Exception as e:
            logger.warning("Config fetch failed: %s", e)
        return None

    async def save_state(self, state: dict):
        """Persist bot state for recovery after restart."""
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO bot_state (bot_id, grid_state, trailing_tps, last_prices, updated_at) "
                    "VALUES ($1, $2::jsonb, $3::jsonb, $4::jsonb, NOW()) "
                    "ON CONFLICT (bot_id) DO UPDATE SET "
                    "grid_state=EXCLUDED.grid_state, trailing_tps=EXCLUDED.trailing_tps, "
                    "last_prices=EXCLUDED.last_prices, updated_at=NOW()",
                    self.bot_id,
                    json.dumps(state.get("grid_state", {})),
                    json.dumps(state.get("trailing_tps", [])),
                    json.dumps(state.get("last_prices", {})),
                )
        except Exception as e:
            logger.debug("State save failed: %s", e)

    async def load_state(self, max_age_sec: int = 600) -> dict | None:
        """Load persisted state if younger than max_age_sec."""
        if not self._pool:
            return None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT grid_state, trailing_tps, last_prices, updated_at "
                    "FROM bot_state WHERE bot_id=$1",
                    self.bot_id,
                )
                if not row:
                    return None
                from datetime import datetime, timezone
                updated = row["updated_at"]
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - updated).total_seconds()
                if age > max_age_sec:
                    logger.info("Saved state too old (%.0fs > %ds), clean start", age, max_age_sec)
                    return None
                logger.info("Loaded saved state (age: %.0fs)", age)
                return {
                    "grid_state": json.loads(row["grid_state"]) if row["grid_state"] else {},
                    "trailing_tps": json.loads(row["trailing_tps"]) if row["trailing_tps"] else [],
                    "last_prices": json.loads(row["last_prices"]) if row["last_prices"] else {},
                }
        except Exception as e:
            logger.warning("State load failed: %s", e)
            return None

    async def log_event(self, category: str, message: str,
                        detail: dict | None = None, level: str = "info"):
        """Log a structured event to the database for the dashboard activity feed."""
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO bot_events (id, bot_id, level, category, message, detail) "
                    "VALUES ($1, $2, $3, $4, $5, $6::jsonb)",
                    _uid(), self.bot_id, level, category, message,
                    json.dumps(detail) if detail else None,
                )
        except Exception:
            pass

    # -- Internal loops --

    async def _heartbeat_loop(self):
        while self._running:
            await self.send_heartbeat()
            await asyncio.sleep(self.config.heartbeat_interval)

    async def _command_loop(self):
        while self._running:
            await self._process_commands()
            await asyncio.sleep(self.config.command_poll_interval)

    async def _process_commands(self):
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, type, payload FROM commands "
                    "WHERE bot_id=$1 AND status='pending' ORDER BY created_at LIMIT 10",
                    self.bot_id,
                )
                for row in rows:
                    cmd_id, cmd_type = row["id"], row["type"]
                    payload = json.loads(row["payload"]) if row["payload"] else {}
                    handler = self._command_handlers.get(cmd_type)

                    if not handler:
                        await conn.execute(
                            "UPDATE commands SET status='failed', processed_at=NOW(), "
                            "result=$1::jsonb WHERE id=$2",
                            json.dumps({"error": f"Unknown: {cmd_type}"}), cmd_id,
                        )
                        continue

                    try:
                        if asyncio.iscoroutinefunction(handler):
                            result = await handler(payload)
                        else:
                            result = handler(payload)
                        await conn.execute(
                            "UPDATE commands SET status='completed', processed_at=NOW(), "
                            "result=$1::jsonb WHERE id=$2",
                            json.dumps(result or {"ok": True}), cmd_id,
                        )
                    except Exception as e:
                        await conn.execute(
                            "UPDATE commands SET status='failed', processed_at=NOW(), "
                            "result=$1::jsonb WHERE id=$2",
                            json.dumps({"error": str(e)}), cmd_id,
                        )

                    logger.info("Command %s [%s] processed", cmd_type, cmd_id[:8])
        except Exception as e:
            logger.warning("Command poll failed: %s", e)

    async def _ensure_schema(self):
        """Create tables if they don't exist (idempotent)."""
        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA_SQL)

    @staticmethod
    def _get_system_info() -> dict:
        info: dict = {}
        try:
            import resource
            ru = resource.getrusage(resource.RUSAGE_SELF)
            info["rss_kb"] = ru.ru_maxrss
        except Exception:
            pass

        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                info["cpu_temp"] = round(int(f.read().strip()) / 1000, 1)
        except Exception:
            pass

        try:
            with open("/proc/meminfo") as f:
                mem = {}
                for line in f:
                    parts = line.split()
                    if parts[0] in ("MemTotal:", "MemAvailable:", "MemFree:", "Buffers:", "Cached:"):
                        mem[parts[0].rstrip(":")] = int(parts[1])
                total = mem.get("MemTotal", 0)
                available = mem.get("MemAvailable", 0)
                info["ram_total_mb"] = round(total / 1024)
                info["ram_used_mb"] = round((total - available) / 1024)
                info["ram_percent"] = round((total - available) / max(total, 1) * 100, 1)
        except Exception:
            pass

        try:
            with open("/proc/loadavg") as f:
                parts = f.read().split()
                info["load_1m"] = float(parts[0])
                info["load_5m"] = float(parts[1])
                info["load_15m"] = float(parts[2])
        except Exception:
            pass

        try:
            with open("/proc/stat") as f:
                line = f.readline()
                vals = [int(x) for x in line.split()[1:]]
                total = sum(vals)
                idle = vals[3]
                info["cpu_percent"] = round((1 - idle / max(total, 1)) * 100, 1)
        except Exception:
            pass

        try:
            import shutil
            usage = shutil.disk_usage("/")
            info["disk_total_gb"] = round(usage.total / (1024 ** 3), 1)
            info["disk_used_gb"] = round(usage.used / (1024 ** 3), 1)
            info["disk_percent"] = round(usage.used / max(usage.total, 1) * 100, 1)
        except Exception:
            pass

        try:
            import platform
            info["hostname"] = platform.node()
            info["arch"] = platform.machine()
            info["python"] = platform.python_version()
        except Exception:
            pass

        try:
            import subprocess
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            if result.returncode == 0:
                info["git_commit"] = result.stdout.strip()
            result2 = subprocess.run(
                ["git", "log", "-1", "--format=%ci"],
                capture_output=True, text=True, timeout=5,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            if result2.returncode == 0:
                info["git_date"] = result2.stdout.strip()
        except Exception:
            pass

        for ip_url in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"):
            try:
                from urllib.request import urlopen, Request
                req = Request(ip_url, headers={"User-Agent": "curl/8.0"})
                ip = urlopen(req, timeout=5).read().decode().strip()
                if ip and len(ip) <= 45:
                    info["public_ip"] = ip
                    break
            except Exception:
                continue

        return info


def _uid() -> str:
    return str(uuid.uuid4())


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS heartbeats (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL,
    pairs JSONB NOT NULL DEFAULT '[]',
    uptime INTEGER NOT NULL DEFAULT 0,
    memory JSONB,
    metrics JSONB
);
CREATE INDEX IF NOT EXISTS idx_hb_bot_ts ON heartbeats(bot_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS commands (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    type TEXT NOT NULL,
    payload JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    result JSONB
);
CREATE INDEX IF NOT EXISTS idx_cmd_bot_status ON commands(bot_id, status);

CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    pair TEXT NOT NULL,
    side TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    fee DOUBLE PRECISION NOT NULL,
    pnl DOUBLE PRECISION NOT NULL,
    grid_level DOUBLE PRECISION,
    order_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_tr_bot_pair_ts ON trades(bot_id, pair, timestamp DESC);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pair TEXT NOT NULL,
    equity DOUBLE PRECISION NOT NULL,
    unrealized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_eq_bot_pair_ts ON equity_snapshots(bot_id, pair, timestamp DESC);

CREATE TABLE IF NOT EXISTS bot_statuses (
    id TEXT PRIMARY KEY,
    bot_id TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL,
    last_heartbeat TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pairs JSONB NOT NULL DEFAULT '[]',
    pair_statuses JSONB NOT NULL DEFAULT '{}',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    version TEXT NOT NULL DEFAULT '2.0'
);

CREATE TABLE IF NOT EXISTS bot_configs (
    id TEXT PRIMARY KEY,
    bot_id TEXT UNIQUE NOT NULL,
    config JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bot_secrets (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(bot_id, key)
);

CREATE TABLE IF NOT EXISTS bot_state (
    bot_id TEXT PRIMARY KEY,
    grid_state JSONB NOT NULL DEFAULT '{}',
    trailing_tps JSONB NOT NULL DEFAULT '[]',
    last_prices JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bot_events (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    level TEXT NOT NULL DEFAULT 'info',
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    detail JSONB
);
CREATE INDEX IF NOT EXISTS idx_bot_events_ts ON bot_events (bot_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_bot_events_cat ON bot_events (bot_id, category, timestamp DESC);
"""
