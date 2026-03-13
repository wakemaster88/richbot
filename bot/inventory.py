"""Position-based inventory tracking — replaces index-based _round_trips.

Tracks cost basis using weighted-average-cost (WAC) method.
Survives grid trail/reset because it's price-based, not index-based.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 1000


@dataclass
class TradeEntry:
    timestamp: float
    side: str
    price: float
    amount: float
    fee: float
    pnl: float


@dataclass
class PairInventory:
    pair: str
    base_inventory: float = 0.0
    total_cost: float = 0.0
    realized_pnl: float = 0.0
    total_fees: float = 0.0
    trade_count: int = 0
    buy_count: int = 0
    sell_count: int = 0
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_LIMIT))

    @property
    def avg_cost_basis(self) -> float:
        if self.base_inventory <= 0:
            return 0.0
        return self.total_cost / self.base_inventory

    def unrealized_pnl(self, current_price: float) -> float:
        if self.base_inventory <= 0:
            return 0.0
        return (current_price - self.avg_cost_basis) * self.base_inventory

    @property
    def inventory_value_at_cost(self) -> float:
        return self.total_cost

    def market_value(self, current_price: float) -> float:
        return self.base_inventory * current_price


class InventoryTracker:
    """Tracks position inventory per pair using weighted-average-cost."""

    def __init__(self):
        self._pairs: dict[str, PairInventory] = {}

    def _get(self, pair: str) -> PairInventory:
        if pair not in self._pairs:
            self._pairs[pair] = PairInventory(pair=pair)
        return self._pairs[pair]

    def record_buy(self, pair: str, price: float, amount: float, fee: float) -> float:
        """Record a buy fill. Returns realized PnL (always -fee for buys)."""
        inv = self._get(pair)
        inv.base_inventory += amount
        inv.total_cost += price * amount + fee
        inv.total_fees += fee
        inv.trade_count += 1
        inv.buy_count += 1
        pnl = -fee
        inv.realized_pnl += pnl
        inv.history.append(TradeEntry(
            timestamp=time.time(), side="buy",
            price=price, amount=amount, fee=fee, pnl=pnl,
        ))
        logger.debug(
            "INV %s BUY %.8f @ %.2f | inv=%.8f avg=%.2f cost=%.4f",
            pair, amount, price, inv.base_inventory, inv.avg_cost_basis, inv.total_cost,
        )
        return pnl

    def record_sell(self, pair: str, price: float, amount: float, fee: float) -> float:
        """Record a sell fill. Returns realized PnL for this trade."""
        inv = self._get(pair)
        avg_cost = inv.avg_cost_basis

        gross = (price - avg_cost) * amount if avg_cost > 0 else 0.0
        pnl = gross - fee

        cost_removed = avg_cost * amount
        inv.base_inventory = max(0.0, inv.base_inventory - amount)
        inv.total_cost = max(0.0, inv.total_cost - cost_removed)

        if inv.base_inventory < 1e-12:
            inv.base_inventory = 0.0
            inv.total_cost = 0.0

        inv.total_fees += fee
        inv.trade_count += 1
        inv.sell_count += 1
        inv.realized_pnl += pnl
        inv.history.append(TradeEntry(
            timestamp=time.time(), side="sell",
            price=price, amount=amount, fee=fee, pnl=pnl,
        ))
        logger.debug(
            "INV %s SELL %.8f @ %.2f (avg_cost=%.2f) | pnl=%.6f inv=%.8f",
            pair, amount, price, avg_cost, pnl, inv.base_inventory,
        )
        return pnl

    def mark_to_market(self, pair: str, current_price: float) -> dict:
        """Return current position metrics for a pair."""
        inv = self._get(pair)
        unrealized = inv.unrealized_pnl(current_price)
        return {
            "base_inventory": round(inv.base_inventory, 8),
            "avg_cost_basis": round(inv.avg_cost_basis, 2),
            "total_cost": round(inv.total_cost, 4),
            "market_value": round(inv.market_value(current_price), 4),
            "unrealized_pnl": round(unrealized, 4),
            "realized_pnl": round(inv.realized_pnl, 4),
            "total_pnl": round(inv.realized_pnl + unrealized, 4),
            "total_fees": round(inv.total_fees, 6),
            "trade_count": inv.trade_count,
            "buy_count": inv.buy_count,
            "sell_count": inv.sell_count,
        }

    def get_inventory(self, pair: str) -> PairInventory:
        return self._get(pair)

    def serialize(self) -> dict:
        """Serialize all pair inventories for state persistence."""
        out: dict[str, dict] = {}
        for pair, inv in self._pairs.items():
            out[pair] = {
                "base_inventory": inv.base_inventory,
                "total_cost": inv.total_cost,
                "realized_pnl": inv.realized_pnl,
                "total_fees": inv.total_fees,
                "trade_count": inv.trade_count,
                "buy_count": inv.buy_count,
                "sell_count": inv.sell_count,
            }
        return out

    @classmethod
    def deserialize(cls, data: dict) -> InventoryTracker:
        """Restore InventoryTracker from persisted state."""
        tracker = cls()
        for pair, vals in (data or {}).items():
            inv = tracker._get(pair)
            inv.base_inventory = vals.get("base_inventory", 0.0)
            inv.total_cost = vals.get("total_cost", 0.0)
            inv.realized_pnl = vals.get("realized_pnl", 0.0)
            inv.total_fees = vals.get("total_fees", 0.0)
            inv.trade_count = vals.get("trade_count", 0)
            inv.buy_count = vals.get("buy_count", 0)
            inv.sell_count = vals.get("sell_count", 0)
            logger.info(
                "Inventory restored %s: %.8f @ avg %.2f (realized: %.4f)",
                pair, inv.base_inventory, inv.avg_cost_basis, inv.realized_pnl,
            )
        return tracker
