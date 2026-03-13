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
        self._consecutive_fails: int = 0
        self._paused_levels: set[str] = set()  # level_ids paused due to balance issues
        self._pause_until: float = 0  # timestamp until retries are suppressed
        self._last_filter_log: tuple[bool, bool] = (True, True)  # track to log once on change
        self._placing_lock = asyncio.Lock()

    def on_fill(self, callback):
        """Register a callback for fill events: callback(managed_order)."""
        self._fill_callbacks.append(callback)

    @staticmethod
    def create_managed(order_id: str, symbol: str, level: GridLevel) -> ManagedOrder:
        return ManagedOrder(
            order_id=order_id, symbol=symbol, side=level.side,
            price=level.price, amount=level.amount, grid_level=level,
        )

    def reset_paused_levels(self):
        """Call after a fill or balance change to allow retrying failed orders."""
        self._paused_levels.clear()
        self._pause_until = 0
        self._consecutive_fails = 0

    async def place_grid_orders(self, symbol: str,
                                entry_filter: dict | None = None) -> list[ManagedOrder]:
        """Place all pending grid orders.

        Uses an async lock to prevent concurrent placement (race between
        _poll_loop, _auto_adjust_grid, and update_tick).
        """
        async with self._placing_lock:
            return await self._place_grid_orders_inner(symbol, entry_filter)

    async def _place_grid_orders_inner(self, symbol: str,
                                       entry_filter: dict | None = None) -> list[ManagedOrder]:
        can_trade, reason = self.risk.can_trade()
        if not can_trade:
            logger.warning("Trading paused: %s", reason)
            return []

        now = time.time()
        if now < self._pause_until:
            return []

        allow_buys = (entry_filter or {}).get("allow_buys", True)
        allow_sells = (entry_filter or {}).get("allow_sells", True)

        if (allow_buys, allow_sells) != self._last_filter_log:
            blocked = []
            if not allow_buys:
                blocked.append("Buys")
            if not allow_sells:
                blocked.append("Sells")
            if blocked:
                logger.info("Entry-Filter: %s blockiert (Regime/RSI)", " + ".join(blocked))
            else:
                logger.info("Entry-Filter: alle Seiten wieder erlaubt")
            self._last_filter_log = (allow_buys, allow_sells)

        sides_allowed: set[str] | None = None
        if not allow_buys or not allow_sells:
            sides_allowed = set()
            if allow_buys:
                sides_allowed.add("buy")
            if allow_sells:
                sides_allowed.add("sell")

        MAX_OPEN_ORDERS = 20
        open_count = len(self.get_open_orders(symbol))
        if open_count >= MAX_OPEN_ORDERS:
            self.last_fail_reason = f"Max offene Orders erreicht ({MAX_OPEN_ORDERS})"
            return []

        existing_sigs: set[str] = set()
        for o in self.orders.values():
            if o.status == "open" and o.symbol == symbol:
                existing_sigs.add(f"{o.side}_{o.price:.8f}")

        levels = self.grid.get_levels_to_place(sides_allowed=sides_allowed)
        placed = []
        self.last_fail_reason = ""
        failures_this_round = 0

        for level in levels:
            if level.level_id in self._paused_levels:
                continue

            if open_count >= MAX_OPEN_ORDERS:
                self.last_fail_reason = f"Max offene Orders ({MAX_OPEN_ORDERS})"
                break

            price_sig = f"{level.side}_{level.price:.8f}"
            if price_sig in existing_sigs:
                level.order_id = "__dup_skipped__"
                logger.debug("Duplikat uebersprungen: %s @ %.2f", level.side, level.price)
                continue
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
                open_count += 1
                existing_sigs.add(price_sig)

                self.risk.add_trailing_stop(level.level_id, level.side, level.price, pair=symbol)

                logger.info("Order placed: %s %s %.6f @ %.2f [%s]",
                            level.side, symbol, level.amount, level.price, order["id"])

            except Exception as e:
                err = str(e).lower()
                self.last_fail_reason = str(e)
                failures_this_round += 1

                if "insufficient balance" in err or "notional" in err:
                    self._paused_levels.add(level.level_id)
                    if failures_this_round <= 2:
                        logger.warning("Order %s @ %.2f pausiert (Balance): %s",
                                       level.side, level.price, e)
                elif failures_this_round <= 3:
                    logger.error("Failed to place %s order @ %.2f: %s",
                                 level.side, level.price, e)

        if placed:
            self._consecutive_fails = 0
        elif failures_this_round > 0:
            self._consecutive_fails += 1
            if self._consecutive_fails >= 3:
                self._pause_until = now + 120
                logger.warning("Order-Placement 3x fehlgeschlagen — Pause fuer 2 Min")
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
        self.reset_paused_levels()
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

        if filled:
            self.reset_paused_levels()

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

    def check_trailing_stops(self, current_price: float, pair: str = "") -> list[str]:
        """Check trailing stops for a specific pair and return triggered level IDs."""
        return self.risk.check_trailing_stops(current_price, pair=pair)

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
