"""Performance tracking with SQLite persistence and per-pair metrics."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    timestamp: float
    pair: str
    side: str
    price: float
    amount: float
    fee: float
    pnl: float
    grid_level: float
    order_id: str = ""


@dataclass
class PairPerformance:
    pair: str
    total_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    trade_count: int = 0
    buy_count: int = 0
    sell_count: int = 0
    fees_paid: float = 0.0
    peak_equity: float = 0.0
    max_drawdown: float = 0.0
    start_equity: float = 0.0
    current_equity: float = 0.0
    equity_history: list[tuple[float, float]] = field(default_factory=list)
    pnl_history: list[tuple[float, float]] = field(default_factory=list)


class PerformanceTracker:
    """Track, persist and analyze trading performance per pair.

    Pi-optimized: WAL journal for SD card, bounded history buffers,
    batched writes, connection reuse.
    """

    def __init__(self, db_path: str = "data/richbot.db",
                 equity_history_limit: int = 10000,
                 pi_mode: bool = False):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.pair_stats: dict[str, PairPerformance] = {}
        self._equity_limit = 2000 if pi_mode else equity_history_limit
        self._pi_mode = pi_mode
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=-8000")  # 8MB cache
            if self._pi_mode:
                self._conn.execute("PRAGMA journal_size_limit=1048576")  # 1MB WAL limit
                self._conn.execute("PRAGMA cache_size=-2000")  # 2MB cache on Pi
                self._conn.execute("PRAGMA temp_store=MEMORY")
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                pair TEXT,
                side TEXT,
                price REAL,
                amount REAL,
                fee REAL,
                pnl REAL,
                grid_level REAL,
                order_id TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                pair TEXT,
                equity REAL,
                unrealized_pnl REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE,
                report_json TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_equity_pair ON equity_snapshots(pair)")
        conn.commit()

    def _get_pair_stats(self, pair: str) -> PairPerformance:
        if pair not in self.pair_stats:
            self.pair_stats[pair] = PairPerformance(pair=pair)
        return self.pair_stats[pair]

    def record_trade(self, trade: TradeRecord):
        """Record a completed trade."""
        stats = self._get_pair_stats(trade.pair)
        stats.trade_count += 1
        stats.realized_pnl += trade.pnl
        stats.total_pnl = stats.realized_pnl + stats.unrealized_pnl
        stats.fees_paid += trade.fee
        stats.pnl_history.append((trade.timestamp, stats.realized_pnl))

        if trade.side == "buy":
            stats.buy_count += 1
        else:
            stats.sell_count += 1

        conn = self._get_conn()
        conn.execute(
            "INSERT INTO trades (timestamp, pair, side, price, amount, fee, pnl, grid_level, order_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (trade.timestamp, trade.pair, trade.side, trade.price,
             trade.amount, trade.fee, trade.pnl, trade.grid_level, trade.order_id),
        )
        conn.commit()
        logger.info("Trade recorded: %s %s %.6f @ %.2f (PnL: %.4f)", trade.side, trade.pair, trade.amount, trade.price, trade.pnl)

    def update_equity(self, pair: str, equity: float, unrealized_pnl: float = 0.0):
        """Snapshot equity for drawdown tracking."""
        stats = self._get_pair_stats(pair)
        stats.current_equity = equity
        stats.unrealized_pnl = unrealized_pnl
        stats.total_pnl = stats.realized_pnl + unrealized_pnl

        if stats.start_equity == 0:
            stats.start_equity = equity

        if equity > stats.peak_equity:
            stats.peak_equity = equity

        if stats.peak_equity > 0:
            dd = (stats.peak_equity - equity) / stats.peak_equity * 100
            stats.max_drawdown = max(stats.max_drawdown, dd)

        now = time.time()
        stats.equity_history.append((now, equity))
        if len(stats.equity_history) > self._equity_limit:
            stats.equity_history = stats.equity_history[-(self._equity_limit // 2):]

        conn = self._get_conn()
        conn.execute(
            "INSERT INTO equity_snapshots (timestamp, pair, equity, unrealized_pnl) VALUES (?, ?, ?, ?)",
            (now, pair, equity, unrealized_pnl),
        )
        conn.commit()

    def get_sharpe_ratio(self, pair: str, risk_free_rate: float = 0.0) -> float:
        """Annualized Sharpe Ratio from equity curve."""
        stats = self._get_pair_stats(pair)
        if len(stats.equity_history) < 10:
            return 0.0

        equities = np.array([e for _, e in stats.equity_history])
        returns = np.diff(equities) / equities[:-1]

        if len(returns) == 0 or np.std(returns) == 0:
            return 0.0

        periods_per_year = 365 * 24 * 4
        excess_return = np.mean(returns) - risk_free_rate / periods_per_year
        return float(excess_return / np.std(returns) * np.sqrt(periods_per_year))

    def get_annualized_return(self, pair: str) -> float:
        stats = self._get_pair_stats(pair)
        if stats.start_equity == 0 or len(stats.equity_history) < 2:
            return 0.0

        total_return = (stats.current_equity - stats.start_equity) / stats.start_equity
        first_ts = stats.equity_history[0][0]
        elapsed_days = (time.time() - first_ts) / 86400
        if elapsed_days < 1:
            return total_return * 365 * 100

        return float(((1 + total_return) ** (365 / elapsed_days) - 1) * 100)

    def get_max_drawdown(self, pair: str) -> float:
        return self._get_pair_stats(pair).max_drawdown

    def get_summary(self, pair: str) -> dict:
        stats = self._get_pair_stats(pair)
        return {
            "pair": pair,
            "total_pnl": stats.total_pnl,
            "realized_pnl": stats.realized_pnl,
            "unrealized_pnl": stats.unrealized_pnl,
            "trade_count": stats.trade_count,
            "buy_count": stats.buy_count,
            "sell_count": stats.sell_count,
            "fees_paid": stats.fees_paid,
            "max_drawdown_pct": stats.max_drawdown,
            "sharpe_ratio": self.get_sharpe_ratio(pair),
            "annualized_return_pct": self.get_annualized_return(pair),
            "current_equity": stats.current_equity,
        }

    def get_all_summaries(self) -> list[dict]:
        return [self.get_summary(pair) for pair in self.pair_stats]

    def get_trade_history(self, pair: str | None = None, limit: int = 100) -> list[dict]:
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        if pair:
            rows = conn.execute(
                "SELECT * FROM trades WHERE pair = ? ORDER BY timestamp DESC LIMIT ?",
                (pair, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        conn.row_factory = None
        return [dict(row) for row in rows]

    def get_equity_history(self, pair: str) -> list[tuple[float, float]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT timestamp, equity FROM equity_snapshots WHERE pair = ? ORDER BY timestamp",
            (pair,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def save_daily_report(self, report: dict):
        from datetime import date
        today = date.today().isoformat()
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO daily_reports (date, report_json) VALUES (?, ?)",
            (today, json.dumps(report)),
        )
        conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def prune_old_snapshots(self, keep_days: int = 30):
        """Remove old equity snapshots to save disk on Pi."""
        cutoff = time.time() - keep_days * 86400
        conn = self._get_conn()
        conn.execute("DELETE FROM equity_snapshots WHERE timestamp < ?", (cutoff,))
        conn.execute("VACUUM")
        conn.commit()
        logger.info("Pruned snapshots older than %d days", keep_days)
