"""Walk-forward optimisation — overfitting-safe parameter search.

Splits historical data into rolling train/test windows, optimises on
train, validates on test, and only promotes parameters that show
consistent out-of-sample performance.

Pi-friendly: runs as a low-priority overnight task (~5 min for 288 sims).
"""

from __future__ import annotations

import copy
import itertools
import logging
import time
from dataclasses import dataclass, field

import numpy as np

from bot.backtest import BacktestEngine, BacktestResult
from bot.config import BotConfig

logger = logging.getLogger(__name__)

CANDLES_PER_DAY = 24  # 1h candles


@dataclass
class WindowResult:
    """Metrics for a single train/test window."""
    window_idx: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    best_params: dict
    in_sample_pnl: float
    in_sample_sharpe: float
    oos_pnl: float
    oos_sharpe: float
    oos_win_rate: float
    oos_max_dd: float
    all_candidates: list = field(default_factory=list)


@dataclass
class WalkForwardResult:
    """Aggregate results across all walk-forward windows."""
    best_params: dict = field(default_factory=dict)
    robustness_score: float = 0.0
    oos_sharpe: float = 0.0
    oos_pnl: float = 0.0
    overfitting_ratio: float = 0.0
    window_results: list = field(default_factory=list)
    total_windows: int = 0
    profitable_windows: int = 0
    duration_sec: float = 0.0
    param_grid_size: int = 0

    def summary(self) -> str:
        of_warn = " ⚠ OVERFITTING" if self.overfitting_ratio > 3.0 else ""
        robust_grade = (
            "EXCELLENT" if self.robustness_score > 0.8
            else "GOOD" if self.robustness_score > 0.6
            else "FAIR" if self.robustness_score > 0.4
            else "POOR"
        )
        lines = [
            f"{'═' * 56}",
            f"  WALK-FORWARD  ({self.total_windows} windows, {self.param_grid_size} combos)",
            f"{'═' * 56}",
            f"  Robustness       {self.robustness_score:.2f}  [{robust_grade}]",
            f"  OOS Sharpe       {self.oos_sharpe:.3f}",
            f"  OOS PnL          {self.oos_pnl:+.4f}",
            f"  Overfitting      {self.overfitting_ratio:.2f}x{of_warn}",
            f"  Profitable Win   {self.profitable_windows}/{self.total_windows}",
            f"  Duration         {self.duration_sec:.1f}s",
            f"{'─' * 56}",
            f"  Best Parameters:",
        ]
        for k, v in self.best_params.items():
            lines.append(f"    {k:.<24} {v}")
        lines.append(f"{'═' * 56}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "best_params": self.best_params,
            "robustness_score": round(self.robustness_score, 4),
            "oos_sharpe": round(self.oos_sharpe, 4),
            "oos_pnl": round(self.oos_pnl, 6),
            "overfitting_ratio": round(self.overfitting_ratio, 3),
            "total_windows": self.total_windows,
            "profitable_windows": self.profitable_windows,
            "duration_sec": round(self.duration_sec, 2),
            "param_grid_size": self.param_grid_size,
            "window_results": [
                {
                    "window": w.window_idx,
                    "best_params": w.best_params,
                    "is_pnl": round(w.in_sample_pnl, 6),
                    "is_sharpe": round(w.in_sample_sharpe, 4),
                    "oos_pnl": round(w.oos_pnl, 6),
                    "oos_sharpe": round(w.oos_sharpe, 4),
                    "oos_win_rate": round(w.oos_win_rate, 2),
                    "oos_max_dd": round(w.oos_max_dd, 3),
                }
                for w in self.window_results
            ],
        }


# ── Walk-forward engine ──────────────────────────────────────────────

DEFAULT_PARAM_GRID = {
    "spacing_percent": [0.3, 0.5, 0.7, 1.0],
    "grid_count": [10, 20],
    "trail_trigger_percent": [0.2, 0.3, 0.5],
}

TOP_N = 3


