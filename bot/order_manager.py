"""Order management with trailing stops and risk integration."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from bot.config import BotConfig
from bot.exchange import Exchange
from bot.grid_engine import GridEngine, GridLevel
from bot.risk_manager import RiskManager

logger = logging.getLogger(__name__)


@dataclass
class ManagedOrder:
    order_id: str
    symbol: str
    side: str
    price: float
    amount: float
    grid_level: GridLevel
    status: str = "open"  # open, filled, cancelled
    fill_price: float = 0.0
    fill_time: float = 0.0
    pnl: float = 0.0


class OrderManager:
    """Manages order lifecycle: placement, tracking, fills, and trailing stops."""

    FEE_RATE = 0.001  # 0.1% default; overridden from exchange if available

    def __init__(self, exchange: Exchange, grid_engine: GridEngine,
                 risk_manager: RiskManager, config: BotConfig):
        self.exchange = exchange
        self.grid = grid_engine
        self.risk = risk_manager
        self.config = config
        self.orders: dict[str, ManagedOrder] = {}
        self._fill_callbacks: list = []
        self._round_trips: dict[int, float] = {}  # grid_index -> buy_price
        self.last_fail_reason: str = ""

    def on_fill(self, callback):
        """Register a callback for fill events: callback(managed_order)."""
        self._fill_callbacks.append(callback)

    @staticmethod
    def create_managed(order_id: str, symbol: str, level: GridLevel) -> ManagedOrder:
        return ManagedOrder(
            order_id=order_id, symbol=symbol, side=level.side,
            price=level.price, amount=level.amount, grid_level=level,
        )

    async def place_grid_orders(self, symbol: str) -> list[ManagedOrder]:
        """Place all pending grid orders."""
        can_trade, reason = self.risk.can_trade()
        if not can_trade:
            logger.warning("Trading paused: %s", reason)
            return []

        levels = self.grid.get_levels_to_place()
        placed = []
        self.last_fail_reason = ""

        for level in levels:
            try:
                if level.side == "buy":
                    order = await self.exchange.async_create_limit_buy(
                        symbol, level.amount, level.price
                    )
                else:
                    order = await self.exchange.async_create_limit_sell(
                        symbol, level.amount, level.price
                    )

                level.order_id = order["id"]
                managed = ManagedOrder(
                    order_id=order["id"],
                    symbol=symbol,
                    side=level.side,
                    price=level.price,
                    amount=level.amount,
                    grid_level=level,
                )
                self.orders[order["id"]] = managed
                placed.append(managed)

                self.risk.add_trailing_stop(level.level_id, level.side, level.price)

                logger.info("Order placed: %s %s %.6f @ %.2f [%s]",
                            level.side, symbol, level.amount, level.price, order["id"])

            except Exception as e:
                self.last_fail_reason = str(e)
                logger.error("Failed to place %s order @ %.2f: %s", level.side, level.price, e)

        return placed

    async def cancel_all(self, symbol: str):
        """Cancel all open orders for a symbol."""
        cancelled = 0
        for oid, order in list(self.orders.items()):
            if order.symbol == symbol and order.status == "open":
                try:
                    await self.exchange.async_cancel_order(oid, symbol)
                    order.status = "cancelled"
                    cancelled += 1
                except Exception as e:
                    logger.warning("Cancel failed for %s: %s", oid, e)
        logger.info("Cancelled %d orders for %s", cancelled, symbol)

    async def check_fills(self, symbol: str) -> list[ManagedOrder]:
        """Poll for filled orders (used when WebSocket is not available)."""
        filled = []
        try:
            open_orders = await self.exchange.async_fetch_open_orders(symbol)
            open_ids = {o["id"] for o in open_orders}

            for oid, managed in list(self.orders.items()):
                if managed.status == "open" and managed.symbol == symbol and oid not in open_ids:
                    managed.status = "filled"
                    managed.fill_time = time.time()
                    managed.fill_price = managed.price

                    self.grid.mark_filled(oid)

                    pnl = self._calculate_pnl(managed)
                    managed.pnl = pnl
                    self.risk.record_trade(pnl)

                    filled.append(managed)
                    for cb in self._fill_callbacks:
                        try:
                            cb(managed)
                        except Exception as e:
                            logger.error("Fill callback error: %s", e)

                    logger.info("Fill detected: %s %s @ %.2f (PnL: %.4f)",
                                managed.side, symbol, managed.fill_price, pnl)

        except Exception as e:
            logger.error("Error checking fills: %s", e)

        return filled

    def process_ws_fill(self, order_data: dict) -> ManagedOrder | None:
        """Process a fill event from WebSocket."""
        oid = order_data.get("id", "")
        if oid not in self.orders:
            return None

        managed = self.orders[oid]
        if managed.status != "open":
            return None

        managed.status = "filled"
        managed.fill_time = time.time()
        managed.fill_price = float(order_data.get("price", managed.price))
        managed.pnl = self._calculate_pnl(managed)

        self.grid.mark_filled(oid)
        self.risk.record_trade(managed.pnl)

        for cb in self._fill_callbacks:
            try:
                cb(managed)
            except Exception as e:
                logger.error("Fill callback error: %s", e)

        return managed

    def _calculate_pnl(self, order: ManagedOrder) -> float:
        """Calculate realized PnL for a filled grid order.
        Buy fills record cost; sell fills realize the round-trip profit minus fees."""
        fee = order.fill_price * order.amount * self.FEE_RATE
        idx = order.grid_level.index

        if order.side == "buy":
            self._round_trips[idx] = order.fill_price
            return -fee

        buy_price = self._round_trips.pop(idx, None)
        if buy_price is not None:
            gross = (order.fill_price - buy_price) * order.amount
            return gross - 2 * fee
        return -fee

    def check_trailing_stops(self, current_price: float) -> list[str]:
        """Check trailing stops and return triggered level IDs."""
        return self.risk.check_trailing_stops(current_price)

    def get_open_orders(self, symbol: str | None = None) -> list[ManagedOrder]:
        orders = [o for o in self.orders.values() if o.status == "open"]
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    def get_filled_orders(self, symbol: str | None = None) -> list[ManagedOrder]:
        orders = [o for o in self.orders.values() if o.status == "filled"]
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    def get_stats(self, symbol: str | None = None) -> dict:
        open_orders = self.get_open_orders(symbol)
        filled = self.get_filled_orders(symbol)
        total_pnl = sum(o.pnl for o in filled)
        return {
            "open_orders": len(open_orders),
            "filled_orders": len(filled),
            "total_pnl": total_pnl,
            "buy_fills": len([o for o in filled if o.side == "buy"]),
            "sell_fills": len([o for o in filled if o.side == "sell"]),
        }
