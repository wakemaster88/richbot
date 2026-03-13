"""Grid calculation engine with infinity/trailing grid support.

Supports asymmetric grids where buy_count and sell_count can differ.
Range is computed internally per side to guarantee min_fee_spacing.
Pyramid sizing: levels further from the price get larger amounts.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum

from bot.dynamic_range import RangeResult

logger = logging.getLogger(__name__)


class GridMode(Enum):
    STATIC = "static"
    INFINITY = "infinity"


@dataclass
class GridLevel:
    price: float
    side: str  # "buy" or "sell"
    amount: float
    order_id: str | None = None
    filled: bool = False
    index: int = 0
    partial_fills: list = field(default_factory=list)

    @property
    def level_id(self) -> str:
        return f"{self.side}_{self.index}_{self.price:.2f}"

    @property
    def filled_amount(self) -> float:
        return sum(amt for _, _, amt in self.partial_fills)

    @property
    def is_fully_filled(self) -> bool:
        return self.filled

    @property
    def fill_pct(self) -> float:
        if self.amount <= 0:
            return 100.0 if self.filled else 0.0
        return min(self.filled_amount / self.amount * 100, 100.0)


@dataclass
class GridState:
    levels: list[GridLevel] = field(default_factory=list)
    range_result: RangeResult | None = None
    mode: GridMode = GridMode.INFINITY
    shift_count: int = 0
    last_price: float = 0.0
    _cache_ver: int = 0
    _buy_cache: list[GridLevel] | None = field(default=None, repr=False)
    _sell_cache: list[GridLevel] | None = field(default=None, repr=False)
    _active_cache: list[GridLevel] | None = field(default=None, repr=False)
    _filled_cache: list[GridLevel] | None = field(default=None, repr=False)

    def invalidate(self):
        self._cache_ver += 1
        self._buy_cache = None
        self._sell_cache = None
        self._active_cache = None
        self._filled_cache = None

    @property
    def buy_levels(self) -> list[GridLevel]:
        if self._buy_cache is None:
            self._buy_cache = [l for l in self.levels if l.side == "buy"]
        return self._buy_cache

    @property
    def sell_levels(self) -> list[GridLevel]:
        if self._sell_cache is None:
            self._sell_cache = [l for l in self.levels if l.side == "sell"]
        return self._sell_cache

    @property
    def active_levels(self) -> list[GridLevel]:
        if self._active_cache is None:
            self._active_cache = [l for l in self.levels if not l.filled]
        return self._active_cache

    @property
    def filled_levels(self) -> list[GridLevel]:
        if self._filled_cache is None:
            self._filled_cache = [l for l in self.levels if l.filled]
        return self._filled_cache


def _pyramid_weights(count: int) -> list[float]:
    """Pyramid weights: 0.7 (near price) to 1.3 (far from price)."""
    if count <= 1:
        return [1.0]
    return [0.7 + 0.6 * i / (count - 1) for i in range(count)]


def _floor_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor(value / step) * step


class GridEngine:
    """Computes and manages grid levels with trailing/infinity support."""

    MIN_SPACING_VS_FEE = 1.2
    FEE_RATE = 0.001

    def __init__(self, grid_count: int = 20, spacing_percent: float = 0.5,
                 amount_per_order: float = 0.0001, infinity_mode: bool = True,
                 trail_trigger_percent: float = 1.5):
        self.grid_count = grid_count
        self.spacing_percent = max(
            spacing_percent,
            self.FEE_RATE * 200 * self.MIN_SPACING_VS_FEE,
        )
        self.amount_per_order = amount_per_order
        self.infinity_mode = infinity_mode
        self.trail_trigger_percent = trail_trigger_percent
        self.state = GridState(
            mode=GridMode.INFINITY if infinity_mode else GridMode.STATIC,
        )

    @staticmethod
    def _weighted_positions(count: int) -> list[float]:
        """Sqrt-weighted positions: denser near price, sparser at boundaries."""
        if count <= 1:
            return [1.0]
        return [((i + 1) / count) ** 0.6 for i in range(count)]

    def _min_fee_spacing(self, price: float) -> float:
        return price * self.FEE_RATE * 2 * self.MIN_SPACING_VS_FEE

    def _build_side(self, side: str, count: int, current_price: float,
                    span: float, amount: float, min_spacing: float,
                    use_linear: bool = False,
                    budget: float | None = None,
                    budget_is_quote: bool = True,
                    step_size: float = 0.00001,
                    min_amount: float = 0.0,
                    min_distance: float = 0.0) -> list[GridLevel]:
        """Build levels for one side of the grid.

        If budget is provided, uses pyramid sizing to distribute capital.
        Otherwise falls back to uniform `amount` per level.
        min_distance: minimum price distance from current_price for the
                      closest level (prevents instant fills).
        """
        if count <= 0 or span <= 0:
            return []

        if use_linear:
            pos_weights = [(i + 1) / count for i in range(count)]
        else:
            pos_weights = self._weighted_positions(count)

        pyr_weights = _pyramid_weights(count)

        if budget is not None and budget > 0:
            weight_sum = sum(pyr_weights)
            if budget_is_quote:
                base_amount = budget / (weight_sum * current_price)
            else:
                base_amount = budget / weight_sum
            amounts = [_floor_step(base_amount * w, step_size) for w in pyr_weights]
            for i, a in enumerate(amounts):
                if a < min_amount:
                    amounts[i] = min_amount
        else:
            amounts = [amount] * count

        safe_dist = max(min_distance, min_spacing)

        levels: list[GridLevel] = []
        seen_prices: set[float] = set()
        for i, w in enumerate(pos_weights):
            if side == "buy":
                price = current_price - span * w
            else:
                price = current_price + span * w

            rounded = round(price, 2)

            dist_from_current = abs(rounded - current_price)
            if dist_from_current < safe_dist:
                if side == "buy":
                    rounded = round(current_price - safe_dist - min_spacing * i, 2)
                else:
                    rounded = round(current_price + safe_dist + min_spacing * i, 2)

            if rounded in seen_prices:
                continue
            if levels and abs(rounded - levels[-1].price) < min_spacing:
                continue

            seen_prices.add(rounded)
            lvl_amount = amounts[i] if i < len(amounts) else amounts[-1]
            levels.append(GridLevel(price=rounded, side=side, amount=lvl_amount, index=i))

        return levels

    def calculate_grid(self, range_result: RangeResult, current_price: float,
                       dynamic_amount: float | None = None,
                       buy_count: int | None = None,
                       sell_count: int | None = None,
                       buy_budget: float | None = None,
                       sell_budget: float | None = None,
                       step_size: float = 0.00001,
                       min_amount: float = 0.0,
                       min_distance_pct: float = 0.0) -> list[GridLevel]:
        """Calculate grid levels with asymmetric counts and pyramid sizing.

        Args:
            buy_budget: USDC available for buy orders (enables pyramid sizing)
            sell_budget: base currency available for sell orders (enables pyramid sizing)
            step_size: exchange quantity precision for rounding
            min_amount: minimum order amount (min_notional / price, rounded)
            min_distance_pct: minimum % distance from current price for closest order
        """
        self.state.range_result = range_result
        self.state.last_price = current_price
        amount = dynamic_amount or self.amount_per_order

        if buy_count is None and sell_count is None:
            buy_count = self.grid_count // 2
            sell_count = self.grid_count - buy_count
        elif buy_count is None:
            buy_count = max(0, self.grid_count - sell_count)
        elif sell_count is None:
            sell_count = max(0, self.grid_count - buy_count)

        min_spacing = self._min_fee_spacing(current_price)
        min_distance = current_price * max(min_distance_pct, self.FEE_RATE * 2 * self.MIN_SPACING_VS_FEE) / 100 \
            if min_distance_pct > 0 else min_spacing

        buy_span_needed = max(buy_count, 1) * min_spacing * 1.15 if buy_count > 0 else 0
        sell_span_needed = max(sell_count, 1) * min_spacing * 1.15 if sell_count > 0 else 0

        actual_buy_span = current_price - range_result.lower
        actual_sell_span = range_result.upper - current_price

        buy_span = max(actual_buy_span, buy_span_needed)
        sell_span = max(actual_sell_span, sell_span_needed)

        effective_lower = current_price - buy_span
        effective_upper = current_price + sell_span

        effective_range = RangeResult(
            upper=effective_upper, lower=effective_lower, mid=current_price,
            atr=range_result.atr, source=range_result.source,
            confidence=range_result.confidence,
        )
        self.state.range_result = effective_range

        if buy_span != actual_buy_span or sell_span != actual_sell_span:
            logger.info(
                "Range angepasst fuer %dB+%dS: [%.2f, %.2f] (Buy-Span: %.2f, Sell-Span: %.2f)",
                buy_count, sell_count, effective_lower, effective_upper, buy_span, sell_span,
            )

        buy_levels = self._build_side(
            "buy", buy_count, current_price, buy_span, amount, min_spacing,
            use_linear=False, budget=buy_budget, budget_is_quote=True,
            step_size=step_size, min_amount=min_amount, min_distance=min_distance,
        )
        sell_levels = self._build_side(
            "sell", sell_count, current_price, sell_span, amount, min_spacing,
            use_linear=False, budget=sell_budget, budget_is_quote=False,
            step_size=step_size, min_amount=min_amount, min_distance=min_distance,
        )

        if len(buy_levels) < buy_count:
            linear_buys = self._build_side(
                "buy", buy_count, current_price, buy_span, amount, min_spacing,
                use_linear=True, budget=buy_budget, budget_is_quote=True,
                step_size=step_size, min_amount=min_amount, min_distance=min_distance,
            )
            if len(linear_buys) > len(buy_levels):
                buy_levels = linear_buys
        if len(sell_levels) < sell_count:
            linear_sells = self._build_side(
                "sell", sell_count, current_price, sell_span, amount, min_spacing,
                use_linear=True, budget=sell_budget, budget_is_quote=False,
                step_size=step_size, min_amount=min_amount, min_distance=min_distance,
            )
            if len(linear_sells) > len(sell_levels):
                sell_levels = linear_sells

        levels = buy_levels + sell_levels
        levels.sort(key=lambda l: l.price)

        self.state.levels = levels
        self.state.invalidate()

        ab = len(buy_levels)
        a_s = len(sell_levels)

        if levels:
            amounts = [l.amount for l in levels]
            logger.info(
                "Grid calculated: %dB+%dS=%d levels in [%.2f, %.2f] "
                "(amount range: %.8f — %.8f)",
                ab, a_s, len(levels), effective_lower, effective_upper,
                min(amounts), max(amounts),
            )
        else:
            logger.info("Grid calculated: 0 levels")

        return levels

    def check_trail_needed(self, current_price: float) -> str | None:
        """Check if grid needs trailing. Returns 'up', 'down', or None."""
        if not self.infinity_mode or self.state.range_result is None:
            return None

        rng = self.state.range_result
        trigger = rng.spread * self.trail_trigger_percent / 100

        if current_price > rng.upper - trigger:
            return "up"
        if current_price < rng.lower + trigger:
            return "down"
        return None

    def trail_grid(self, direction: str, current_price: float,
                   new_range: RangeResult, dynamic_amount: float | None = None,
                   buy_count: int | None = None,
                   sell_count: int | None = None,
                   buy_budget: float | None = None,
                   sell_budget: float | None = None,
                   step_size: float = 0.00001,
                   min_amount: float = 0.0,
                   min_distance_pct: float = 0.0) -> list[GridLevel]:
        """Trail the grid in the given direction by recalculating."""
        self.state.shift_count += 1
        logger.info(
            "Trailing grid %s (shift #%d) to new range [%.2f, %.2f]",
            direction, self.state.shift_count, new_range.lower, new_range.upper,
        )
        return self.calculate_grid(
            new_range, current_price, dynamic_amount,
            buy_count=buy_count, sell_count=sell_count,
            buy_budget=buy_budget, sell_budget=sell_budget,
            step_size=step_size, min_amount=min_amount,
            min_distance_pct=min_distance_pct,
        )

    def mark_filled(self, order_id: str) -> GridLevel | None:
        """Mark a grid level as filled by order ID."""
        for level in self.state.levels:
            if level.order_id == order_id:
                level.filled = True
                self.state.invalidate()
                logger.info("Grid level filled: %s @ %.2f", level.side, level.price)
                return level
        return None

    def get_opposite_level(self, filled_level: GridLevel) -> GridLevel | None:
        """Get the corresponding opposite level for a filled order (buy->sell, sell->buy)."""
        target_side = "sell" if filled_level.side == "buy" else "buy"
        for level in self.state.levels:
            if level.side == target_side and not level.filled and level.index == filled_level.index:
                return level
        unfilled = [l for l in self.state.levels if l.side == target_side and not l.filled]
        if unfilled:
            if target_side == "sell":
                return min(unfilled, key=lambda l: l.price)
            return max(unfilled, key=lambda l: l.price)
        return None

    def get_levels_to_place(self, sides_allowed: set[str] | None = None) -> list[GridLevel]:
        """Get all levels that need order placement.

        Args:
            sides_allowed: If given, only return levels whose side is in this set.
        """
        out = [l for l in self.state.levels if not l.filled and l.order_id is None]
        if sides_allowed is not None:
            out = [l for l in out if l.side in sides_allowed]
        return out

    def reset(self):
        self.state = GridState(
            mode=GridMode.INFINITY if self.infinity_mode else GridMode.STATIC,
        )
        self.state.invalidate()
