"""Order management with trailing stops and risk integration."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from bot.config import BotConfig
from bot.exchange import Exchange
from bot.grid_engine import GridEngine, GridLevel
from bot.inventory import InventoryTracker
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
    status: str = "open"  # open, partially_filled, filled, cancelled
    fill_price: float = 0.0
    fill_time: float = 0.0
    pnl: float = 0.0
    slippage: float = 0.0
    actual_fee: float = 0.0
    is_maker: bool = True
    filled_amount: float = 0.0
    remaining_amount: float = 0.0
    fills: list = field(default_factory=list)
    _partial_first_seen: float = 0.0

    def __post_init__(self):
        if self.remaining_amount == 0.0:
            self.remaining_amount = self.amount

    @property
    def fill_pct(self) -> float:
        return self.filled_amount / self.amount * 100 if self.amount > 0 else 0.0


class OrderManager:
    """Manages order lifecycle: placement, tracking, fills, and trailing stops."""

    FEE_RATE = 0.001  # 0.1% default; overridden from exchange if available

    def __init__(self, exchange: Exchange, grid_engine: GridEngine,
                 risk_manager: RiskManager, config: BotConfig,
                 inventory: InventoryTracker | None = None):
        self.exchange = exchange
        self.grid = grid_engine
        self.risk = risk_manager
        self.config = config
        self.inventory = inventory or InventoryTracker()
        self.orders: dict[str, ManagedOrder] = {}
        self._fill_callbacks: list = []
        self._partial_fill_callbacks: list = []
        self.last_fail_reason: str = ""
        self._consecutive_fails: int = 0
        self._paused_levels: set[str] = set()  # level_ids paused due to balance issues
        self._pause_until: float = 0  # timestamp until retries are suppressed
        self._last_filter_log: tuple[bool, bool] = (True, True)  # track to log once on change
        self._placing_lock = asyncio.Lock()

    def on_fill(self, callback):
        """Register a callback for fill events: callback(managed_order)."""
        self._fill_callbacks.append(callback)

    def on_partial_fill(self, callback):
        """Register a callback for partial fill events: callback(managed_order)."""
        self._partial_fill_callbacks.append(callback)

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
        """Cancel all open/partially_filled orders for a symbol."""
        cancelled = 0
        for oid, order in list(self.orders.items()):
            if order.symbol == symbol and order.status in ("open", "partially_filled"):
                try:
                    await self.exchange.async_cancel_order(oid, symbol)
                    order.status = "cancelled"
                    cancelled += 1
                except Exception as e:
                    logger.warning("Cancel failed for %s: %s", oid, e)
        self.reset_paused_levels()
        logger.info("Cancelled %d orders for %s", cancelled, symbol)

    PARTIAL_FILL_COMPLETE_PCT = 80.0
    PARTIAL_FILL_STALE_PCT = 20.0
    PARTIAL_FILL_STALE_SEC = 300.0

    async def _fetch_real_fill_price(self, managed: ManagedOrder) -> None:
        """Fetch actual fill price via Exchange API and compute slippage."""
        try:
            detail = await self.exchange.async_fetch_order(managed.symbol, managed.order_id)
            exec_qty = detail.get("executed_qty", 0)
            if exec_qty > 0:
                managed.fill_price = detail["avg_price"]
                managed.filled_amount = exec_qty
                managed.remaining_amount = managed.amount - exec_qty
        except Exception as e:
            logger.debug("fetch_order fallback for %s: %s", managed.order_id, e)
            if managed.fill_price == 0:
                managed.fill_price = managed.price

        try:
            trades = await self.exchange.async_fetch_my_trades(managed.symbol, managed.order_id)
            if trades:
                total_commission = 0.0
                maker_fills = 0
                managed.fills = []
                for t in trades:
                    if t["commission_asset"] == managed.symbol.split("/")[1]:
                        total_commission += t["commission"]
                    else:
                        total_commission += t["commission"] * t["price"]
                    if t["is_maker"]:
                        maker_fills += 1
                    managed.fills.append({
                        "price": t["price"], "qty": t["qty"],
                        "commission": t["commission"],
                        "is_maker": t["is_maker"],
                    })
                managed.actual_fee = total_commission
                managed.is_maker = maker_fills > len(trades) / 2
        except Exception as e:
            logger.debug("fetch_my_trades fallback for %s: %s", managed.order_id, e)

        if managed.price > 0:
            managed.slippage = abs(managed.fill_price - managed.price) / managed.price

    async def _fetch_order_status(self, managed: ManagedOrder) -> dict | None:
        """Fetch current order status from exchange. Returns detail dict or None."""
        try:
            return await self.exchange.async_fetch_order(managed.symbol, managed.order_id)
        except Exception as e:
            logger.debug("fetch_order_status failed for %s: %s", managed.order_id, e)
            return None

    def _finalize_fill(self, managed: ManagedOrder) -> None:
        """Finalize a fully filled order: mark grid, compute PnL, fire callbacks."""
        managed.status = "filled"
        managed.filled_amount = managed.amount
        managed.remaining_amount = 0.0

        self.grid.mark_filled(managed.order_id)
        level = managed.grid_level
        level.partial_fills.append((managed.fill_time, managed.fill_price, managed.filled_amount))

        pnl = self._calculate_pnl(managed)
        managed.pnl = pnl
        self.risk.record_trade(pnl)

        for cb in self._fill_callbacks:
            try:
                cb(managed)
            except Exception as e:
                logger.error("Fill callback error: %s", e)

        slip_bps = managed.slippage * 10_000
        logger.info(
            "Fill detected: %s %s @ %.2f (limit: %.2f, slip: %.1fbps, fee: %.6f, PnL: %.4f)",
            managed.side, managed.symbol, managed.fill_price, managed.price,
            slip_bps, managed.actual_fee, pnl,
        )

    def _handle_partial_completion(self, managed: ManagedOrder) -> None:
        """Treat a partial fill >= threshold as complete."""
        managed.fill_time = time.time()
        managed.amount = managed.filled_amount
        managed.remaining_amount = 0.0
        logger.info(
            "Partial fill >= %.0f%% treated as complete: %s %s %.6f/%.6f @ %.2f",
            self.PARTIAL_FILL_COMPLETE_PCT, managed.side, managed.symbol,
            managed.filled_amount, managed.amount, managed.fill_price,
        )
        self._finalize_fill(managed)

    async def check_fills(self, symbol: str) -> list[ManagedOrder]:
        """Poll for filled orders with partial-fill awareness."""
        filled = []
        try:
            open_orders = await self.exchange.async_fetch_open_orders(symbol)
            open_ids = {o["id"] for o in open_orders}

            for oid, managed in list(self.orders.items()):
                if managed.symbol != symbol:
                    continue
                if managed.status not in ("open", "partially_filled"):
                    continue

                still_open = oid in open_ids

                if still_open:
                    detail = await self._fetch_order_status(managed)
                    if detail is None:
                        continue
                    exec_qty = detail.get("executed_qty", 0)
                    if exec_qty <= 0:
                        continue

                    managed.filled_amount = exec_qty
                    managed.remaining_amount = managed.amount - exec_qty
                    managed.fill_price = detail["avg_price"]

                    fill_pct = managed.fill_pct

                    if managed.status == "open":
                        managed.status = "partially_filled"
                        managed._partial_first_seen = time.time()
                        managed.grid_level.partial_fills.append(
                            (time.time(), detail["avg_price"], exec_qty)
                        )
                        logger.info(
                            "Partial fill: %s %s %.6f/%.6f (%.1f%%) @ %.2f",
                            managed.side, symbol, exec_qty, managed.amount,
                            fill_pct, managed.fill_price,
                        )
                        for cb in self._partial_fill_callbacks:
                            try:
                                cb(managed)
                            except Exception:
                                pass

                    if fill_pct >= self.PARTIAL_FILL_COMPLETE_PCT:
                        try:
                            await self.exchange.async_cancel_order(oid, symbol)
                        except Exception as e:
                            logger.debug("Cancel rest of partial %s: %s", oid, e)
                        await self._fetch_real_fill_price(managed)
                        self._handle_partial_completion(managed)
                        filled.append(managed)

                    elif (fill_pct < self.PARTIAL_FILL_STALE_PCT
                          and managed._partial_first_seen > 0
                          and time.time() - managed._partial_first_seen > self.PARTIAL_FILL_STALE_SEC):
                        logger.info(
                            "Stale partial (<%.0f%% after %ds): cancel+replace %s %s",
                            self.PARTIAL_FILL_STALE_PCT,
                            int(self.PARTIAL_FILL_STALE_SEC),
                            managed.side, symbol,
                        )
                        try:
                            await self.exchange.async_cancel_order(oid, symbol)
                        except Exception as e:
                            logger.warning("Cancel stale partial %s: %s", oid, e)
                            continue

                        if managed.filled_amount > 0:
                            await self._fetch_real_fill_price(managed)
                            managed.amount = managed.filled_amount
                            managed.remaining_amount = 0.0
                            managed.fill_time = time.time()
                            self._finalize_fill(managed)
                            filled.append(managed)

                        level = managed.grid_level
                        remaining = level.amount - managed.filled_amount
                        if remaining > 0:
                            level.order_id = None
                            level.amount = remaining
                            logger.info(
                                "Re-queueing remaining %.6f for %s @ %.2f",
                                remaining, level.side, level.price,
                            )

                else:
                    managed.fill_time = time.time()
                    await self._fetch_real_fill_price(managed)

                    if managed.filled_amount > 0 and managed.filled_amount < managed.amount:
                        if managed.fill_pct >= self.PARTIAL_FILL_COMPLETE_PCT:
                            self._handle_partial_completion(managed)
                        else:
                            managed.amount = managed.filled_amount
                            managed.remaining_amount = 0.0
                            self._finalize_fill(managed)
                    else:
                        managed.filled_amount = managed.amount
                        managed.remaining_amount = 0.0
                        self._finalize_fill(managed)

                    filled.append(managed)

        except Exception as e:
            logger.error("Error checking fills: %s", e)

        if filled:
            self.reset_paused_levels()

        return filled

    def process_ws_fill(self, order_data: dict) -> ManagedOrder | None:
        """Process a fill event from WebSocket.

        Handles both PARTIALLY_FILLED and FILLED statuses.
        Returns the ManagedOrder only when it's fully filled (or treated as such).
        For partial fills, updates internal state and fires partial callbacks.
        """
        oid = order_data.get("id", "")
        if oid not in self.orders:
            return None

        managed = self.orders[oid]
        if managed.status == "filled":
            return None

        order_status = order_data.get("order_status", "FILLED")
        cum_quote = float(order_data.get("cum_quote_qty", 0))
        cum_qty = float(order_data.get("cum_qty", 0))
        last_qty = float(order_data.get("last_qty", 0))
        last_price = float(order_data.get("last_price", 0))

        if cum_quote > 0 and cum_qty > 0:
            managed.fill_price = cum_quote / cum_qty
        else:
            managed.fill_price = float(order_data.get("price", managed.price))

        managed.filled_amount = cum_qty if cum_qty > 0 else managed.amount
        managed.remaining_amount = managed.amount - managed.filled_amount

        if last_qty > 0 and last_price > 0:
            managed.fills.append({
                "price": last_price, "qty": last_qty,
                "time": time.time(),
            })
            managed.grid_level.partial_fills.append(
                (time.time(), last_price, last_qty)
            )

        ws_commission = float(order_data.get("commission", 0))
        if ws_commission > 0:
            comm_asset = order_data.get("commission_asset", "")
            if comm_asset == managed.symbol.split("/")[1]:
                managed.actual_fee += ws_commission
            else:
                managed.actual_fee += ws_commission * managed.fill_price

        managed.is_maker = order_data.get("is_maker", True)

        if managed.price > 0:
            managed.slippage = abs(managed.fill_price - managed.price) / managed.price

        is_full = order_status == "FILLED"
        is_near_complete = managed.fill_pct >= self.PARTIAL_FILL_COMPLETE_PCT

        if is_full or is_near_complete:
            managed.status = "filled"
            managed.fill_time = time.time()
            if not is_full:
                managed.amount = managed.filled_amount
                managed.remaining_amount = 0.0

            managed.pnl = self._calculate_pnl(managed)
            self.grid.mark_filled(oid)
            self.risk.record_trade(managed.pnl)

            for cb in self._fill_callbacks:
                try:
                    cb(managed)
                except Exception as e:
                    logger.error("Fill callback error: %s", e)

            return managed

        managed.status = "partially_filled"
        if managed._partial_first_seen == 0:
            managed._partial_first_seen = time.time()
        logger.info(
            "WS partial fill: %s %s %.6f/%.6f (%.1f%%) @ %.2f",
            managed.side, managed.symbol, managed.filled_amount,
            managed.amount, managed.fill_pct, managed.fill_price,
        )
        for cb in self._partial_fill_callbacks:
            try:
                cb(managed)
            except Exception:
                pass
        return None

    async def async_enrich_ws_fill(self, managed: ManagedOrder) -> None:
        """Post-WS enrichment: fetch real fill details from REST API."""
        if managed.status != "filled":
            return
        await self._fetch_real_fill_price(managed)
        managed.pnl = self._calculate_pnl(managed)

    def _calculate_pnl(self, order: ManagedOrder) -> float:
        """Calculate realized PnL via InventoryTracker (position-based, not index-based).

        Buys increase inventory and cost basis; sells realize PnL against
        the weighted-average cost. Works correctly after grid trail/reset.
        """
        qty = order.filled_amount if order.filled_amount > 0 else order.amount
        fee = order.actual_fee if order.actual_fee > 0 else (
            order.fill_price * qty * self.FEE_RATE
        )

        if order.side == "buy":
            return self.inventory.record_buy(order.symbol, order.fill_price, qty, fee)
        else:
            return self.inventory.record_sell(order.symbol, order.fill_price, qty, fee)

    def check_trailing_stops(self, current_price: float, pair: str = "") -> list[str]:
        """Check trailing stops for a specific pair and return triggered level IDs."""
        return self.risk.check_trailing_stops(current_price, pair=pair)

    def get_open_orders(self, symbol: str | None = None) -> list[ManagedOrder]:
        orders = [o for o in self.orders.values() if o.status in ("open", "partially_filled")]
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
        partial = [o for o in self.orders.values()
                   if o.status == "partially_filled"
                   and (symbol is None or o.symbol == symbol)]
        total_pnl = sum(o.pnl for o in filled)

        slippages = [o.slippage for o in filled if o.slippage > 0]
        avg_slippage = sum(slippages) / len(slippages) if slippages else 0.0
        max_slippage = max(slippages) if slippages else 0.0
        total_slippage_cost = sum(
            o.slippage * o.fill_price * o.amount for o in filled if o.slippage > 0
        )

        return {
            "open_orders": len(open_orders),
            "filled_orders": len(filled),
            "partially_filled": len(partial),
            "total_pnl": total_pnl,
            "buy_fills": len([o for o in filled if o.side == "buy"]),
            "sell_fills": len([o for o in filled if o.side == "sell"]),
            "avg_slippage_bps": round(avg_slippage * 10_000, 2),
            "max_slippage_bps": round(max_slippage * 10_000, 2),
            "total_slippage_cost": round(total_slippage_cost, 6),
        }
