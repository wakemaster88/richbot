"""Grid calculation engine with infinity/trailing grid support."""

from __future__ import annotations

import logging
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

    @property
    def level_id(self) -> str:
        return f"{self.side}_{self.index}_{self.price:.2f}"


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


class GridEngine:
    """Computes and manages grid levels with trailing/infinity support."""

    def __init__(self, grid_count: int = 20, spacing_percent: float = 0.5,
                 amount_per_order: float = 0.001, infinity_mode: bool = True,
                 trail_trigger_percent: float = 1.5):
        self.grid_count = grid_count
        self.spacing_percent = spacing_percent
        self.amount_per_order = amount_per_order
        self.infinity_mode = infinity_mode
        self.trail_trigger_percent = trail_trigger_percent
        self.state = GridState(
            mode=GridMode.INFINITY if infinity_mode else GridMode.STATIC,
        )

    def calculate_grid(self, range_result: RangeResult, current_price: float,
                       dynamic_amount: float | None = None) -> list[GridLevel]:
        """Calculate grid levels within the given range."""
        self.state.range_result = range_result
        self.state.last_price = current_price
        amount = dynamic_amount or self.amount_per_order

        total_levels = self.grid_count
        buy_count = total_levels // 2
        sell_count = total_levels - buy_count

        levels = []

        buy_step = (current_price - range_result.lower) / max(buy_count, 1)
        for i in range(buy_count):
            price = current_price - buy_step * (i + 1)
            if price < range_result.lower:
                price = range_result.lower
            levels.append(GridLevel(
                price=round(price, 2),
                side="buy",
                amount=amount,
                index=i,
            ))

        sell_step = (range_result.upper - current_price) / max(sell_count, 1)
        for i in range(sell_count):
            price = current_price + sell_step * (i + 1)
            if price > range_result.upper:
                price = range_result.upper
            levels.append(GridLevel(
                price=round(price, 2),
                side="sell",
                amount=amount,
                index=i,
            ))

        levels.sort(key=lambda l: l.price)
        self.state.levels = levels
        self.state.invalidate()

        logger.info(
            "Grid calculated: %d levels (buy=%d, sell=%d) in [%.2f, %.2f]",
            len(levels), buy_count, sell_count, range_result.lower, range_result.upper,
        )
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
                   new_range: RangeResult, dynamic_amount: float | None = None) -> list[GridLevel]:
        """Trail the grid in the given direction by recalculating."""
        self.state.shift_count += 1
        logger.info(
            "Trailing grid %s (shift #%d) to new range [%.2f, %.2f]",
            direction, self.state.shift_count, new_range.lower, new_range.upper,
        )
        old_filled = [l for l in self.state.levels if l.filled]
        new_levels = self.calculate_grid(new_range, current_price, dynamic_amount)
        return new_levels

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
        """Get the corresponding opposite level for a filled order (buy→sell, sell→buy)."""
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

    def get_levels_to_place(self) -> list[GridLevel]:
        """Get all levels that need order placement."""
        return [l for l in self.state.levels if not l.filled and l.order_id is None]

    def reset(self):
        self.state = GridState(
            mode=GridMode.INFINITY if self.infinity_mode else GridMode.STATIC,
        )
        self.state.invalidate()
