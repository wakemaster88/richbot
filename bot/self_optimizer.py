"""Self-optimization engine: evaluates performance and tunes parameters.

Runs every 6 hours, analyses trade history, and makes small adjustments
to grid spacing, order sizing, and range width to improve profitability.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

import numpy as np

from bot.performance_tracker import PerformanceTracker

logger = logging.getLogger(__name__)

MAX_ADJUSTMENT_PCT = 0.05  # cap each single parameter change at 5%


@dataclass
class PerformanceWindow:
    """Metrics computed over a rolling window of trades."""
    period_hours: int
    trade_count: int
    win_rate: float
    avg_profit_per_trade: float
    total_pnl: float
    sharpe_ratio: float
    max_drawdown_pct: float
    grid_fill_rate: float
    avg_hold_time: float


@dataclass
class PairScore:
    """Per-pair performance score for capital allocation."""
    pair: str
    sharpe: float
    pnl_24h: float
    pnl_48h: float
    capital_weight: float = 0.5  # default equal weight


class SelfOptimizer:
    """Evaluates recent performance and suggests parameter adjustments."""

    EVAL_PERIOD_H = 24

    def __init__(self, tracker: PerformanceTracker):
        self.tracker = tracker
        self._last_optimization = 0.0

    # ── evaluation ───────────────────────────────────────────────

    def evaluate(self, pair: str) -> PerformanceWindow:
        """Compute performance metrics for the last EVAL_PERIOD_H hours."""
        cutoff = time.time() - self.EVAL_PERIOD_H * 3600
        trades = self._get_trades_since(pair, cutoff)

        trade_count = len(trades)
        if trade_count == 0:
            return PerformanceWindow(
                period_hours=self.EVAL_PERIOD_H, trade_count=0,
                win_rate=0.0, avg_profit_per_trade=0.0, total_pnl=0.0,
                sharpe_ratio=0.0, max_drawdown_pct=0.0,
                grid_fill_rate=0.0, avg_hold_time=0.0,
            )

        pnls = [t["pnl"] for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / trade_count if trade_count else 0.0
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / trade_count

        hold_times = self._estimate_hold_times(trades)
        avg_hold = float(np.mean(hold_times)) if hold_times else 0.0

        stats = self.tracker._get_pair_stats(pair)
        grid_total = max(1, len(getattr(stats, "equity_history", [])))
        filled_count = stats.trade_count
        grid_fill_rate = min(1.0, filled_count / max(grid_total, 1))

        sharpe = self.tracker.get_sharpe_ratio(pair)
        max_dd = self.tracker.get_max_drawdown(pair)

        return PerformanceWindow(
            period_hours=self.EVAL_PERIOD_H,
            trade_count=trade_count,
            win_rate=win_rate,
            avg_profit_per_trade=avg_pnl,
            total_pnl=total_pnl,
            sharpe_ratio=sharpe,
            max_drawdown_pct=max_dd,
            grid_fill_rate=grid_fill_rate,
            avg_hold_time=avg_hold,
        )

    # ── suggestions ──────────────────────────────────────────────

    def suggest_adjustments(self, current_params: dict,
                            window: PerformanceWindow) -> dict:
        """Propose parameter tweaks based on recent performance.

        Each adjustment is capped at MAX_ADJUSTMENT_PCT per cycle.
        """
        adj: dict[str, float] = {}

        spacing = current_params.get("spacing_mult", 1.0)
        size = current_params.get("size_mult", 1.0)
        range_mult = current_params.get("range_multiplier", 1.0)

        if window.trade_count < 3:
            return {}

        # Win rate → spacing
        if window.win_rate < 0.50:
            spacing = self._nudge(spacing, +0.10)
        elif window.win_rate > 0.70:
            spacing = self._nudge(spacing, -0.05)

        # Grid fill rate → range width
        if window.grid_fill_rate < 0.30:
            range_mult = self._nudge(range_mult, -0.05)
        elif window.grid_fill_rate > 0.80:
            range_mult = self._nudge(range_mult, +0.05)

        # Hold time → spacing (trending market = wider spacing)
        if window.avg_hold_time > 3600:
            spacing = self._nudge(spacing, +0.05)

        # Drawdown → size reduction
        if window.max_drawdown_pct > 5.0:
            size = self._nudge(size, -0.10)
        elif window.max_drawdown_pct < 1.0 and window.win_rate > 0.55:
            size = self._nudge(size, +0.05)

        spacing = max(0.5, min(3.0, spacing))
        size = max(0.2, min(1.5, size))
        range_mult = max(0.5, min(2.0, range_mult))

        if spacing != current_params.get("spacing_mult", 1.0):
            adj["spacing_mult"] = round(spacing, 3)
        if size != current_params.get("size_mult", 1.0):
            adj["size_mult"] = round(size, 3)
        if range_mult != current_params.get("range_multiplier", 1.0):
            adj["range_multiplier"] = round(range_mult, 3)

        return adj

    # ── pair scoring ─────────────────────────────────────────────

    def score_pairs(self, pairs: list[str]) -> list[PairScore]:
        """Score pairs and compute capital allocation weights.

        Better Sharpe → more capital. Negative PnL for 48h → penalty.
        """
        if len(pairs) <= 1:
            return [PairScore(pair=p, sharpe=0, pnl_24h=0, pnl_48h=0, capital_weight=1.0)
                    for p in pairs]

        scores: list[PairScore] = []
        now = time.time()

        for pair in pairs:
            sharpe = self.tracker.get_sharpe_ratio(pair)
            pnl_24h = self._pnl_since(pair, now - 24 * 3600)
            pnl_48h = self._pnl_since(pair, now - 48 * 3600)
            scores.append(PairScore(pair=pair, sharpe=sharpe,
                                    pnl_24h=pnl_24h, pnl_48h=pnl_48h))

        best_sharpe = max(s.sharpe for s in scores)
        worst_sharpe = min(s.sharpe for s in scores)
        spread = best_sharpe - worst_sharpe

        for s in scores:
            if s.pnl_48h < 0:
                s.capital_weight = 0.30
            elif spread > 0.01:
                rank = (s.sharpe - worst_sharpe) / spread
                s.capital_weight = 0.40 + 0.20 * rank  # range [0.40, 0.60]
            else:
                s.capital_weight = 1.0 / len(pairs)

        total_w = sum(s.capital_weight for s in scores)
        if total_w > 0:
            for s in scores:
                s.capital_weight = round(s.capital_weight / total_w, 3)

        return scores

    # ── helpers ──────────────────────────────────────────────────

    @staticmethod
    def _nudge(value: float, delta: float) -> float:
        """Apply a delta, but cap the change at MAX_ADJUSTMENT_PCT of current value."""
        max_change = abs(value) * MAX_ADJUSTMENT_PCT
        clamped = max(-max_change, min(max_change, delta))
        return value + clamped

    def _get_trades_since(self, pair: str, since: float) -> list[dict]:
        """Fetch trades from SQLite since a given timestamp."""
        try:
            conn = self.tracker._get_conn()
            rows = conn.execute(
                "SELECT side, price, amount, pnl, timestamp FROM trades "
                "WHERE pair = ? AND timestamp >= ? ORDER BY timestamp",
                (pair, since),
            ).fetchall()
            return [{"side": r[0], "price": r[1], "amount": r[2],
                     "pnl": r[3], "timestamp": r[4]} for r in rows]
        except Exception:
            return []

    def _pnl_since(self, pair: str, since: float) -> float:
        trades = self._get_trades_since(pair, since)
        return sum(t["pnl"] for t in trades)

    @staticmethod
    def _estimate_hold_times(trades: list[dict]) -> list[float]:
        """Estimate hold times by pairing consecutive buy→sell or sell→buy."""
        hold_times: list[float] = []
        last_entry: dict | None = None
        for t in trades:
            if last_entry is None:
                last_entry = t
            elif t["side"] != last_entry["side"]:
                dt = t["timestamp"] - last_entry["timestamp"]
                if dt > 0:
                    hold_times.append(dt)
                last_entry = None
            else:
                last_entry = t
        return hold_times
