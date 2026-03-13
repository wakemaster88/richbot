"""Cross-pair correlation monitoring and portfolio-level risk limits.

Tracks hourly returns per pair, computes Pearson correlation matrices,
derives effective position limits and a portfolio Value-at-Risk estimate.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK = 168  # 1 week of hourly bars
HIGH_CORR_THRESHOLD = 0.7
EXTREME_CORR_THRESHOLD = 0.9
VAR_CONFIDENCE = 1.65  # ~95% one-tail normal


@dataclass
class CorrelationResult:
    matrix: list[list[float]]
    pairs: list[str]
    portfolio_var_pct: float
    portfolio_var_abs: float
    high_corr_warnings: list[dict]
    size_adjustments: dict[str, float]


class CorrelationMonitor:
    """Monitors return correlations across trading pairs."""

    def __init__(self, pairs: list[str], lookback: int = DEFAULT_LOOKBACK):
        self._pairs = list(pairs)
        self._lookback = lookback
        self._returns: dict[str, deque[float]] = {
            p: deque(maxlen=lookback) for p in pairs
        }
        self._corr_matrix: np.ndarray | None = None
        self._last_result: CorrelationResult | None = None

    @property
    def pairs(self) -> list[str]:
        return self._pairs

    def add_pair(self, pair: str):
        if pair not in self._pairs:
            self._pairs.append(pair)
            self._returns[pair] = deque(maxlen=self._lookback)
            self._corr_matrix = None

    def update(self, pair: str, returns: np.ndarray) -> None:
        """Feed hourly log-returns for a pair (newest last)."""
        if pair not in self._returns:
            self.add_pair(pair)
        buf = self._returns[pair]
        for r in returns:
            buf.append(float(r))

    def returns_from_ohlcv(self, closes: np.ndarray) -> np.ndarray:
        """Compute hourly log-returns from close prices."""
        if len(closes) < 2:
            return np.array([])
        c = np.asarray(closes, dtype=np.float64)
        c = c[c > 0]
        if len(c) < 2:
            return np.array([])
        return np.diff(np.log(c))

    def _build_matrix(self) -> np.ndarray | None:
        n = len(self._pairs)
        if n < 2:
            return None
        min_obs = max(20, self._lookback // 4)
        lengths = [len(self._returns[p]) for p in self._pairs]
        common = min(lengths)
        if common < min_obs:
            return None

        data = np.zeros((common, n))
        for j, p in enumerate(self._pairs):
            buf = self._returns[p]
            arr = list(buf)[-common:]
            data[:, j] = arr

        with np.errstate(divide="ignore", invalid="ignore"):
            corr = np.corrcoef(data, rowvar=False)
        corr = np.nan_to_num(corr, nan=0.0)
        np.fill_diagonal(corr, 1.0)
        return corr

    def correlation_matrix(self) -> np.ndarray | None:
        self._corr_matrix = self._build_matrix()
        return self._corr_matrix

    def effective_position_limit(self, pair: str, base_limit: float) -> float:
        """Reduce position limit when highly correlated with other active pairs."""
        if self._corr_matrix is None or pair not in self._pairs:
            return base_limit

        idx = self._pairs.index(pair)
        n = len(self._pairs)
        max_corr = 0.0
        for j in range(n):
            if j == idx:
                continue
            c = abs(self._corr_matrix[idx, j])
            if c > max_corr:
                max_corr = c

        if max_corr < HIGH_CORR_THRESHOLD:
            return base_limit

        factor = 1.0 / math.sqrt(1.0 + max_corr)
        reduced = base_limit * factor
        logger.debug(
            "Position limit %s: %.4f → %.4f (corr=%.2f, factor=%.3f)",
            pair, base_limit, reduced, max_corr, factor,
        )
        return reduced

    def portfolio_var(
        self,
        positions: dict[str, float],
        prices: dict[str, float],
        daily_vols: dict[str, float] | None = None,
    ) -> float:
        """Approximate daily portfolio VaR (dollar amount) using correlation matrix.

        Uses sqrt(w^T * Sigma * w) where Sigma is the covariance matrix
        and w is the dollar-weighted position vector.
        """
        if self._corr_matrix is None or len(self._pairs) < 2:
            return 0.0

        n = len(self._pairs)
        w = np.zeros(n)
        vol = np.zeros(n)

        for i, p in enumerate(self._pairs):
            pos = positions.get(p, 0.0)
            px = prices.get(p, 0.0)
            w[i] = abs(pos * px)
            if daily_vols and p in daily_vols:
                vol[i] = daily_vols[p]
            else:
                buf = self._returns[p]
                if len(buf) >= 20:
                    arr = np.array(list(buf)[-168:])
                    hourly_vol = float(np.std(arr))
                    vol[i] = hourly_vol * math.sqrt(24)
                else:
                    vol[i] = 0.025

        cov = np.outer(vol, vol) * self._corr_matrix
        var_sq = w @ cov @ w
        if var_sq <= 0:
            return 0.0
        return float(np.sqrt(var_sq)) * VAR_CONFIDENCE

    def compute(
        self,
        positions: dict[str, float],
        prices: dict[str, float],
        total_equity: float = 0.0,
    ) -> CorrelationResult:
        """Full computation: matrix, warnings, limits, VaR."""
        self.correlation_matrix()

        n = len(self._pairs)
        matrix_list: list[list[float]] = []
        if self._corr_matrix is not None:
            matrix_list = [
                [round(float(self._corr_matrix[i, j]), 3) for j in range(n)]
                for i in range(n)
            ]

        warnings: list[dict] = []
        adjustments: dict[str, float] = {}

        if self._corr_matrix is not None:
            for i in range(n):
                for j in range(i + 1, n):
                    c = float(self._corr_matrix[i, j])
                    if abs(c) >= HIGH_CORR_THRESHOLD:
                        warn = {
                            "pair_a": self._pairs[i],
                            "pair_b": self._pairs[j],
                            "correlation": round(c, 3),
                            "extreme": abs(c) >= EXTREME_CORR_THRESHOLD,
                        }
                        warnings.append(warn)
                        if abs(c) >= EXTREME_CORR_THRESHOLD:
                            logger.warning(
                                "EXTREME CORRELATION: %s / %s = %.2f — Diversifikation pruefen",
                                self._pairs[i], self._pairs[j], c,
                            )

            for p in self._pairs:
                factor = self.effective_position_limit(p, 1.0)
                if factor < 0.99:
                    adjustments[p] = round(factor, 3)

        var_abs = self.portfolio_var(positions, prices)
        var_pct = (var_abs / total_equity * 100) if total_equity > 0 else 0.0

        result = CorrelationResult(
            matrix=matrix_list,
            pairs=list(self._pairs),
            portfolio_var_pct=round(var_pct, 2),
            portfolio_var_abs=round(var_abs, 4),
            high_corr_warnings=warnings,
            size_adjustments=adjustments,
        )
        self._last_result = result
        return result

    def get_last_result(self) -> CorrelationResult | None:
        return self._last_result

    def get_metrics(self) -> dict:
        """Serializable metrics for dashboard / heartbeat."""
        r = self._last_result
        if r is None:
            return {}
        return {
            "matrix": r.matrix,
            "pairs": r.pairs,
            "portfolio_var_pct": r.portfolio_var_pct,
            "portfolio_var_abs": r.portfolio_var_abs,
            "high_corr_warnings": r.high_corr_warnings,
            "size_adjustments": r.size_adjustments,
        }
