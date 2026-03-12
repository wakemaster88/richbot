"""Market regime detection based on technical indicators.

Classifies the market into RANGING, TREND_UP, TREND_DOWN, or VOLATILE
and provides regime-specific parameters for grid trading.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import numpy as np

from bot import indicators as ind

logger = logging.getLogger(__name__)


class Regime(Enum):
    RANGING = "ranging"
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    VOLATILE = "volatile"


@dataclass
class RegimeParams:
    """Parameters that adapt the trading strategy to the current regime."""
    regime: Regime
    target_ratio: float   # target USDC fraction of total equity
    spacing_mult: float   # multiplier for grid spacing
    size_mult: float      # multiplier for order sizes
    max_levels: int | None  # cap on grid levels (None = no cap)


@dataclass
class EntryFilter:
    """Regime-aware entry filter for order placement."""
    allow_buys: bool
    allow_sells: bool
    rsi_value: float


_REGIME_PARAMS: dict[Regime, RegimeParams] = {
    Regime.RANGING: RegimeParams(
        regime=Regime.RANGING,
        target_ratio=0.50, spacing_mult=1.0, size_mult=1.0, max_levels=None,
    ),
    Regime.TREND_UP: RegimeParams(
        regime=Regime.TREND_UP,
        target_ratio=0.30, spacing_mult=1.3, size_mult=0.8, max_levels=None,
    ),
    Regime.TREND_DOWN: RegimeParams(
        regime=Regime.TREND_DOWN,
        target_ratio=0.70, spacing_mult=1.3, size_mult=0.6, max_levels=None,
    ),
    Regime.VOLATILE: RegimeParams(
        regime=Regime.VOLATILE,
        target_ratio=0.50, spacing_mult=2.0, size_mult=0.5, max_levels=4,
    ),
}


class RegimeDetector:
    """Detects market regime from OHLCV data.

    Decision tree:
    1. Bollinger Width > 2× average → VOLATILE
    2. ADX < 20 → RANGING
    3. ADX ≥ 25 and EMA9 > EMA21 → TREND_UP
    4. ADX ≥ 25 and EMA9 < EMA21 → TREND_DOWN
    5. ADX 20–25 → keep previous regime (transition zone)
    """

    def __init__(self):
        self._regime = Regime.RANGING
        self._rsi: float = 50.0
        self._adx: float = 0.0
        self._boll_width: float = 0.0
        self._avg_boll_width: float = 0.0
        self._boll_width_history: list[float] = []

    @property
    def regime(self) -> Regime:
        return self._regime

    @property
    def rsi_value(self) -> float:
        return self._rsi

    def update(self, ohlcv: np.ndarray) -> Regime:
        """Update regime from OHLCV data.

        Args:
            ohlcv: 2D array with columns [timestamp, open, high, low, close, volume]

        Returns:
            The detected Regime.
        """
        if len(ohlcv) < 5:
            return self._regime

        highs = ohlcv[:, 2].astype(np.float64)
        lows = ohlcv[:, 3].astype(np.float64)
        closes = ohlcv[:, 4].astype(np.float64)

        self._rsi = ind.rsi(closes, 14)
        self._adx = ind.adx(highs, lows, closes, 14)
        self._boll_width = ind.bollinger_width(closes, 20, 2.0)

        self._boll_width_history.append(self._boll_width)
        if len(self._boll_width_history) > 20:
            self._boll_width_history = self._boll_width_history[-20:]
        self._avg_boll_width = (
            sum(self._boll_width_history) / len(self._boll_width_history)
            if self._boll_width_history else self._boll_width
        )

        ema9 = ind.ema(closes, 9)
        ema21 = ind.ema(closes, 21)
        ema9_last = float(ema9[-1])
        ema21_last = float(ema21[-1])

        old_regime = self._regime

        if self._avg_boll_width > 0 and self._boll_width > 2.0 * self._avg_boll_width:
            self._regime = Regime.VOLATILE
        elif self._adx < 20:
            self._regime = Regime.RANGING
        elif self._adx >= 25:
            if ema9_last > ema21_last:
                self._regime = Regime.TREND_UP
            else:
                self._regime = Regime.TREND_DOWN
        # ADX 20–25: transition zone — keep previous regime

        if self._regime != old_regime:
            logger.info(
                "Regime: %s → %s (ADX=%.1f, RSI=%.1f, BollW=%.4f/avg %.4f, EMA9=%.2f/EMA21=%.2f)",
                old_regime.value, self._regime.value,
                self._adx, self._rsi, self._boll_width, self._avg_boll_width,
                ema9_last, ema21_last,
            )

        return self._regime

    def get_grid_params(self) -> RegimeParams:
        return _REGIME_PARAMS[self._regime]

    def get_entry_filter(self) -> EntryFilter:
        r = self._regime
        rsi = self._rsi

        if r == Regime.RANGING:
            return EntryFilter(allow_buys=True, allow_sells=True, rsi_value=rsi)

        if r == Regime.TREND_UP:
            return EntryFilter(allow_buys=True, allow_sells=(rsi > 60), rsi_value=rsi)

        if r == Regime.TREND_DOWN:
            return EntryFilter(allow_buys=(rsi < 40), allow_sells=True, rsi_value=rsi)

        # VOLATILE
        return EntryFilter(allow_buys=(rsi < 30), allow_sells=(rsi > 70), rsi_value=rsi)

    def to_dict(self) -> dict:
        """Serialise current state for status/logging."""
        return {
            "regime": self._regime.value,
            "rsi": round(self._rsi, 1),
            "adx": round(self._adx, 1),
            "boll_width": round(self._boll_width, 4),
            "avg_boll_width": round(self._avg_boll_width, 4),
        }
