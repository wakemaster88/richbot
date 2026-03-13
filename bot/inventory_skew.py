"""Inventory skewing — shifts grid prices and sizes toward neutral position.

When holding too much crypto the grid makes sells more aggressive (closer
to market, larger size) and buys more conservative (further, smaller).
Vice-versa when underweight.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MAX_PRICE_ADJUSTMENT = 0.005
MAX_SIZE_ADJUSTMENT = 0.50
ALERT_THRESHOLD = 0.50

SELL_PRICE_COEFF = 0.001
BUY_PRICE_COEFF = 0.0005
SELL_SIZE_COEFF = 0.30
BUY_SIZE_COEFF = 0.20


@dataclass
class SkewResult:
    skew_factor: float
    current_ratio: float
    target_ratio: float
    base_value: float
    quote_value: float
    total_value: float
    adjusted_levels: int
    needs_rebalance: bool

    @property
    def skew_pct(self) -> float:
        return self.skew_factor * 100

    @property
    def description(self) -> str:
        if abs(self.skew_factor) < 0.05:
            return "balanced"
        if self.skew_factor > 0:
            return "overweight — sells aggressiver"
        return "underweight — buys aggressiver"


class InventorySkew:
    """Computes and applies inventory-based grid skewing."""

    def __init__(
        self,
        max_price_adj: float = MAX_PRICE_ADJUSTMENT,
        max_size_adj: float = MAX_SIZE_ADJUSTMENT,
        alert_threshold: float = ALERT_THRESHOLD,
    ):
        self.max_price_adj = max_price_adj
        self.max_size_adj = max_size_adj
        self.alert_threshold = alert_threshold
        self._last_skew: dict[str, SkewResult] = {}

    def compute_skew(
        self,
        pair: str,
        base_value: float,
        quote_value: float,
        target_ratio: float,
    ) -> float:
        """Compute skew factor: positive = too much base, negative = too little."""
        total = base_value + quote_value
        if total <= 0:
            return 0.0
        current_ratio = base_value / total
        if target_ratio <= 0:
            target_ratio = 0.50
        skew = (current_ratio - target_ratio) / target_ratio
        return max(-1.0, min(1.0, skew))

    def apply_to_grid(
        self,
        grid_levels: list,
        pair: str,
        base_value: float,
        quote_value: float,
        target_ratio: float,
        min_amount: float = 0.0,
    ) -> SkewResult:
        """Apply inventory skew adjustments to grid levels in-place."""
        total = base_value + quote_value
        if total <= 0:
            result = SkewResult(
                skew_factor=0.0, current_ratio=0.5, target_ratio=target_ratio,
                base_value=base_value, quote_value=quote_value,
                total_value=total, adjusted_levels=0, needs_rebalance=False,
            )
            self._last_skew[pair] = result
            return result

        current_ratio = base_value / total
        skew = self.compute_skew(pair, base_value, quote_value, target_ratio)

        adjusted = 0
        for level in grid_levels:
            if level.filled or level.order_id is not None:
                continue

            if level.side == "sell":
                price_shift = -skew * SELL_PRICE_COEFF
                price_shift = max(-self.max_price_adj, min(self.max_price_adj, price_shift))
                level.price *= (1 + price_shift)

                size_mult = 1 + skew * SELL_SIZE_COEFF
                size_mult = max(1 - self.max_size_adj, min(1 + self.max_size_adj, size_mult))
                level.amount *= size_mult
            else:
                price_shift = -skew * BUY_PRICE_COEFF
                price_shift = max(-self.max_price_adj, min(self.max_price_adj, price_shift))
                level.price *= (1 + price_shift)

                size_mult = 1 - skew * BUY_SIZE_COEFF
                size_mult = max(1 - self.max_size_adj, min(1 + self.max_size_adj, size_mult))
                level.amount *= size_mult

            if min_amount > 0 and level.amount < min_amount:
                level.amount = min_amount

            level.price = round(level.price, 2)
            adjusted += 1

        needs_rebalance = abs(skew) > self.alert_threshold

        result = SkewResult(
            skew_factor=round(skew, 4),
            current_ratio=round(current_ratio, 4),
            target_ratio=round(target_ratio, 4),
            base_value=round(base_value, 4),
            quote_value=round(quote_value, 4),
            total_value=round(total, 4),
            adjusted_levels=adjusted,
            needs_rebalance=needs_rebalance,
        )
        self._last_skew[pair] = result

        if abs(skew) > 0.05:
            logger.info(
                "Skew %s: %.1f%% (base %.1f%% vs target %.1f%%) — %s, %d levels adjusted",
                pair, skew * 100, current_ratio * 100, target_ratio * 100,
                result.description, adjusted,
            )

        return result

    def get_last_skew(self, pair: str) -> SkewResult | None:
        return self._last_skew.get(pair)

    def get_metrics(self, pair: str) -> dict:
        """Return skew metrics for dashboard / heartbeat."""
        sr = self._last_skew.get(pair)
        if sr is None:
            return {}
        return {
            "skew_factor": sr.skew_factor,
            "skew_pct": round(sr.skew_pct, 1),
            "current_ratio": sr.current_ratio,
            "target_ratio": sr.target_ratio,
            "base_value": sr.base_value,
            "quote_value": sr.quote_value,
            "description": sr.description,
            "needs_rebalance": sr.needs_rebalance,
        }