class WalkForward:
    """Walk-forward optimiser built on the BacktestEngine."""

    def __init__(
        self,
        config: BotConfig | None = None,
        initial_capital: float = 200.0,
        train_days: int = 30,
        test_days: int = 10,
        step_days: int = 5,
        maker_fee: float = 0.001,
    ):
        self.config = config or BotConfig()
        self.initial_capital = initial_capital
        self.train_days = train_days
        self.test_days = test_days
        self.step_days = step_days
        self.maker_fee = maker_fee

    async def run(
        self,
        pair: str,
        ohlcv: np.ndarray,
        param_grid: dict | None = None,
    ) -> WalkForwardResult:
        """Execute walk-forward optimisation across rolling windows."""
        t0 = time.time()
        grid = param_grid or DEFAULT_PARAM_GRID

        combos = list(self._expand_grid(grid))
        n_combos = len(combos)
        logger.info(
            "Walk-Forward: %d param combos, train=%dd test=%dd step=%dd",
            n_combos, self.train_days, self.test_days, self.step_days,
        )

        train_candles = self.train_days * CANDLES_PER_DAY
        test_candles = self.test_days * CANDLES_PER_DAY
        step_candles = self.step_days * CANDLES_PER_DAY
        window_candles = train_candles + test_candles
        total = len(ohlcv)

        if total < window_candles + 50:
            logger.warning(
                "Walk-Forward: nicht genug Daten (%d Candles, brauche %d)",
                total, window_candles + 50,
            )
            return WalkForwardResult(duration_sec=time.time() - t0)

        windows: list[WindowResult] = []
        start = 0
        w_idx = 0

        while start + window_candles <= total:
            train_slice = ohlcv[start:start + train_candles]
            test_slice = ohlcv[start + train_candles:start + window_candles]

            logger.info(
                "Window %d: train[%d:%d] test[%d:%d]",
                w_idx, start, start + train_candles,
                start + train_candles, start + window_candles,
            )

            is_results = await self._evaluate_combos(pair, train_slice, combos)

            top_indices = sorted(
                range(len(is_results)),
                key=lambda i: is_results[i].sharpe_ratio * 0.6 + (is_results[i].total_pnl / max(self.initial_capital, 1)) * 0.4,
                reverse=True,
            )[:TOP_N]

            best_oos: BacktestResult | None = None
            best_combo: dict = combos[top_indices[0]] if top_indices else {}
            best_is: BacktestResult = is_results[top_indices[0]] if top_indices else BacktestResult(pair=pair, days=0, initial_capital=self.initial_capital)

            for idx in top_indices:
                combo = combos[idx]
                oos_result = await self._run_single(pair, test_slice, combo)
                if best_oos is None or oos_result.sharpe_ratio > best_oos.sharpe_ratio:
                    best_oos = oos_result
                    best_combo = combo
                    best_is = is_results[idx]

            if best_oos is None:
                best_oos = BacktestResult(pair=pair, days=0, initial_capital=self.initial_capital)

            windows.append(WindowResult(
                window_idx=w_idx,
                train_start=start,
                train_end=start + train_candles,
                test_start=start + train_candles,
                test_end=start + window_candles,
                best_params=best_combo,
                in_sample_pnl=best_is.total_pnl,
                in_sample_sharpe=best_is.sharpe_ratio,
                oos_pnl=best_oos.total_pnl,
                oos_sharpe=best_oos.sharpe_ratio,
                oos_win_rate=best_oos.win_rate,
                oos_max_dd=best_oos.max_drawdown,
            ))

            logger.info(
                "Window %d: IS-PnL=%.4f IS-Sharpe=%.2f | OOS-PnL=%.4f OOS-Sharpe=%.2f | params=%s",
                w_idx, best_is.total_pnl, best_is.sharpe_ratio,
                best_oos.total_pnl, best_oos.sharpe_ratio, best_combo,
            )

            start += step_candles
            w_idx += 1

        return self._aggregate(windows, n_combos, time.time() - t0)

    # ── internal helpers ──────────────────────────────────────────

    async def _evaluate_combos(
        self, pair: str, data: np.ndarray, combos: list[dict],
    ) -> list[BacktestResult]:
        """Run all parameter combos on one data slice."""
        results: list[BacktestResult] = []
        for combo in combos:
            r = await self._run_single(pair, data, combo)
            results.append(r)
        return results

    async def _run_single(
        self, pair: str, data: np.ndarray, params: dict,
    ) -> BacktestResult:
        """Run a single backtest with specific parameters."""
        cfg = copy.deepcopy(self.config)
        if "spacing_percent" in params:
            cfg.grid.spacing_percent = params["spacing_percent"]
        if "grid_count" in params:
            cfg.grid.grid_count = params["grid_count"]
        if "trail_trigger_percent" in params:
            cfg.grid.trail_trigger_percent = params["trail_trigger_percent"]
        if "range_multiplier" in params:
            cfg.grid.range_multiplier = params["range_multiplier"]

        engine = BacktestEngine(
            config=cfg,
            initial_capital=self.initial_capital,
            maker_fee=self.maker_fee,
            taker_fee=self.maker_fee,
            seed=42,
        )
        days = max(1, len(data) // CANDLES_PER_DAY)
        return await engine.run(pair, data, days)

    def _aggregate(
        self,
        windows: list[WindowResult],
        param_grid_size: int,
        elapsed: float,
    ) -> WalkForwardResult:
        """Combine all window results into aggregate metrics."""
        if not windows:
            return WalkForwardResult(duration_sec=elapsed, param_grid_size=param_grid_size)

        profitable = sum(1 for w in windows if w.oos_pnl > 0)
        total = len(windows)
        robustness = profitable / total

        oos_pnls = [w.oos_pnl for w in windows]
        oos_sharpes = [w.oos_sharpe for w in windows]
        is_pnls = [w.in_sample_pnl for w in windows]

        avg_oos_sharpe = sum(oos_sharpes) / len(oos_sharpes)
        avg_oos_pnl = sum(oos_pnls) / len(oos_pnls)
        avg_is_pnl = sum(is_pnls) / len(is_pnls)

        if avg_oos_pnl != 0:
            overfitting_ratio = abs(avg_is_pnl / avg_oos_pnl)
        else:
            overfitting_ratio = float("inf") if avg_is_pnl > 0 else 0.0

        if not all(oos_sharpes):
            sharpe_consistency = 0.0
        else:
            positive_sharpe = sum(1 for s in oos_sharpes if s > 0)
            sharpe_consistency = positive_sharpe / len(oos_sharpes)

        robustness = (robustness * 0.5 + sharpe_consistency * 0.5)

        param_votes: dict[str, list] = {}
        for w in windows:
            if w.oos_pnl > 0:
                for k, v in w.best_params.items():
                    param_votes.setdefault(k, []).append(v)

        best_params: dict = {}
        for k, values in param_votes.items():
            if all(isinstance(v, (int, float)) for v in values):
                best_params[k] = round(sum(values) / len(values), 4)
            else:
                from collections import Counter
                best_params[k] = Counter(values).most_common(1)[0][0]

        if not best_params and windows:
            best_params = windows[-1].best_params

        return WalkForwardResult(
            best_params=best_params,
            robustness_score=round(robustness, 4),
            oos_sharpe=round(avg_oos_sharpe, 4),
            oos_pnl=round(avg_oos_pnl, 6),
            overfitting_ratio=round(min(overfitting_ratio, 999.0), 3),
            window_results=windows,
            total_windows=total,
            profitable_windows=profitable,
            duration_sec=elapsed,
            param_grid_size=param_grid_size,
        )

    @staticmethod
    def _expand_grid(param_grid: dict) -> list[dict]:
        """Expand a param grid dict into all combinations."""
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        combos = []
        for combo in itertools.product(*values):
            combos.append(dict(zip(keys, combo)))
        return combos
