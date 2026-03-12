"""Trailing Take-Profit engine.

After a fill, instead of immediately placing a fixed counter-order, we track
the price and execute a market order when the price trails back from its
extreme by trail_percent — but only after reaching min_profit_percent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TrailingEntry:
    """A single trailing take-profit tracker for one fill."""
    pair: str
    side: str           # "buy" or "sell" — the side that was FILLED
    entry_price: float
    amount: float
    trail_percent: float
    min_profit_percent: float
    max_loss_percent: float
    created_at: float = field(default_factory=time.time)
    highest: float = 0.0   # for buy fills: highest price seen since fill
    lowest: float = 0.0    # for sell fills: lowest price seen since fill
    triggered: bool = False
    trigger_price: float = 0.0
    trigger_reason: str = ""
    fallback_placed: bool = False
    grid_level_price: float = 0.0   # original grid level price for fallback

    def __post_init__(self):
        if self.side == "buy":
            self.highest = self.entry_price
        else:
            self.lowest = self.entry_price


class TrailingTakeProfit:
    """Manages trailing take-profit entries for all pairs.

    Usage:
        ttp = TrailingTakeProfit()
        ttp.add_entry(pair, side, price, amount, grid_level_price)
        # on each tick:
        triggered = ttp.check(pair, current_price)
        # triggered is a list of TrailingEntry that should be executed
    """

    TRAIL_PERCENT = 0.003       # 0.3% trail from extreme
    MIN_PROFIT_PERCENT = 0.0015 # 0.15% minimum profit before trailing activates
    MAX_LOSS_PERCENT = 0.02     # 2% stop-loss
    FALLBACK_SECONDS = 3600     # 60 minutes before fallback limit order

    def __init__(self, trail_percent: float = TRAIL_PERCENT,
                 min_profit_percent: float = MIN_PROFIT_PERCENT,
                 max_loss_percent: float = MAX_LOSS_PERCENT,
                 fallback_seconds: float = FALLBACK_SECONDS):
        self.trail_percent = trail_percent
        self.min_profit_percent = min_profit_percent
        self.max_loss_percent = max_loss_percent
        self.fallback_seconds = fallback_seconds
        self._entries: list[TrailingEntry] = []

    @property
    def active_count(self) -> int:
        return sum(1 for e in self._entries if not e.triggered and not e.fallback_placed)

    def add_entry(self, pair: str, side: str, entry_price: float,
                  amount: float, grid_level_price: float = 0.0):
        """Register a new trailing TP after a fill."""
        entry = TrailingEntry(
            pair=pair,
            side=side,
            entry_price=entry_price,
            amount=amount,
            trail_percent=self.trail_percent,
            min_profit_percent=self.min_profit_percent,
            max_loss_percent=self.max_loss_percent,
            grid_level_price=grid_level_price or entry_price,
        )
        self._entries.append(entry)
        logger.info(
            "Trailing-TP: %s %s %.8f @ %.2f (trail=%.2f%%, min_profit=%.2f%%)",
            side, pair, amount, entry_price,
            self.trail_percent * 100, self.min_profit_percent * 100,
        )

    def check(self, pair: str, current_price: float) -> tuple[list[TrailingEntry], list[TrailingEntry]]:
        """Check all entries for the given pair against current price.

        Returns:
            (triggered, fallbacks) — triggered entries need market orders,
            fallbacks need limit orders placed.
        """
        now = time.time()
        triggered: list[TrailingEntry] = []
        fallbacks: list[TrailingEntry] = []

        for entry in self._entries:
            if entry.pair != pair or entry.triggered or entry.fallback_placed:
                continue

            if entry.side == "buy":
                self._check_buy_fill(entry, current_price, now, triggered, fallbacks)
            else:
                self._check_sell_fill(entry, current_price, now, triggered, fallbacks)

        return triggered, fallbacks

    def _check_buy_fill(self, entry: TrailingEntry, price: float, now: float,
                        triggered: list, fallbacks: list):
        """After a buy fill, we want to sell at a profit."""
        if price > entry.highest:
            entry.highest = price

        profit_pct = (price - entry.entry_price) / entry.entry_price

        # Stop-loss
        if profit_pct <= -entry.max_loss_percent:
            entry.triggered = True
            entry.trigger_price = price
            entry.trigger_reason = "stop_loss"
            triggered.append(entry)
            return

        trail_from_high = (entry.highest - price) / entry.highest if entry.highest > 0 else 0
        min_profit_reached = profit_pct >= entry.min_profit_percent

        if min_profit_reached and trail_from_high >= entry.trail_percent:
            entry.triggered = True
            entry.trigger_price = price
            entry.trigger_reason = "trailing_tp"
            triggered.append(entry)
            return

        # Fallback: place limit order after timeout
        if now - entry.created_at > self.fallback_seconds and not entry.fallback_placed:
            entry.fallback_placed = True
            fallbacks.append(entry)

    def _check_sell_fill(self, entry: TrailingEntry, price: float, now: float,
                         triggered: list, fallbacks: list):
        """After a sell fill, we want to buy back at a lower price."""
        if entry.lowest == 0 or price < entry.lowest:
            entry.lowest = price

        profit_pct = (entry.entry_price - price) / entry.entry_price

        # Stop-loss (price went up after we sold)
        if profit_pct <= -entry.max_loss_percent:
            entry.triggered = True
            entry.trigger_price = price
            entry.trigger_reason = "stop_loss"
            triggered.append(entry)
            return

        trail_from_low = (price - entry.lowest) / entry.lowest if entry.lowest > 0 else 0
        min_profit_reached = profit_pct >= entry.min_profit_percent

        if min_profit_reached and trail_from_low >= entry.trail_percent:
            entry.triggered = True
            entry.trigger_price = price
            entry.trigger_reason = "trailing_tp"
            triggered.append(entry)
            return

        if now - entry.created_at > self.fallback_seconds and not entry.fallback_placed:
            entry.fallback_placed = True
            fallbacks.append(entry)

    def cleanup(self):
        """Remove completed entries."""
        self._entries = [e for e in self._entries if not e.triggered and not e.fallback_placed]

    def get_entries(self, pair: str | None = None) -> list[TrailingEntry]:
        """Get active entries, optionally filtered by pair."""
        entries = [e for e in self._entries if not e.triggered and not e.fallback_placed]
        if pair:
            entries = [e for e in entries if e.pair == pair]
        return entries

    def to_status(self, pair: str | None = None) -> list[dict]:
        """Serialise active entries for status reporting."""
        return [
            {
                "pair": e.pair, "side": e.side, "entry_price": e.entry_price,
                "amount": e.amount, "highest": e.highest, "lowest": e.lowest,
                "age_sec": round(time.time() - e.created_at),
            }
            for e in self.get_entries(pair)
        ]

    def serialize(self) -> list[dict]:
        """Serialize all active entries for DB persistence."""
        return [
            {
                "pair": e.pair, "side": e.side, "entry_price": e.entry_price,
                "amount": e.amount, "trail_percent": e.trail_percent,
                "min_profit_percent": e.min_profit_percent,
                "max_loss_percent": e.max_loss_percent,
                "created_at": e.created_at, "highest": e.highest,
                "lowest": e.lowest, "grid_level_price": e.grid_level_price,
            }
            for e in self.get_entries()
        ]

    def deserialize(self, data: list[dict]):
        """Restore entries from serialized data."""
        for d in data:
            entry = TrailingEntry(
                pair=d["pair"], side=d["side"],
                entry_price=d["entry_price"], amount=d["amount"],
                trail_percent=d.get("trail_percent", self.trail_percent),
                min_profit_percent=d.get("min_profit_percent", self.min_profit_percent),
                max_loss_percent=d.get("max_loss_percent", self.max_loss_percent),
                created_at=d.get("created_at", time.time()),
                grid_level_price=d.get("grid_level_price", d["entry_price"]),
            )
            entry.highest = d.get("highest", entry.entry_price)
            entry.lowest = d.get("lowest", entry.entry_price)
            self._entries.append(entry)
        if data:
            logger.info("Restored %d trailing-TP entries from saved state", len(data))
