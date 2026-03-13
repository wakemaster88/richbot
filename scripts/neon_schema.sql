-- Neon Postgres schema for RichBot cloud sync.
-- This matches the Prisma schema in web/prisma/schema.prisma.
-- Tables are also auto-created by the Pi bot on first connect.
-- Run this manually if you prefer: psql $DATABASE_URL < scripts/neon_schema.sql

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
    order_id TEXT,
    fill_price DOUBLE PRECISION,
    slippage_bps DOUBLE PRECISION,
    is_maker BOOLEAN
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
