"""Multi-pair orchestrator: runs grid bots for multiple trading pairs concurrently.

Pi-optimized: periodic GC, bounded buffers, reduced fetch limits.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import time

import pandas as pd

from bot.capital_allocator import CapitalAllocator, AllocationResult
from bot.circuit_breaker import CircuitBreaker, CBLevel
from bot.cloud_sync import CloudSync
from bot.config import BotConfig
from bot.correlation import CorrelationMonitor
from bot.dynamic_range import compute_dynamic_range, detect_range_breakout, shift_range, RangeResult
from bot.exchange import Exchange
from bot.fee_engine import FeeEngine
from bot.grid_engine import GridEngine
from bot.inventory import InventoryTracker
from bot.inventory_skew import InventorySkew
from bot.ml_predictor import LSTMPredictor
from bot.multi_timeframe import MultiTimeframe
from bot.order_manager import OrderManager
from bot.performance_tracker import PerformanceTracker, TradeRecord
from bot.regime_detector import Regime, RegimeDetector
from bot.risk_manager import RiskManager
from bot.self_optimizer import SelfOptimizer
from bot.telegram_bot import TelegramNotifier
from bot.news_sentiment import NewsSentiment
from bot.rl_optimizer import GridBandit, compute_reward
from bot.trailing_tp import TrailingTakeProfit

try:
    from bot.ws_client import WebSocketClient
except ImportError:
    WebSocketClient = None  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)


class PairBot:
    """Grid bot instance for a single trading pair."""

    def __init__(self, pair: str, config: BotConfig, exchange: Exchange,
                 risk_manager: RiskManager, tracker: PerformanceTracker,
                 telegram: TelegramNotifier, ml_predictor: LSTMPredictor | None = None,
                 cloud: CloudSync | None = None,
                 fee_engine: FeeEngine | None = None,
                 inventory: InventoryTracker | None = None,
                 inventory_skew: InventorySkew | None = None,
                 circuit_breaker: CircuitBreaker | None = None):
        self.pair = pair
        self.config = config
        self.exchange = exchange
        self.risk = risk_manager
        self.tracker = tracker
        self.telegram = telegram
        self.ml = ml_predictor
        self.cloud = cloud
        self.fee_engine = fee_engine
        self.inventory = inventory or InventoryTracker()
        self.inv_skew = inventory_skew or InventorySkew()
        self.cb = circuit_breaker or CircuitBreaker()

        self.pair_grid_count = 4  # will be set by CapitalAllocator
        self.pair_amount = 0.0
        self.pair_buy_count = 2
        self.pair_sell_count = 2
        self.pair_step_size = 0.00001
        self.pair_min_amount = 0.0
        self.last_allocation: AllocationResult | None = None
        self.regime = RegimeDetector()
        self.mtf = MultiTimeframe(pair)

        self.grid = GridEngine(
            grid_count=self.pair_grid_count,
            spacing_percent=config.grid.spacing_percent,
            amount_per_order=config.grid.amount_per_order,
            infinity_mode=config.grid.infinity_mode,
            trail_trigger_percent=config.grid.trail_trigger_percent,
        )
        self.order_mgr = OrderManager(exchange, self.grid, risk_manager, config, inventory=self.inventory)
        self.current_range: RangeResult | None = None
        self.current_price: float = 0.0
        self.last_prediction: dict | None = None
        self._running = False
        self._grid_issue: str = ""
        self._trail_cooldown_until: float = 0.0
        self._sell_drain: list[tuple[float, float]] = []  # (timestamp, base_amount_sold)

        self.quote = pair.split("/")[1] if "/" in pair else "USDT"
        self.base = pair.split("/")[0] if "/" in pair else pair
        self.order_mgr.on_fill(self._on_fill)

    def _on_fill(self, managed_order):
        """Handle order fill events (full or partial-treated-as-full)."""
        fill_qty = managed_order.filled_amount if managed_order.filled_amount > 0 else managed_order.amount

        if managed_order.side == "sell":
            self.record_sell_drain(fill_qty)

        actual_fee = managed_order.actual_fee if managed_order.actual_fee > 0 else (
            managed_order.fill_price * fill_qty * OrderManager.FEE_RATE
        )
        was_partial = len(managed_order.fills) > 1 or managed_order.fill_pct < 100
        trade = TradeRecord(
            timestamp=managed_order.fill_time,
            pair=self.pair,
            side=managed_order.side,
            price=managed_order.grid_level.price,
            amount=fill_qty,
            fee=actual_fee,
            pnl=managed_order.pnl,
            grid_level=managed_order.grid_level.price,
            order_id=managed_order.order_id,
            fill_price=managed_order.fill_price,
            slippage=managed_order.slippage,
            actual_fee=actual_fee,
            is_maker=managed_order.is_maker,
        )
        self.tracker.record_trade(trade)

        slip_bps = managed_order.slippage * 10_000
        asyncio.create_task(
            self.telegram.alert_fill(
                self.pair, managed_order.side, managed_order.fill_price,
                fill_qty, managed_order.pnl,
            )
        )

        if self.cloud and self.cloud.connected:
            asyncio.create_task(self.cloud.sync_trade(trade))
            summary = self.tracker.get_summary(self.pair)
            value = managed_order.fill_price * fill_qty
            asyncio.create_task(self.cloud.log_event(
                "trade", f"{'Kauf' if managed_order.side == 'buy' else 'Verkauf'} {self.pair} @ {managed_order.fill_price:.2f}",
                {"side": managed_order.side, "price": managed_order.fill_price,
                 "limit_price": managed_order.price,
                 "amount": fill_qty, "value": round(value, 4),
                 "fee": round(actual_fee, 6), "pnl": managed_order.pnl,
                 "slippage_bps": round(slip_bps, 2),
                 "is_maker": managed_order.is_maker,
                 "partial_fill": was_partial,
                 "fill_pct": round(managed_order.fill_pct, 1),
                 "num_fills": len(managed_order.fills),
                 "cum_pnl": round(summary["realized_pnl"], 4),
                 "trade_nr": summary["trade_count"],
                 "win": managed_order.pnl > 0},
            ))

            if slip_bps > 5.0:
                asyncio.create_task(self.cloud.log_event(
                    "warning",
                    f"Hohe Slippage bei {self.pair}: {slip_bps:.1f}bps",
                    {"pair": self.pair, "slippage_bps": round(slip_bps, 2),
                     "limit_price": managed_order.price,
                     "fill_price": managed_order.fill_price},
                ))

    def apply_allocation(self, alloc: AllocationResult, step_size: float = 0.00001,
                         min_amount: float = 0.0):
        """Apply an AllocationResult to this pair bot's grid config."""
        self.last_allocation = alloc
        self.pair_grid_count = alloc.grid_count
        self.pair_buy_count = alloc.buy_count
        self.pair_sell_count = alloc.sell_count
        self.pair_amount = alloc.amount_per_order
        self.pair_step_size = step_size
        self.pair_min_amount = min_amount

        self.grid = GridEngine(
            grid_count=alloc.grid_count,
            spacing_percent=self.config.grid.spacing_percent,
            amount_per_order=alloc.amount_per_order,
            infinity_mode=self.config.grid.infinity_mode,
            trail_trigger_percent=self.config.grid.trail_trigger_percent,
        )
        self.order_mgr.grid = self.grid

    async def _recover_orders(self, saved_grid: dict) -> bool:
        """Try to match live open orders against saved grid levels.

        Returns True if recovery succeeded, False if a clean start is needed.
        """
        try:
            live_orders = await self.exchange.async_fetch_open_orders(self.pair)
        except Exception:
            return False

        if not live_orders:
            return False

        saved_levels = saved_grid.get("levels", [])
        if not saved_levels:
            return False

        saved_range = saved_grid.get("range")
        if saved_range:
            from bot.dynamic_range import RangeResult
            self.current_range = RangeResult(
                upper=saved_range["upper"], lower=saved_range["lower"],
                mid=saved_range.get("mid", (saved_range["upper"] + saved_range["lower"]) / 2),
                atr=saved_range.get("atr", 0), source=saved_range.get("source", "recovered"),
            )

        from bot.grid_engine import GridLevel
        PRICE_TOLERANCE = 0.001

        recovered_levels: list[GridLevel] = []
        matched_order_ids: set[str] = set()

        for sl in saved_levels:
            level = GridLevel(
                price=sl["price"], side=sl["side"], amount=sl["amount"],
                index=sl.get("index", len(recovered_levels)),
            )

            for lo in live_orders:
                if lo["id"] in matched_order_ids:
                    continue
                lo_side = lo.get("side", "").lower()
                lo_price = float(lo.get("price", 0))
                if lo_side == level.side and lo_price > 0:
                    if abs(lo_price - level.price) / level.price < PRICE_TOLERANCE:
                        level.order_id = lo["id"]
                        level.amount = float(lo.get("amount", level.amount))
                        matched_order_ids.add(lo["id"])
                        managed = OrderManager.create_managed(lo["id"], self.pair, level)
                        self.order_mgr.orders[lo["id"]] = managed
                        break

            recovered_levels.append(level)

        self.grid.state.levels = recovered_levels
        self.grid.state.invalidate()
        if self.current_range:
            self.grid.state.range_result = self.current_range

        matched = sum(1 for l in recovered_levels if l.order_id)
        unmatched_live = len(live_orders) - len(matched_order_ids)

        logger.info(
            "%s State Recovery: %d/%d Levels matched, %d live Orders unmatched",
            self.pair, matched, len(recovered_levels), unmatched_live,
        )
        return matched > 0

    async def initialize(self, allocator: CapitalAllocator, pair_count: int,
                         saved_state: dict | None = None):
        """Set up initial grid using CapitalAllocator.

        If saved_state is provided and recent, tries to recover existing orders
        instead of cancelling everything and starting fresh.
        """
        logger.info("Initializing pair bot for %s", self.pair)

        fetch_limit = self.config.pi.ohlcv_fetch_limit if self.config.is_pi else 200
        ohlcv = await self.exchange.async_fetch_ohlcv(self.pair, timeframe=self.config.atr.timeframe, limit=fetch_limit)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])

        ticker = await self.exchange.async_fetch_ticker(self.pair)
        self.current_price = ticker["last"]

        import numpy as _np
        ohlcv_arr = _np.array(ohlcv, dtype=_np.float64)
        self.regime.update(ohlcv_arr)
        rp = self.regime.get_grid_params()
        allocator.target_quote_ratio = rp.target_ratio

        balance = await asyncio.to_thread(self.exchange.fetch_account_balances)
        market = self.exchange._markets.get(self.pair, {})
        min_notional = market.get("limits", {}).get("cost", {}).get("min", 5.0)
        step_size = market.get("precision", {}).get("amount", 0.00001)

        import math as _m
        min_amount = _m.ceil((min_notional * 1.15) / self.current_price / step_size) * step_size

        alloc = allocator.allocate(
            self.pair, balance, self.current_price,
            pair_count, min_notional, step_size,
        )
        self.apply_allocation(alloc, step_size=step_size, min_amount=min_amount)

        # Try state recovery
        pair_saved = (saved_state or {}).get("grid_state", {}).get(self.pair)
        if pair_saved and alloc.grid_count >= 2:
            recovered = await self._recover_orders(pair_saved)
            if recovered:
                unplaced = self.grid.get_levels_to_place()
                if unplaced:
                    try:
                        await self.order_mgr.place_grid_orders(self.pair, self._entry_filter_dict())
                    except Exception as e:
                        logger.warning("Recovery re-place failed: %s", e)

                self.current_range = self.current_range or compute_dynamic_range(
                    df, self.current_price, self.config.atr,
                    self.config.grid.range_multiplier, self.ml, self.config.ml,
                )

                actual_levels = len(self.grid.state.levels)
                logger.info(
                    "%s recovered: price=%.2f, %d levels, %d with orders",
                    self.pair, self.current_price, actual_levels,
                    sum(1 for l in self.grid.state.levels if l.order_id),
                )
                return

        # Clean start: cancel old orders and build fresh grid
        try:
            old_orders = await self.exchange.async_fetch_open_orders(self.pair)
            if old_orders:
                for o in old_orders:
                    try:
                        await self.exchange.async_cancel_order(o["id"], self.pair)
                    except Exception:
                        pass
                logger.info("%s: %d alte offene Orders gecancelt", self.pair, len(old_orders))
        except Exception as e:
            logger.warning("Alte Orders pruefen fehlgeschlagen: %s", e)

        self.current_range = compute_dynamic_range(
            df, self.current_price, self.config.atr,
            self.config.grid.range_multiplier,
            self.ml, self.config.ml,
        )

        if alloc.grid_count < 2:
            logger.warning("%s: Nicht genug Kapital fuer Grid (Equity: %.2f)", self.pair, alloc.total_equity)
            self._grid_issue = "Nicht genug Kapital"
            return

        rp_init = self.regime.get_grid_params()
        self._apply_fees_to_grid()
        self.grid.calculate_grid(
            self.current_range, self.current_price, alloc.amount_per_order,
            buy_count=alloc.buy_count, sell_count=alloc.sell_count,
            buy_budget=alloc.buy_budget, sell_budget=alloc.sell_budget,
            step_size=step_size, min_amount=min_amount,
            min_distance_pct=rp_init.min_distance_pct,
        )
        self._apply_inventory_skew(rp_init.target_ratio)

        try:
            placed = await self.order_mgr.place_grid_orders(self.pair, self._entry_filter_dict())
            if len(placed) < len(self.grid.state.levels):
                self._grid_issue = self.order_mgr.last_fail_reason or "Balance zu niedrig"
        except Exception as e:
            logger.error("Failed to place initial orders for %s: %s", self.pair, e)
            self._grid_issue = str(e)

        actual_levels = len(self.grid.state.levels)
        logger.info(
            "%s initialized: price=%.2f, range=[%.2f, %.2f], %dB+%dS=%d levels, %.8f/order",
            self.pair, self.current_price, self.current_range.lower,
            self.current_range.upper, alloc.buy_count, alloc.sell_count,
            actual_levels, alloc.amount_per_order,
        )

        if self.cloud and self.cloud.connected:
            asyncio.create_task(self.cloud.log_event(
                "system", f"{self.pair} gestartet — {alloc.buy_count}B+{alloc.sell_count}S={actual_levels} Level",
                {"pair": self.pair, "price": self.current_price,
                 "range": [self.current_range.lower, self.current_range.upper],
                 "levels": actual_levels, "buy_count": alloc.buy_count,
                 "sell_count": alloc.sell_count, "amount": alloc.amount_per_order,
                 "equity": alloc.total_equity, "reserve": alloc.reserve_usdc,
                 "rebalance": alloc.rebalance_needed},
            ))

    def record_sell_drain(self, amount: float):
        """Track base currency sold for drain protection."""
        self._sell_drain.append((time.time(), amount))

    def _recent_sell_volume(self, window_sec: int = 600) -> float:
        """Total base sold in the last N seconds."""
        cutoff = time.time() - window_sec
        self._sell_drain = [(t, a) for t, a in self._sell_drain if t > cutoff]
        return sum(a for _, a in self._sell_drain)

    def is_sell_drain_active(self, current_base_balance: float) -> bool:
        """True if we've sold > 60% of our starting base in the last 10 minutes."""
        recent = self._recent_sell_volume(600)
        if current_base_balance <= 0 and recent > 0:
            return True
        starting = current_base_balance + recent
        if starting <= 0:
            return False
        return recent / starting > 0.60

    async def update_tick(self, price: float):
        """Process a price update."""
        self.current_price = price

        cb_ok, cb_reason = self.cb.can_trade(self.pair)
        if not cb_ok:
            return
        can_trade, reason = self.risk.can_trade()
        if not can_trade:
            return

        triggered = self.order_mgr.check_trailing_stops(price, pair=self.pair)
        if triggered:
            logger.info("%s: %d trailing stops triggered", self.pair, len(triggered))
            for level_id in triggered:
                for oid, order in list(self.order_mgr.orders.items()):
                    if order.status == "open" and order.grid_level.level_id == level_id:
                        try:
                            await self.exchange.async_cancel_order(oid, self.pair)
                            order.status = "cancelled"
                            logger.info("%s: Cancelled order %s (trailing stop)", self.pair, oid[:8])
                        except Exception as e:
                            logger.warning("Cancel on trailing stop failed: %s", e)

        if self.current_range:
            breakout = detect_range_breakout(price, self.current_range)
            if breakout:
                now = time.time()
                if now < self._trail_cooldown_until:
                    return

                logger.info("%s: Range breakout %s at %.2f", self.pair, breakout, price)
                await self.order_mgr.cancel_all(self.pair)

                rp = self.regime.get_grid_params()
                self._trail_cooldown_until = now + rp.trail_cooldown_sec

                new_range = shift_range(self.current_range, breakout)
                self.current_range = new_range
                alloc = self.last_allocation
                self._apply_fees_to_grid()
                self.grid.trail_grid(
                    breakout, price, new_range, self.pair_amount,
                    buy_count=self.pair_buy_count, sell_count=self.pair_sell_count,
                    buy_budget=alloc.buy_budget if alloc else None,
                    sell_budget=alloc.sell_budget if alloc else None,
                    step_size=self.pair_step_size, min_amount=self.pair_min_amount,
                    min_distance_pct=rp.min_distance_pct,
                )
                self._apply_inventory_skew(rp.target_ratio)

                if rp.trail_cooldown_sec > 0:
                    await asyncio.sleep(min(rp.trail_cooldown_sec, 15))

                placed = await self.order_mgr.place_grid_orders(self.pair, self._entry_filter_dict())

                if self.cloud and self.cloud.connected:
                    asyncio.create_task(self.cloud.log_event(
                        "grid", f"Range-Verschiebung {breakout} — Grid neu berechnet",
                        {"direction": breakout, "price": price,
                         "new_range": [new_range.lower, new_range.upper],
                         "levels": len(self.grid.state.levels),
                         "orders_placed": len(placed),
                         "min_distance_pct": rp.min_distance_pct,
                         "trail_cooldown": rp.trail_cooldown_sec},
                    ))

                await self.telegram.alert_range_shift(
                    self.pair, breakout, new_range.lower, new_range.upper,
                    source=new_range.source,
                )

    async def update_fill(self, order_data: dict):
        """Process a fill from WebSocket. Only place opposite when fully filled."""
        managed = self.order_mgr.process_ws_fill(order_data)
        if managed and managed.status == "filled":
            asyncio.create_task(self.order_mgr.async_enrich_ws_fill(managed))

            opposite = self.grid.get_opposite_level(managed.grid_level)
            if opposite:
                fill_qty = managed.filled_amount if managed.filled_amount > 0 else managed.amount
                if fill_qty < managed.grid_level.amount:
                    opposite.amount = fill_qty

                try:
                    if opposite.side == "buy":
                        order = await self.exchange.async_create_limit_buy(
                            self.pair, opposite.amount, opposite.price
                        )
                    else:
                        order = await self.exchange.async_create_limit_sell(
                            self.pair, opposite.amount, opposite.price
                        )
                    opposite.order_id = order["id"]
                except Exception as e:
                    logger.error("Failed to place opposite order: %s", e)

    async def run_ml_prediction(self):
        """Run LSTM prediction if enabled."""
        if self.ml is None or not self.config.ml.enabled:
            return

        try:
            fetch_limit = self.config.pi.ohlcv_fetch_limit if self.config.is_pi else 500
            ohlcv = self.exchange.fetch_ohlcv(self.pair, timeframe="1h", limit=fetch_limit)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])

            prediction = self.ml.predict(df)
            if prediction:
                self.last_prediction = prediction
                if prediction["confidence"] >= self.config.ml.confidence_threshold:
                    await self.telegram.alert_lstm_prediction(self.pair, prediction)
                    logger.info("%s LSTM: %s (conf=%.2f)", self.pair, prediction["label"], prediction["confidence"])
        except Exception as e:
            logger.error("ML prediction failed for %s: %s", self.pair, e)

    async def update_equity(self):
        """Update equity tracking."""
        try:
            balance = await asyncio.to_thread(self.exchange.fetch_account_balances)

            quote_bal = balance.get(self.quote, {})
            base = self.pair.split("/")[0]
            base_bal = balance.get(base, {})
            usdt = quote_bal.get("total", 0)
            logger.info(
                "%s Equity — %s: %.4f | %s: %.8f (≈%.2f %s)",
                self.pair, self.quote, usdt,
                base, base_bal.get("total", 0),
                base_bal.get("total", 0) * (self.current_price or 0), self.quote,
            )
            self.tracker.update_equity(self.pair, usdt)

            if self.cloud and self.cloud.connected:
                await self.cloud.sync_equity(self.pair, usdt)

            self.risk.update_equity(usdt)

            old_cb = self.cb.get_level(self.pair)
            new_cb = self.cb.update_equity(self.pair, usdt)

            if new_cb > old_cb:
                cb_name = {CBLevel.YELLOW: "YELLOW", CBLevel.ORANGE: "ORANGE", CBLevel.RED: "RED"}.get(new_cb, "?")
                cb_status = self.cb.get_pair_status(self.pair)
                if new_cb == CBLevel.RED:
                    await self.telegram.alert_drawdown_stop(self.pair, cb_status["drawdown_pct"], usdt)
                    await self.order_mgr.cancel_all(self.pair)
                if self.cloud and self.cloud.connected:
                    level_str = "critical" if new_cb == CBLevel.RED else "warn"
                    asyncio.create_task(self.cloud.log_event(
                        "circuit_breaker",
                        f"CB {self.pair} → {cb_name}: DD {cb_status['drawdown_pct']:.1f}% "
                        f"(Schwelle {cb_status[cb_name.lower() + '_threshold']:.1f}%, vol_adj {cb_status['vol_adj']:.1f}x)",
                        {"pair": self.pair, **cb_status},
                        level=level_str,
                    ))
        except Exception as e:
            logger.error("Equity update failed for %s: %s", self.pair, e)

    def _apply_fees_to_grid(self):
        """Ensure the grid engine respects fee-aware minimum spacing."""
        if self.fee_engine:
            self.fee_engine.apply_to_grid(self.grid, self.pair)
            fees = self.fee_engine.get_fees(self.pair)
            self.order_mgr.FEE_RATE = max(fees.maker, fees.taker)

    def _apply_inventory_skew(self, target_ratio: float | None = None):
        """Shift grid prices/sizes based on inventory imbalance."""
        if target_ratio is None:
            target_ratio = self.regime.get_grid_params().target_ratio

        inv = self.inventory.get_inventory(self.pair)
        price = self.current_price
        if price <= 0:
            return

        base_value = inv.base_inventory * price
        quote_alloc = self.last_allocation
        quote_value = quote_alloc.reserve_usdc if quote_alloc else 0.0
        if base_value + quote_value <= 0:
            return

        result = self.inv_skew.apply_to_grid(
            self.grid.state.levels, self.pair,
            base_value=base_value, quote_value=quote_value,
            target_ratio=target_ratio,
            min_amount=self.pair_min_amount,
        )

        if result.needs_rebalance:
            logger.warning(
                "SKEW ALERT %s: %.0f%% — Rebalance empfohlen (base %.1f%% vs target %.1f%%)",
                self.pair, result.skew_pct, result.current_ratio * 100, result.target_ratio * 100,
            )

    def _entry_filter_dict(self, base_balance: float | None = None) -> dict:
        ef = self.regime.get_entry_filter()
        allow_buys = ef.allow_buys
        allow_sells = ef.allow_sells
        if allow_sells and base_balance is not None and self.is_sell_drain_active(base_balance):
            allow_sells = False
            logger.warning("%s: Sell-Drain-Schutz aktiv — >60%% Base in 10 Min verkauft", self.pair)
        if not self.cb.can_buy(self.pair):
            allow_buys = False
        if not self.cb.can_sell(self.pair):
            allow_sells = False
        return {"allow_buys": allow_buys, "allow_sells": allow_sells}

    def get_status(self) -> dict:
        open_orders = self.order_mgr.get_open_orders(self.pair)
        orders_list = [
            {
                "side": o.side, "price": o.price, "amount": o.amount,
                "id": o.order_id, "status": o.status,
                "fill_pct": round(o.fill_pct, 1) if o.status == "partially_filled" else 100 if o.status == "filled" else 0,
                "filled_amount": o.filled_amount,
            }
            for o in sorted(open_orders, key=lambda x: x.price)
        ]
        partial_count = sum(1 for o in open_orders if o.status == "partially_filled")
        unplaced = len(self.grid.get_levels_to_place())
        alloc = self.last_allocation

        fee_metrics = {}
        if self.fee_engine:
            fee_metrics = self.fee_engine.get_metrics(
                self.pair, self.grid.spacing_percent, self.current_price,
            )

        inv_metrics = self.inventory.mark_to_market(self.pair, self.current_price) if self.current_price else {}

        return {
            "pair": self.pair,
            "price": self.current_price,
            "range": f"[{self.current_range.lower:.2f}, {self.current_range.upper:.2f}]" if self.current_range else "N/A",
            "range_source": self.current_range.source if self.current_range else "N/A",
            "grid_levels": len(self.grid.state.levels),
            "grid_configured": self.pair_grid_count,
            "grid_buy_count": self.pair_buy_count,
            "grid_sell_count": self.pair_sell_count,
            "active_orders": len(open_orders),
            "partially_filled_orders": partial_count,
            "filled_orders": len(self.order_mgr.get_filled_orders(self.pair)),
            "unplaced_orders": unplaced,
            "grid_issue": self._grid_issue if unplaced > 0 else "",
            "open_orders": orders_list,
            "last_prediction": self.last_prediction,
            "regime": self.regime.to_dict(),
            "allocation": {
                "equity": alloc.total_equity if alloc else 0,
                "reserve": alloc.reserve_usdc if alloc else 0,
                "amount_per_order": alloc.amount_per_order if alloc else 0,
                "rebalance_needed": alloc.rebalance_needed if alloc else False,
            },
            "fee_metrics": fee_metrics,
            "inventory": inv_metrics,
            "skew": self.inv_skew.get_metrics(self.pair),
            "circuit_breaker": self.cb.get_pair_status(self.pair),
            "mtf": self.mtf.get_metrics(),
            **self.tracker.get_summary(self.pair),
        }


class MultiPairBot:
    """Orchestrates multiple PairBot instances. Pi-aware resource management."""

    def __init__(self, config: BotConfig):
        self.config = config
        self.exchange = Exchange(config.exchange)
        self.risk = RiskManager(config.risk)
        self.tracker = PerformanceTracker(
            config.db_path,
            equity_history_limit=config.pi.equity_history_limit if config.is_pi else 10000,
            pi_mode=config.is_pi,
        )
        self.telegram = TelegramNotifier(config.telegram)
        self.cloud = CloudSync(config.cloud)
        self.allocator = CapitalAllocator()
        self.optimizer = SelfOptimizer(self.tracker)
        self.trailing_tp = TrailingTakeProfit()
        self.fee_engine = FeeEngine()
        self.inventory = InventoryTracker()
        self.inv_skew = InventorySkew()
        self.cb = CircuitBreaker(base_threshold=config.risk.max_drawdown_percent)
        self.correlation = CorrelationMonitor(config.pairs)
        self.sentiment = NewsSentiment(
            api_key=config.sentiment.api_key,
            provider=config.sentiment.provider,
            fetch_interval=config.sentiment.fetch_interval,
            cache_validity=config.sentiment.cache_validity,
        )
        self.rl = GridBandit()
        self.ws: WebSocketClient | None = None
        self.pair_bots: dict[str, PairBot] = {}
        self._running = False

    async def start(self):
        """Start trading all configured pairs."""
        logger.info("Starting multi-pair bot for %s", self.config.pairs)

        await self.cloud.start()
        await self.cloud.fetch_env()

        db_cfg = await self.cloud.fetch_config_update()
        if db_cfg:
            self._apply_config(db_cfg)
            if self.config.grid.amount_per_order < 0.0001:
                logger.warning("amount_per_order zu klein (%.8f), korrigiere auf 0.0001",
                               self.config.grid.amount_per_order)
                self.config.grid.amount_per_order = 0.0001
            logger.info("Dashboard-Config geladen und angewendet")

        import os
        api_key = os.environ.get("BINANCE_API_KEY", "").strip()
        api_secret = os.environ.get("BINANCE_SECRET", "").strip()
        tg_token = os.environ.get("TELEGRAM_TOKEN", "").strip()
        tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        xai_key = os.environ.get("XAI_API_KEY", "").strip()

        if api_key:
            self.config.exchange.api_key = api_key
        if api_secret:
            self.config.exchange.api_secret = api_secret
        if api_key and api_secret:
            self.config.exchange.sandbox = False
            masked_key = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "???"
            logger.info("Real API keys loaded — sandbox disabled (key=%s, len=%d)", masked_key, len(api_key))
        if tg_token:
            self.config.telegram.bot_token = tg_token
        if tg_chat:
            self.config.telegram.chat_id = tg_chat
        if xai_key and not self.config.sentiment.api_key:
            self.config.sentiment.api_key = xai_key
        self._sync_sentiment_runtime()

        logger.info("Config: pairs=%s, grid_count=%d, amount_per_order=%.8f",
                     self.config.pairs, self.config.grid.grid_count, self.config.grid.amount_per_order)

        self.exchange = Exchange(self.config.exchange)
        self.telegram = TelegramNotifier(self.config.telegram)

        self._register_cloud_commands()
        await self.cloud.sync_config(self.config.to_dict())

        # Validate exchange credentials before starting trading loops
        exchange_ok = await self._test_exchange()
        if not exchange_ok:
            await self._wait_for_valid_credentials()
            return

        await self._start_trading()

    async def _test_exchange(self) -> bool:
        """Test exchange connectivity with a lightweight API call (no threads needed)."""
        import hmac, hashlib, time as _time, json as _json
        from urllib.request import Request, urlopen
        from urllib.error import URLError

        api_key = self.config.exchange.api_key
        api_secret = self.config.exchange.api_secret
        if not api_key or not api_secret:
            logger.error("UNGUELTIGE API-KEYS: Kein Key/Secret konfiguriert")
            return False

        try:
            ts = int(_time.time() * 1000)
            query = f"timestamp={ts}"
            sig = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            url = f"https://api.binance.com/api/v3/account?{query}&signature={sig}"
            req = Request(url, headers={"X-MBX-APIKEY": api_key})
            resp = urlopen(req, timeout=10)
            data = _json.loads(resp.read().decode())
            logger.info("Binance API OK — Konto verifiziert")
            return True
        except URLError as e:
            if hasattr(e, "read"):
                try:
                    data = _json.loads(e.read().decode())
                    code = data.get("code", "")
                    msg = data.get("msg", "")
                    full = f"binance {{{code}: \"{msg}\"}}"
                    if code in (-2008, -2014, -2015):
                        logger.error("UNGUELTIGE API-KEYS: %s", full)
                        logger.error("Bitte gueltige Binance API-Keys im Dashboard unter Einstellungen > Secrets eintragen.")
                    else:
                        logger.error("Exchange-Verbindung fehlgeschlagen: %s", full)
                    return False
                except Exception:
                    pass
            logger.error("Exchange-Test fehlgeschlagen [%s]: %s", type(e).__name__, e)
            return False
        except Exception as e:
            logger.error("Exchange-Test fehlgeschlagen [%s]: %s", type(e).__name__, e or "(kein Detail)")
            return False

    async def _wait_for_valid_credentials(self):
        """Stay alive with cloud sync only, waiting for valid API keys."""
        logger.warning("Bot im Standby — warte auf gueltige API-Keys aus der Datenbank...")
        self.cloud.update_status("waiting_credentials", self.config.pairs, {})
        await self.cloud.send_heartbeat()
        self._running = True

        while self._running:
            await asyncio.sleep(30)
            await self.cloud.fetch_env(force=True)

            import os
            new_key = os.environ.get("BINANCE_API_KEY", "").strip()
            new_secret = os.environ.get("BINANCE_SECRET", "").strip()
            if new_key and new_secret:
                self.config.exchange.api_key = new_key
                self.config.exchange.api_secret = new_secret
                self.config.exchange.sandbox = False
                try:
                    await self.exchange.close()
                except Exception:
                    pass
                self.exchange = Exchange(self.config.exchange)
                if await self._test_exchange():
                    logger.info("Gueltige API-Keys erkannt — starte Trading...")
                    await self._start_trading()
                    return
                try:
                    await self.exchange.close()
                except Exception:
                    pass
            await self.cloud.send_heartbeat()

    async def _start_trading(self):
        """Initialize pairs and start trading loops."""
        try:
            await self.exchange.preload_markets(self.config.pairs)
        except Exception as e:
            logger.error("Markets laden fehlgeschlagen [%s]: %s", type(e).__name__, e)
            logger.warning("Bot geht zurueck in Standby...")
            await self._wait_for_valid_credentials()
            return

        try:
            await asyncio.to_thread(
                self.fee_engine.fetch_fees, self.exchange, self.config.pairs,
            )
        except Exception as e:
            logger.warning("Fee-Engine Init fehlgeschlagen: %s — nutze Defaults", e)

        for pair in self.config.pairs:
            ml = None
            if self.config.ml.enabled and not self.config.is_pi:
                from bot.config import PiConfig
                pi_cfg = self.config.pi if self.config.is_pi else PiConfig()
                ml = LSTMPredictor(self.config.ml, pair, pi_config=pi_cfg)
            bot = PairBot(pair, self.config, self.exchange, self.risk,
                          self.tracker, self.telegram, ml, self.cloud,
                          fee_engine=self.fee_engine, inventory=self.inventory,
                          inventory_skew=self.inv_skew,
                          circuit_breaker=self.cb)
            self.pair_bots[pair] = bot

        saved_state = await self.cloud.load_state() if self.cloud.connected else None
        if saved_state:
            self.trailing_tp.deserialize(saved_state.get("trailing_tps", []))
            inv_data = saved_state.get("inventory")
            if inv_data:
                self.inventory = InventoryTracker.deserialize(inv_data)
                for bot in self.pair_bots.values():
                    bot.inventory = self.inventory
                    bot.order_mgr.inventory = self.inventory

        pair_count = len(self.pair_bots)
        for pair, bot in self.pair_bots.items():
            try:
                await bot.initialize(self.allocator, pair_count, saved_state)
            except Exception as e:
                logger.error("Failed to initialize %s [%s]: %s", pair, type(e).__name__, e or "(kein Detail)")
                import traceback
                logger.debug("".join(traceback.format_exception(e)))

        await self.telegram.send_startup_message(self.config.pairs)

        self._running = True

        if self.config.websocket.enabled and not self.config.is_pi:
            await self._run_websocket()
        else:
            if self.config.is_pi and self.config.websocket.enabled:
                logger.info("WebSocket deaktiviert auf Pi (Speicherschutz) — nutze Polling")
            await self._run_polling()

    async def _run_websocket(self):
        """Run with WebSocket real-time updates."""
        self.ws = WebSocketClient(
            config=self.config.websocket,
            exchange_name=self.config.exchange.name,
            api_key=self.config.exchange.api_key,
            api_secret=self.config.exchange.api_secret,
            sandbox=self.config.exchange.sandbox,
        )

        self.ws.on_ticker(self._on_ticker)
        self.ws.on_order_update(self._on_order_update)

        await self.ws.start(self.config.pairs)

        tasks = [
            asyncio.create_task(self._equity_loop()),
            asyncio.create_task(self._daily_report_loop()),
            asyncio.create_task(self._range_refresh_loop()),
            asyncio.create_task(self._optimization_loop()),
            asyncio.create_task(self._fee_refresh_loop()),
            asyncio.create_task(self._correlation_loop()),
            asyncio.create_task(self._mtf_loop()),
        ]
        if not self.config.is_pi:
            tasks.append(asyncio.create_task(self._ml_loop()))
        if self.config.is_pi:
            tasks.append(asyncio.create_task(self._gc_loop()))
            tasks.append(asyncio.create_task(self._memory_watchdog()))
        tasks.append(asyncio.create_task(self._sentiment_loop()))

        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            for t in tasks:
                t.cancel()
            if self.ws:
                await self.ws.stop()

    async def _run_polling(self):
        """Fallback polling mode when WebSocket is not available."""
        tasks = [
            asyncio.create_task(self._poll_loop()),
            asyncio.create_task(self._equity_loop()),
            asyncio.create_task(self._daily_report_loop()),
            asyncio.create_task(self._range_refresh_loop()),
            asyncio.create_task(self._optimization_loop()),
            asyncio.create_task(self._fee_refresh_loop()),
            asyncio.create_task(self._correlation_loop()),
            asyncio.create_task(self._mtf_loop()),
        ]
        if not self.config.is_pi:
            tasks.append(asyncio.create_task(self._ml_loop()))
        if self.config.is_pi:
            tasks.append(asyncio.create_task(self._gc_loop()))
            tasks.append(asyncio.create_task(self._memory_watchdog()))
        tasks.append(asyncio.create_task(self._sentiment_loop()))
        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            for t in tasks:
                t.cancel()

    async def _range_refresh_loop(self):
        """Recompute range with fresh OHLCV + ML every 2 hours."""
        while self._running:
            await asyncio.sleep(7200)
            for pair, bot in self.pair_bots.items():
                try:
                    fetch_limit = self.config.pi.ohlcv_fetch_limit if self.config.is_pi else 200
                    ohlcv = await self.exchange.async_fetch_ohlcv(pair, timeframe=self.config.atr.timeframe, limit=fetch_limit)
                    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
                    ticker = await self.exchange.async_fetch_ticker(pair)
                    price = ticker["last"]

                    new_range = compute_dynamic_range(
                        df, price, self.config.atr,
                        self.config.grid.range_multiplier,
                        bot.ml, self.config.ml,
                    )

                    if new_range and bot.current_range:
                        drift = abs(new_range.lower - bot.current_range.lower) / bot.current_range.spread
                        if drift > 0.15:
                            logger.info("%s: Range refresh — drift %.1f%%, recalculating grid", pair, drift * 100)
                            await bot.order_mgr.cancel_all(pair)
                            bot.current_range = new_range

                            balance = await asyncio.to_thread(self.exchange.fetch_account_balances)
                            market = self.exchange._markets.get(pair, {})
                            min_notional = market.get("limits", {}).get("cost", {}).get("min", 5.0)
                            step_size = market.get("precision", {}).get("amount", 0.00001)
                            import math as _m
                            min_amount = _m.ceil((min_notional * 1.15) / price / step_size) * step_size
                            alloc = self.allocator.allocate(
                                pair, balance, price, len(self.pair_bots), min_notional, step_size,
                            )
                            bot.apply_allocation(alloc, step_size=step_size, min_amount=min_amount)
                            rp_r = bot.regime.get_grid_params()
                            bot._apply_fees_to_grid()
                            bot.grid.calculate_grid(
                                new_range, price, alloc.amount_per_order,
                                buy_count=alloc.buy_count, sell_count=alloc.sell_count,
                                buy_budget=alloc.buy_budget, sell_budget=alloc.sell_budget,
                                step_size=step_size, min_amount=min_amount,
                                min_distance_pct=rp_r.min_distance_pct,
                            )
                            bot._apply_inventory_skew(rp_r.target_ratio)
                            await bot.order_mgr.place_grid_orders(pair, bot._entry_filter_dict())
                        else:
                            logger.debug("%s: Range refresh — drift %.1f%%, keeping current grid", pair, drift * 100)
                except Exception as e:
                    logger.error("Range refresh failed for %s: %s", pair, e)

    def _trailing_tp_enabled(self, bot: PairBot) -> bool:
        """Trailing TP is active except in VOLATILE regime."""
        return bot.regime.regime != Regime.VOLATILE

    async def _place_counter_order(self, pair: str, bot: PairBot, opposite,
                                    fill_price: float = 0.0):
        """Place a smart limit counter-order adjusted to current price.

        The counter price is pushed away from the fill price to guarantee
        profit above fees, and the amount is increased by 10% when the price
        already moved 1%+ in the profitable direction (pyramid on winners).
        """
        current = bot.current_price or 0
        min_profit_spacing = self.fee_engine.min_profitable_spacing(pair)

        grid_price = opposite.price
        amount = opposite.amount

        if opposite.side == "sell":
            smart_price = current * (1 + min_profit_spacing)
            price = max(grid_price, smart_price)
            if fill_price and current > 0:
                move_pct = (current - fill_price) / fill_price
                if move_pct > 0.01:
                    amount = float(self.exchange.amount_to_precision(pair, amount * 1.10))
        else:
            smart_price = current * (1 - min_profit_spacing)
            price = min(grid_price, smart_price)
            if fill_price and current > 0:
                move_pct = (fill_price - current) / fill_price
                if move_pct > 0.01:
                    amount = float(self.exchange.amount_to_precision(pair, amount * 1.10))

        price = float(self.exchange.price_to_precision(pair, price))

        try:
            if opposite.side == "buy":
                order = await self.exchange.async_create_limit_buy(pair, amount, price)
            else:
                order = await self.exchange.async_create_limit_sell(pair, amount, price)
            opposite.order_id = order["id"]
            opposite.price = price
            opposite.amount = amount
            bot.order_mgr.orders[order["id"]] = OrderManager.create_managed(
                order["id"], pair, opposite)
            bot.risk.add_trailing_stop(opposite.level_id, opposite.side, price, pair=pair)
            if price != grid_price:
                logger.info("Smart-Gegenseite: %s %s %.8f @ %.2f (Grid: %.2f, Anpassung: +%.3f%%)",
                            opposite.side, pair, amount, price, grid_price,
                            abs(price - grid_price) / grid_price * 100)
            else:
                logger.info("Gegenseite platziert: %s %s %.8f @ %.2f", opposite.side, pair, amount, price)
        except Exception as e:
            logger.warning("Gegenseite fehlgeschlagen: %s %s @ %.2f: %s",
                           opposite.side, pair, price, e)
            if self.cloud.connected:
                asyncio.create_task(self.cloud.log_event(
                    "error", f"Gegenseite fehlgeschlagen: {opposite.side} @ {price:.2f}",
                    {"pair": pair, "side": opposite.side, "price": price,
                     "grid_price": grid_price, "reason": str(e)},
                    level="warn",
                ))

    async def _execute_trailing_tp(self, entry, pair: str, bot: PairBot):
        """Execute a market order triggered by trailing TP."""
        counter_side = "sell" if entry.side == "buy" else "buy"
        try:
            result = await self.exchange.async_create_market_order(pair, counter_side, entry.amount)
            exec_price = result.get("price", entry.trigger_price)
            if entry.side == "buy":
                pnl = (exec_price - entry.entry_price) * entry.amount
                hold_sec = time.time() - entry.created_at
            else:
                pnl = (entry.entry_price - exec_price) * entry.amount
                hold_sec = time.time() - entry.created_at

            profit_pct = pnl / (entry.entry_price * entry.amount) * 100 if entry.entry_price else 0

            logger.info(
                "Trailing-TP %s: %s %s %.8f @ %.2f (Entry %.2f, %s %.2f, PnL %.4f / %.2f%%)",
                entry.trigger_reason, counter_side, pair, entry.amount, exec_price,
                entry.entry_price,
                "High" if entry.side == "buy" else "Low",
                entry.highest if entry.side == "buy" else entry.lowest,
                pnl, profit_pct,
            )

            trade = TradeRecord(
                timestamp=time.time(), pair=pair, side=counter_side,
                price=entry.entry_price, amount=entry.amount,
                fee=exec_price * entry.amount * OrderManager.FEE_RATE,
                pnl=pnl, grid_level=entry.grid_level_price,
                order_id=result.get("id", "trailing_tp"),
                fill_price=exec_price,
                slippage=0.0,
                is_maker=False,
            )
            bot.tracker.record_trade(trade)
            asyncio.create_task(bot.telegram.alert_fill(pair, counter_side, exec_price, entry.amount, pnl))

            if self.cloud.connected:
                asyncio.create_task(self.cloud.sync_trade(trade))
                asyncio.create_task(self.cloud.log_event(
                    "trailing_tp",
                    f"Trailing-TP: {counter_side.title()} {pair} @ {exec_price:.2f} "
                    f"(Entry {entry.entry_price:.2f}, {'High' if entry.side == 'buy' else 'Low'} "
                    f"{entry.highest if entry.side == 'buy' else entry.lowest:.2f})",
                    {"pair": pair, "side": counter_side, "entry_price": entry.entry_price,
                     "exit_price": exec_price, "highest": entry.highest, "lowest": entry.lowest,
                     "profit_pct": round(profit_pct, 3), "hold_time_sec": round(hold_sec),
                     "reason": entry.trigger_reason, "pnl": round(pnl, 6)},
                ))
        except Exception as e:
            logger.error("Trailing-TP Market-Order fehlgeschlagen: %s %s: %s", counter_side, pair, e)

    async def _poll_loop(self):
        while self._running:
            for pair, bot in self.pair_bots.items():
                try:
                    ticker = await self.exchange.async_fetch_ticker(pair)
                    price = ticker["last"]
                    await bot.update_tick(price)

                    use_trailing = self._trailing_tp_enabled(bot)

                    filled = await bot.order_mgr.check_fills(pair)
                    for managed in filled:
                        fill_qty = managed.filled_amount if managed.filled_amount > 0 else managed.amount
                        opposite = bot.grid.get_opposite_level(managed.grid_level)
                        if opposite:
                            if fill_qty < managed.grid_level.amount:
                                opposite.amount = fill_qty
                        if opposite and use_trailing:
                            self.trailing_tp.add_entry(
                                pair, managed.side, managed.fill_price,
                                fill_qty, opposite.price,
                            )
                        elif opposite:
                            await self._place_counter_order(pair, bot, opposite, managed.fill_price)

                    # Check trailing take-profits
                    triggered, fallbacks = self.trailing_tp.check(pair, price)
                    for entry in triggered:
                        await self._execute_trailing_tp(entry, pair, bot)
                    for entry in fallbacks:
                        opposite_side = "sell" if entry.side == "buy" else "buy"
                        logger.info(
                            "Trailing-TP Fallback: %s %s @ %.2f (Timeout nach %ds)",
                            opposite_side, pair, entry.grid_level_price,
                            int(time.time() - entry.created_at),
                        )
                        try:
                            if opposite_side == "buy":
                                order = await self.exchange.async_create_limit_buy(
                                    pair, entry.amount, entry.grid_level_price)
                            else:
                                order = await self.exchange.async_create_limit_sell(
                                    pair, entry.amount, entry.grid_level_price)
                            logger.info("Fallback-Order platziert: %s %s @ %.2f", opposite_side, pair, entry.grid_level_price)
                        except Exception as e:
                            logger.warning("Fallback-Order fehlgeschlagen: %s", e)

                    if triggered or fallbacks:
                        self.trailing_tp.cleanup()

                    unplaced = bot.grid.get_levels_to_place()
                    if unplaced:
                        recovered = await bot.order_mgr.place_grid_orders(pair, bot._entry_filter_dict())
                        if recovered:
                            bot._grid_issue = ""
                            if self.cloud.connected:
                                asyncio.create_task(self.cloud.log_event(
                                    "grid", f"{len(recovered)} Order(s) nachplatziert",
                                    {"pair": pair, "count": len(recovered),
                                     "orders": [{"side": o.side, "price": o.price} for o in recovered]},
                                ))
                        else:
                            reason = bot.order_mgr.last_fail_reason or "Unbekannt"
                            bot._grid_issue = reason

                except Exception as e:
                    logger.error("Poll error for %s: %s", pair, e)
            await asyncio.sleep(5)

    async def _on_ticker(self, symbol: str, ticker: dict):
        bot = self.pair_bots.get(symbol)
        if bot:
            await bot.update_tick(ticker.get("last", 0))

    async def _on_order_update(self, symbol: str, order: dict):
        bot = self.pair_bots.get(symbol)
        if bot:
            ccxt_status = (order.get("status") or "").upper()
            order_status = "FILLED"
            if ccxt_status == "OPEN" or ccxt_status == "":
                order_status = "PARTIALLY_FILLED"
            elif ccxt_status in ("CLOSED", "FILLED"):
                order_status = "FILLED"
            elif ccxt_status == "CANCELED":
                return

            info = order.get("info", {})
            enriched = {
                "id": order.get("id", ""),
                "price": order.get("price", 0),
                "cum_quote_qty": order.get("cost", 0),
                "cum_qty": order.get("filled", 0),
                "last_qty": float(info.get("l", 0)),
                "last_price": float(info.get("L", 0)),
                "order_status": order_status,
                "is_maker": info.get("m", True),
            }
            fee_info = order.get("fee") or {}
            if fee_info.get("cost"):
                enriched["commission"] = fee_info["cost"]
                enriched["commission_asset"] = fee_info.get("currency", "")
            await bot.update_fill(enriched)

    async def _ml_loop(self):
        interval = self.config.ml.prediction_interval_minutes * 60
        while self._running:
            for pair, bot in self.pair_bots.items():
                await bot.run_ml_prediction()
            await asyncio.sleep(interval)

    async def _update_regimes(self):
        """Update market regime for all pairs."""
        import numpy as _np
        fetch_limit = self.config.pi.ohlcv_fetch_limit if self.config.is_pi else 200
        for pair, bot in self.pair_bots.items():
            try:
                ohlcv = await self.exchange.async_fetch_ohlcv(
                    pair, timeframe=self.config.atr.timeframe, limit=fetch_limit,
                )
                ohlcv_arr = _np.array(ohlcv, dtype=_np.float64)
                if len(ohlcv_arr) >= 14:
                    from bot import indicators as _ind
                    atr_val = _ind.atr(ohlcv_arr[:, 2], ohlcv_arr[:, 3], ohlcv_arr[:, 4], 14)
                    self.cb.update_atr(pair, atr_val)
                old_regime = bot.regime.regime
                new_regime = bot.regime.update(ohlcv_arr)
                if new_regime != old_regime and self.cloud.connected:
                    rp = bot.regime.get_grid_params()
                    asyncio.create_task(self.cloud.log_event(
                        "regime",
                        f"{pair} Regime: {old_regime.value} → {new_regime.value}",
                        {"pair": pair, "old": old_regime.value, "new": new_regime.value,
                         "target_ratio": rp.target_ratio, "spacing_mult": rp.spacing_mult,
                         "size_mult": rp.size_mult, **bot.regime.to_dict()},
                    ))
            except Exception as e:
                logger.debug("Regime update failed for %s: %s", pair, e)

    async def _equity_loop(self):
        cycle = 0
        while self._running:
            await asyncio.gather(
                *(bot.update_equity() for bot in self.pair_bots.values()),
                return_exceptions=True,
            )
            self.tracker.flush()

            await self._update_regimes()

            cycle += 1
            if cycle % 5 == 0:
                await self._auto_adjust_grid()
                if self.cloud.connected:
                    await self._log_analytics_snapshot()
                    await self._monitoring_checks()

            if self.cloud.connected:
                metrics = {pair: bot.get_status() for pair, bot in self.pair_bots.items()}
                corr_metrics = self.correlation.get_metrics()
                if corr_metrics:
                    metrics["__correlation__"] = corr_metrics
                metrics["__circuit_breaker__"] = self.cb.get_metrics()
                wallet = await self._fetch_wallet()
                self.cloud.update_status("running", self.config.pairs, metrics, wallet)

                await self._persist_state()

            await asyncio.sleep(60)

    async def _persist_state(self):
        """Save current grid state + trailing TPs for crash recovery."""
        try:
            grid_state: dict = {}
            last_prices: dict = {}
            for pair, bot in self.pair_bots.items():
                last_prices[pair] = bot.current_price
                levels = []
                for l in bot.grid.state.levels:
                    levels.append({
                        "price": l.price, "side": l.side, "amount": l.amount,
                        "index": l.index, "order_id": l.order_id, "filled": l.filled,
                    })
                rng = bot.current_range
                grid_state[pair] = {
                    "levels": levels,
                    "range": {
                        "upper": rng.upper, "lower": rng.lower,
                        "mid": rng.mid, "atr": rng.atr, "source": rng.source,
                    } if rng else None,
                    "regime": bot.regime.regime.value,
                }

            await self.cloud.save_state({
                "grid_state": grid_state,
                "trailing_tps": self.trailing_tp.serialize(),
                "last_prices": last_prices,
                "inventory": self.inventory.serialize(),
            })
        except Exception as e:
            logger.debug("State persist failed: %s", e)

    async def _monitoring_checks(self):
        """Run health checks and emit alerts for critical conditions."""
        if not self.cloud.connected:
            return
        now = time.time()
        if not hasattr(self, "_mon_state"):
            self._mon_state: dict = {"last_alert": {}, "loss_streak": {}}

        cooldown = 600  # don't repeat same alert within 10 min

        for pair, bot in self.pair_bots.items():
            stats = self.tracker._get_pair_stats(pair)
            summary = self.tracker.get_summary(pair)

            # No trades for 2 hours
            alert_key = f"no_trade_{pair}"
            last_trade_ts = stats.pnl_history[-1][0] if stats.pnl_history else self._mon_state.get("start", now)
            if now - last_trade_ts > 7200 and now - self._mon_state["last_alert"].get(alert_key, 0) > cooldown:
                asyncio.create_task(self.cloud.log_event(
                    "monitoring", f"Kein Trade seit 2h — Grid pruefen ({pair})",
                    {"pair": pair, "last_trade_age_sec": int(now - last_trade_ts)},
                    level="warn",
                ))
                self._mon_state["last_alert"][alert_key] = now

            # Circuit breaker monitoring
            cb_status = self.cb.get_pair_status(pair)
            cb_level = cb_status["level"]
            if cb_level != "GREEN":
                alert_key = f"cb_{cb_level}_{pair}"
                if now - self._mon_state["last_alert"].get(alert_key, 0) > cooldown:
                    lvl = "critical" if cb_level == "RED" else "warn"
                    asyncio.create_task(self.cloud.log_event(
                        "monitoring",
                        f"CB {cb_level} aktiv — DD {cb_status['drawdown_pct']:.1f}% ({pair})",
                        {"pair": pair, **cb_status}, level=lvl,
                    ))
                    self._mon_state["last_alert"][alert_key] = now

            # USDC ratio check
            alloc = bot.last_allocation
            if alloc and alloc.total_equity > 0:
                usdc_pct = (alloc.quote_for_trading + alloc.reserve_usdc) / alloc.total_equity * 100
                if usdc_pct < 20:
                    alert_key = f"low_usdc_{pair}"
                    if now - self._mon_state["last_alert"].get(alert_key, 0) > cooldown:
                        asyncio.create_task(self.cloud.log_event(
                            "monitoring", f"Nur {usdc_pct:.0f}% USDC — Rebalance noetig ({pair})",
                            {"pair": pair, "usdc_pct": round(usdc_pct, 1)}, level="warn",
                        ))
                        self._mon_state["last_alert"][alert_key] = now

            # Consecutive loss streak
            recent_trades = self.tracker.get_trade_history(pair, limit=10)
            losses = 0
            for t in recent_trades:
                if t.get("pnl", 0) < 0:
                    losses += 1
                else:
                    break
            if losses >= 5:
                alert_key = f"loss_streak_{pair}"
                if now - self._mon_state["last_alert"].get(alert_key, 0) > cooldown:
                    asyncio.create_task(self.cloud.log_event(
                        "monitoring", f"{losses} Verluste in Folge — Spacing geweitet ({pair})",
                        {"pair": pair, "consecutive_losses": losses}, level="warn",
                    ))
                    self._mon_state["last_alert"][alert_key] = now

        self._mon_state["start"] = self._mon_state.get("start", now)

        # Sentiment stale check
        if self.config.sentiment.enabled and self.sentiment._last_signal:
            sig_age = now - self.sentiment._last_signal.timestamp
            alert_key = "sentiment_stale"
            if sig_age > 3600 and now - self._mon_state["last_alert"].get(alert_key, 0) > cooldown:
                asyncio.create_task(self.cloud.log_event(
                    "monitoring",
                    f"Sentiment-Signal veraltet ({int(sig_age / 60)} Min) — Headlines nicht erreichbar?",
                    {"age_sec": int(sig_age), "source": self.sentiment._last_signal.source},
                    level="warn",
                ))
                self._mon_state["last_alert"][alert_key] = now

        # RL divergence check
        if self.config.rl.enabled and self.rl._episode_count >= 10:
            last_10 = self.rl._history[-10:] if self.rl._history else []
            if last_10:
                avg_r = sum(h["reward"] for h in last_10) / len(last_10)
                alert_key = "rl_diverge"
                if avg_r < -0.5 and now - self._mon_state["last_alert"].get(alert_key, 0) > cooldown:
                    self.rl._exploration = max(self.rl._exploration, 0.30)
                    asyncio.create_task(self.cloud.log_event(
                        "monitoring",
                        f"RL divergiert (avg reward {avg_r:.2f}) — Exploration auf 30% erhoeht",
                        {"avg_reward_10": round(avg_r, 3), "episodes": self.rl._episode_count},
                        level="warn",
                    ))
                    self._mon_state["last_alert"][alert_key] = now
                    logger.warning("RL divergiert (avg_reward=%.2f) — exploration auf 30%%", avg_r)

        # RL milestone
        if self.config.rl.enabled:
            ep = self.rl._episode_count
            for milestone in (100, 500, 1000):
                alert_key = f"rl_milestone_{milestone}"
                if ep >= milestone and alert_key not in self._mon_state.get("milestones_sent", set()):
                    if "milestones_sent" not in self._mon_state:
                        self._mon_state["milestones_sent"] = set()
                    self._mon_state["milestones_sent"].add(alert_key)
                    asyncio.create_task(self.cloud.log_event(
                        "monitoring",
                        f"RL hat {milestone} Episoden gelernt — Exploration bei {self.rl._exploration * 100:.1f}%",
                        {"episodes": ep, "exploration": round(self.rl._exploration, 4)},
                        level="info",
                    ))

    async def _optimization_loop(self):
        """Self-optimize based on recent performance.

        Heuristic SelfOptimizer remains the primary layer.
        When RL is enabled, GridBandit proposes a second set of deltas
        that are merged with the heuristic — agreement amplifies,
        contradiction defers to the heuristic until warmup is complete.
        """
        await asyncio.sleep(300)
        if self.config.rl.enabled:
            logger.info("RL-Optimizer aktiv — erste Optimierung in 5 Min")
        while self._running:
            try:
                pairs = list(self.pair_bots.keys())
                if not pairs:
                    continue

                for pair, bot in self.pair_bots.items():
                    window = self.optimizer.evaluate(pair)
                    if window.trade_count < 3:
                        logger.info("Optimizer: %s — zu wenig Trades (%d), ueberspringe",
                                    pair, window.trade_count)
                        continue

                    rp = bot.regime.get_grid_params()
                    current_params = {
                        "spacing_mult": rp.spacing_mult,
                        "size_mult": rp.size_mult,
                        "range_multiplier": self.config.grid.range_multiplier,
                        "min_distance_pct": rp.min_distance_pct,
                    }

                    heuristic_adj = self.optimizer.suggest_adjustments(current_params, window)

                    merged_adj = dict(heuristic_adj)

                    rl_action: dict | None = None
                    if self.config.rl.enabled:
                        reward = compute_reward(
                            sharpe=window.sharpe_ratio,
                            win_rate=window.win_rate,
                            pnl_24h=window.total_pnl,
                            drawdown_pct=window.max_drawdown_pct,
                        )
                        self.rl.record_reward(reward)

                        regime_dict = bot.regime.to_dict()
                        perf_summary = {
                            "win_rate": window.win_rate,
                            "sharpe": window.sharpe_ratio,
                            "max_drawdown_pct": window.max_drawdown_pct,
                            "fill_rate": window.grid_fill_rate,
                        }
                        sent_score = regime_dict.get("sentiment_score", 0.0)
                        state = self.rl.get_state(regime_dict, perf_summary, sent_score)
                        rl_action = self.rl.choose_action(state)

                        merged_adj = self._merge_rl_heuristic(
                            current_params, heuristic_adj, rl_action,
                        )

                        logger.info(
                            "Optimizer: pair=%s, heuristic=%s, rl=%s, merged=%s, "
                            "reward=%.3f, episode=%d, exploration=%.1f%%",
                            pair, heuristic_adj, rl_action, merged_adj,
                            reward, self.rl._episode_count,
                            self.rl._exploration * 100,
                        )

                        if self.cloud.connected:
                            asyncio.create_task(self.cloud.log_event(
                                "rl_optimization",
                                f"RL Episode #{self.rl._episode_count}: "
                                f"reward={reward:.3f}, action={rl_action['action_idx']}",
                                {
                                    "state": state.tolist(),
                                    "action": rl_action,
                                    "reward": round(reward, 4),
                                    "was_exploration": rl_action["was_exploration"],
                                    "episode": self.rl._episode_count,
                                    "heuristic_adj": heuristic_adj,
                                    "merged_adj": merged_adj,
                                    "exploration_rate": round(self.rl._exploration, 4),
                                },
                            ))

                    if merged_adj:
                        if "range_multiplier" in merged_adj:
                            self.config.grid.range_multiplier = merged_adj["range_multiplier"]
                        if not rl_action:
                            logger.info(
                                "Optimizer: %s — WR=%.0f%%, Sharpe=%.2f, DD=%.1f%%, Adj: %s",
                                pair, window.win_rate * 100, window.sharpe_ratio,
                                window.max_drawdown_pct, merged_adj,
                            )
                        if self.cloud.connected:
                            asyncio.create_task(self.cloud.log_event(
                                "optimization",
                                f"Self-Tuning {pair}: {', '.join(f'{k}={v}' for k, v in merged_adj.items())}",
                                {"pair": pair, "adjustments": merged_adj,
                                 "win_rate": window.win_rate,
                                 "sharpe": window.sharpe_ratio,
                                 "drawdown": window.max_drawdown_pct,
                                 "trades_24h": window.trade_count,
                                 "total_pnl_24h": window.total_pnl},
                            ))
                    else:
                        logger.info("Optimizer: %s — keine Anpassung noetig (WR=%.0f%%, Sharpe=%.2f)",
                                    pair, window.win_rate * 100, window.sharpe_ratio)

                if len(pairs) >= 2:
                    scores = self.optimizer.score_pairs(pairs)
                    for s in scores:
                        logger.info(
                            "Pair-Score: %s — Sharpe=%.2f, PnL24h=%.4f, Gewicht=%.1f%%",
                            s.pair, s.sharpe, s.pnl_24h, s.capital_weight * 100,
                        )
                    if self.cloud.connected:
                        asyncio.create_task(self.cloud.log_event(
                            "optimization",
                            "Pair-Scoring: " + ", ".join(
                                f"{s.pair}={s.capital_weight:.0%}" for s in scores),
                            {"scores": [{"pair": s.pair, "sharpe": s.sharpe,
                                         "pnl_24h": s.pnl_24h, "weight": s.capital_weight}
                                        for s in scores]},
                        ))

            except Exception as e:
                logger.warning("Optimization loop error: %s", e)
            await asyncio.sleep(self.config.rl.eval_interval_hours * 3600)

    def _merge_rl_heuristic(
        self, current: dict, heuristic: dict, rl_action: dict,
    ) -> dict:
        """Combine heuristic adjustments with RL deltas.

        Before warmup: heuristic wins on conflict, RL only reinforces.
        After warmup:  RL gets 70% weight, heuristic 30%.
        """
        from bot.rl_optimizer import GridBandit, SAFETY_BOUNDS

        past_warmup = self.rl._episode_count >= self.config.rl.warmup_episodes
        rl_w = 0.70 if past_warmup else 0.0
        h_w = 1.0 - rl_w

        rl_params = GridBandit.apply_deltas(current, rl_action)

        merged: dict[str, float] = {}
        for key in ("spacing_mult", "size_mult", "range_multiplier", "min_distance_pct"):
            h_val = heuristic.get(key)
            r_val = rl_params.get(key, current.get(key))
            c_val = current.get(key, r_val)

            if h_val is None and r_val == c_val:
                continue

            h_target = h_val if h_val is not None else c_val
            r_target = r_val if r_val is not None else c_val

            h_dir = h_target - c_val
            r_dir = r_target - c_val

            if not past_warmup:
                if h_val is not None:
                    # RL reinforces heuristic direction only
                    if h_dir * r_dir > 0:
                        final = c_val + h_dir + r_dir * 0.3
                    else:
                        final = h_target
                elif abs(r_dir) > 1e-9:
                    final = c_val + r_dir * 0.3
                else:
                    continue
            else:
                final = c_val + h_w * h_dir + rl_w * r_dir

            lo, hi = SAFETY_BOUNDS.get(key, (0.0, 10.0))
            final = max(lo, min(hi, final))

            if abs(final - c_val) > 1e-6:
                merged[key] = round(final, 4)

        return merged

    async def _log_analytics_snapshot(self):
        """Log comprehensive analytics every ~5 minutes for dashboard analysis."""
        try:
            balance = await asyncio.to_thread(self.exchange.fetch_account_balances)
            total_usdc = 0.0
            balances: dict[str, float] = {}
            seen_assets: set[str] = set()

            for pair, bot in self.pair_bots.items():
                base = pair.split("/")[0]
                quote = pair.split("/")[1]
                price = bot.current_price or 0

                if quote not in seen_assets:
                    q = balance.get(quote, {})
                    balances[quote] = q.get("total", 0)
                    total_usdc += balances[quote]
                    seen_assets.add(quote)

                if base not in seen_assets:
                    b = balance.get(base, {})
                    b_total = b.get("total", 0)
                    balances[base] = b_total
                    total_usdc += b_total * price
                    seen_assets.add(base)

            for pair, bot in self.pair_bots.items():
                summary = self.tracker.get_summary(pair)
                open_orders = bot.order_mgr.get_open_orders(pair)
                total_levels = len(bot.grid.state.levels)
                active = len(open_orders)
                buys = sum(1 for o in open_orders if o.side == "buy")
                sells = active - buys

                fee_info = self.fee_engine.get_metrics(
                    pair, bot.grid.spacing_percent, bot.current_price,
                ) if bot.current_price else {}

                inv_snap = self.inventory.mark_to_market(pair, bot.current_price) if bot.current_price else {}
                skew_snap = self.inv_skew.get_metrics(pair)

                asyncio.create_task(self.cloud.log_event(
                    "snapshot",
                    f"{pair} — {active}/{total_levels} Grid, PnL {summary['realized_pnl']:.4f}",
                    {
                        "pair": pair,
                        "price": bot.current_price,
                        "grid_active": active,
                        "grid_total": total_levels,
                        "grid_buys": buys,
                        "grid_sells": sells,
                        "grid_configured": bot.pair_grid_count,
                        "order_amount": bot.pair_amount,
                        "range": [bot.current_range.lower, bot.current_range.upper] if bot.current_range else None,
                        "realized_pnl": summary["realized_pnl"],
                        "unrealized_pnl": summary["unrealized_pnl"],
                        "total_pnl": summary["total_pnl"],
                        "trade_count": summary["trade_count"],
                        "buy_count": summary.get("buy_count", 0),
                        "sell_count": summary.get("sell_count", 0),
                        "fees": summary["fees_paid"],
                        "max_drawdown": summary["max_drawdown_pct"],
                        "sharpe": summary["sharpe_ratio"],
                        "equity": summary["current_equity"],
                        "portfolio_total": total_usdc,
                        "balances": balances,
                        "issue": bot._grid_issue or None,
                        "avg_slippage_bps": summary.get("avg_slippage_bps", 0),
                        "max_slippage_bps": summary.get("max_slippage_bps", 0),
                        "slippage_cost": summary.get("slippage_cost", 0),
                        "maker_fill_pct": summary.get("maker_fill_pct", 100),
                        **fee_info,
                        **inv_snap,
                        "skew": skew_snap,
                        "correlation": self.correlation.get_metrics(),
                        "mtf": bot.mtf.get_metrics(),
                    },
                ))
        except Exception as e:
            logger.debug("Analytics snapshot failed: %s", e)

    async def _fetch_wallet(self) -> dict:
        """Fetch full wallet balances for dashboard display."""
        try:
            balance = await asyncio.to_thread(self.exchange.fetch_account_balances)
            wallet: dict = {}
            total_usdc = 0.0

            for pair, bot in self.pair_bots.items():
                base = pair.split("/")[0]
                quote = pair.split("/")[1]
                price = bot.current_price or 0

                if quote not in wallet:
                    q = balance.get(quote, {})
                    wallet[quote] = {
                        "free": q.get("free", 0), "locked": q.get("used", 0),
                        "total": q.get("total", 0), "usdc_value": q.get("total", 0),
                    }
                    total_usdc += q.get("total", 0)

                b = balance.get(base, {})
                b_total = b.get("total", 0)
                wallet[base] = {
                    "free": b.get("free", 0), "locked": b.get("used", 0),
                    "total": b_total, "price": price,
                    "usdc_value": b_total * price,
                }
                total_usdc += b_total * price

            wallet["_total_usdc"] = total_usdc
            return wallet
        except Exception as e:
            logger.debug("Wallet fetch failed: %s", e)
            return {}

    async def _auto_adjust_grid(self):
        """Re-evaluate capital allocation and adjust grids every ~5 minutes."""
        pair_count = len(self.pair_bots)
        for pair, bot in list(self.pair_bots.items()):
            try:
                price = bot.current_price or 0
                if price <= 0:
                    continue

                rp = bot.regime.get_grid_params()
                self.allocator.target_quote_ratio = rp.target_ratio

                balance = await asyncio.to_thread(self.exchange.fetch_account_balances)
                market = self.exchange._markets.get(pair, {})
                min_notional = market.get("limits", {}).get("cost", {}).get("min", 5.0)
                step_size = market.get("precision", {}).get("amount", 0.00001)

                alloc = self.allocator.allocate(
                    pair, balance, price, pair_count, min_notional, step_size,
                )

                if alloc.rebalance_needed and alloc.rebalance_action:
                    await self._execute_rebalance(alloc.rebalance_action)
                    balance = await asyncio.to_thread(self.exchange.fetch_account_balances)
                    alloc = self.allocator.allocate(
                        pair, balance, price, pair_count, min_notional, step_size,
                    )

                old_buy = bot.pair_buy_count
                old_sell = bot.pair_sell_count
                old_amount = bot.pair_amount
                new_total = alloc.buy_count + alloc.sell_count
                old_total = old_buy + old_sell

                changed = (
                    abs(new_total - old_total) >= 2
                    or abs(alloc.buy_count - old_buy) >= 1
                    or abs(alloc.sell_count - old_sell) >= 1
                    or (old_amount > 0 and abs(alloc.amount_per_order - old_amount) / old_amount > 0.15)
                )

                if changed and new_total >= 2:
                    import math as _m
                    min_amount = _m.ceil((min_notional * 1.15) / price / step_size) * step_size

                    if rp.max_levels and new_total > rp.max_levels:
                        ratio = alloc.buy_count / max(new_total, 1)
                        alloc.buy_count = max(1, round(rp.max_levels * ratio))
                        alloc.sell_count = rp.max_levels - alloc.buy_count
                        alloc.grid_count = rp.max_levels

                    alloc.buy_budget *= rp.size_mult
                    alloc.sell_budget *= rp.size_mult

                    corr_factor = self.correlation.effective_position_limit(pair, 1.0)
                    if corr_factor < 0.99:
                        alloc.buy_budget *= corr_factor
                        alloc.sell_budget *= corr_factor
                        alloc.amount_per_order *= corr_factor
                        logger.info("%s: Korrelations-Reduktion %.0f%% auf Positionsgroesse",
                                    pair, (1 - corr_factor) * 100)

                    cb_size = self.cb.size_factor(pair)
                    if cb_size < 1.0:
                        alloc.buy_budget *= cb_size
                        alloc.sell_budget *= cb_size
                        alloc.amount_per_order *= cb_size
                        logger.info("%s: CB Size-Reduktion → %.0f%%", pair, cb_size * 100)

                    mtf_sig = bot.mtf.signal
                    if mtf_sig.size_mult != 1.0:
                        alloc.buy_budget *= mtf_sig.size_mult
                        alloc.sell_budget *= mtf_sig.size_mult
                        alloc.amount_per_order *= mtf_sig.size_mult
                    if mtf_sig.suggested_bias == "buy_heavy":
                        alloc.buy_count = max(alloc.buy_count, alloc.sell_count)
                    elif mtf_sig.suggested_bias == "sell_heavy":
                        alloc.sell_count = max(alloc.buy_count, alloc.sell_count)

                    bot.apply_allocation(alloc, step_size=step_size, min_amount=min_amount)
                    bot.grid.spacing_percent *= rp.spacing_mult * self.cb.spacing_mult(pair)

                    await bot.order_mgr.cancel_all(pair)
                    rp_a = bot.regime.get_grid_params()
                    bot._apply_fees_to_grid()
                    bot.grid.calculate_grid(
                        bot.current_range, price, alloc.amount_per_order,
                        buy_count=alloc.buy_count, sell_count=alloc.sell_count,
                        buy_budget=alloc.buy_budget, sell_budget=alloc.sell_budget,
                        step_size=step_size, min_amount=min_amount,
                        min_distance_pct=rp_a.min_distance_pct,
                    )
                    bot._apply_inventory_skew(rp_a.target_ratio)
                    await bot.order_mgr.place_grid_orders(pair, bot._entry_filter_dict())

                    logger.info(
                        "%s Auto-Grid: %dB+%dS → %dB+%dS, avg %.8f/Order (Equity: %.2f, Regime: %s)",
                        pair, old_buy, old_sell, alloc.buy_count, alloc.sell_count,
                        alloc.amount_per_order, alloc.total_equity, rp.regime.value,
                    )
                    if self.cloud.connected:
                        asyncio.create_task(self.cloud.log_event(
                            "grid", f"Auto-Grid: {old_total} → {new_total} Level ({alloc.buy_count}B+{alloc.sell_count}S)",
                            {"pair": pair, "old_buy": old_buy, "old_sell": old_sell,
                             "new_buy": alloc.buy_count, "new_sell": alloc.sell_count,
                             "amount": alloc.amount_per_order, "equity": alloc.total_equity,
                             "rebalanced": alloc.rebalance_needed},
                        ))
            except Exception as e:
                logger.warning("Auto-grid adjust failed for %s: %s", pair, e)

    async def _execute_rebalance(self, action: dict):
        """Execute a small market order to rebalance capital."""
        try:
            pair = action["pair"]
            side = action["side"]
            amount = action["amount"]
            result = await self.exchange.async_create_market_order(pair, side, amount)
            logger.info(
                "Rebalance: %s %s %.8f @ %.2f",
                side, pair, result["amount"], result["price"],
            )
            if self.cloud.connected:
                asyncio.create_task(self.cloud.log_event(
                    "system", f"Rebalance: {side} {pair} {amount:.8f}",
                    {"side": side, "pair": pair, "amount": amount,
                     "fill_price": result["price"]},
                ))
        except Exception as e:
            logger.warning("Rebalance fehlgeschlagen: %s", e)

    async def _daily_report_loop(self):
        while self._running:
            await asyncio.sleep(86400)
            summaries = self.tracker.get_all_summaries()
            await self.telegram.send_daily_report(summaries)
            self.tracker.save_daily_report({"summaries": summaries})
            if self.config.is_pi:
                self.tracker.prune_old_snapshots(keep_days=30)

    async def _fee_refresh_loop(self):
        """Re-fetch exchange fees every 24 hours (fee tiers can change)."""
        while self._running:
            await asyncio.sleep(86400)
            try:
                await asyncio.to_thread(
                    self.fee_engine.fetch_fees, self.exchange, self.config.pairs,
                )
                for pair, bot in self.pair_bots.items():
                    bot._apply_fees_to_grid()
                logger.info("Fee-Engine: Gebuehren aktualisiert")
            except Exception as e:
                logger.warning("Fee-Refresh fehlgeschlagen: %s", e)

    async def _mtf_loop(self):
        """Update multi-timeframe analysis every 5 minutes."""
        await asyncio.sleep(60)
        while self._running:
            for pair, bot in self.pair_bots.items():
                try:
                    sig = await bot.mtf.update(self.exchange)
                    bot.regime.set_mtf(sig.trend_alignment, sig.entry_quality)
                except Exception as e:
                    logger.debug("MTF update failed for %s: %s", pair, e)
            await asyncio.sleep(300)

    async def _correlation_loop(self):
        """Update cross-pair correlations every 6 hours."""
        import numpy as _np
        await asyncio.sleep(300)
        while self._running:
            try:
                for pair, bot in self.pair_bots.items():
                    self.correlation.add_pair(pair)
                    ohlcv = await self.exchange.async_fetch_ohlcv(pair, timeframe="1h", limit=168)
                    if ohlcv and len(ohlcv) > 1:
                        closes = _np.array([c[4] for c in ohlcv], dtype=_np.float64)
                        returns = self.correlation.returns_from_ohlcv(closes)
                        if len(returns) > 0:
                            self.correlation.update(pair, returns)

                positions: dict[str, float] = {}
                prices: dict[str, float] = {}
                total_eq = 0.0
                for pair, bot in self.pair_bots.items():
                    inv = self.inventory.get_inventory(pair)
                    positions[pair] = inv.base_inventory
                    prices[pair] = bot.current_price or 0
                    alloc = bot.last_allocation
                    if alloc:
                        total_eq = max(total_eq, alloc.total_equity)

                result = self.correlation.compute(positions, prices, total_eq)

                for w in result.high_corr_warnings:
                    if w.get("extreme") and self.cloud.connected:
                        asyncio.create_task(self.cloud.log_event(
                            "risk",
                            f"{w['pair_a']} und {w['pair_b']} korrelieren extrem ({w['correlation']:.0%}) — Diversifikation pruefen",
                            w, level="warn",
                        ))

                if result.portfolio_var_pct > 5.0:
                    logger.warning(
                        "Portfolio-VaR %.1f%% ($%.2f) ueberschreitet 5%%-Schwelle",
                        result.portfolio_var_pct, result.portfolio_var_abs,
                    )
                    if self.cloud.connected:
                        asyncio.create_task(self.cloud.log_event(
                            "risk",
                            f"Portfolio-VaR hoch: {result.portfolio_var_pct:.1f}% (${result.portfolio_var_abs:.2f})",
                            {"var_pct": result.portfolio_var_pct, "var_abs": result.portfolio_var_abs},
                            level="warn",
                        ))

                logger.info(
                    "Correlation updated: %d pairs, VaR %.2f%% ($%.2f), %d warnings",
                    len(self.correlation.pairs), result.portfolio_var_pct,
                    result.portfolio_var_abs, len(result.high_corr_warnings),
                )
            except Exception as e:
                logger.debug("Correlation update failed: %s", e)
            await asyncio.sleep(21600)

    async def _sentiment_loop(self):
        """Fetch news sentiment every 15 min and inject into RegimeDetectors.

        Always runs; skips fetch when sentiment is disabled in config so
        the loop reacts immediately when the user enables sentiment via
        the dashboard.
        """
        while self._running:
            if not self.config.sentiment.enabled:
                await asyncio.sleep(30)
                continue

            try:
                self._sync_sentiment_runtime()

                signal = await self.sentiment.get_signal(self.config.pairs)
                for bot in self.pair_bots.values():
                    bot.regime.set_sentiment(signal.score, signal.confidence)

                effect = "neutral"
                if signal.confidence > 0.6 and abs(signal.score) > 0.3:
                    effect = "ADX-Shift"
                if signal.score < -0.7 and signal.confidence > 0.8:
                    effect = "VOLATILE-Override"

                logger.info(
                    "Sentiment: score=%+.2f, conf=%.0f%%, source=%s, "
                    "headlines=%d, regime_effect=%s, provider=%s, has_key=%s",
                    signal.score, signal.confidence * 100,
                    signal.source, len(signal.headlines), effect,
                    self.sentiment._provider,
                    bool(self.sentiment._api_key),
                )

                if abs(signal.score) > 0.5 and self.cloud.connected:
                    asyncio.create_task(self.cloud.log_event(
                        category="sentiment",
                        level="info",
                        message=f"News-Sentiment: {signal.score:+.2f} ({signal.reason})",
                        detail={
                            "score": signal.score,
                            "confidence": signal.confidence,
                            "headlines": signal.headlines[:5],
                            "source": signal.source,
                            "regime_effect": effect,
                        },
                    ))
            except Exception:
                logger.exception("Sentiment-Loop Fehler")
            await asyncio.sleep(self.config.sentiment.fetch_interval)

    def _sync_sentiment_runtime(self):
        """Push current config + env api_key into the live NewsSentiment instance."""
        import os
        api_key = self.config.sentiment.api_key or os.environ.get("XAI_API_KEY", "")
        provider = self.config.sentiment.provider
        if api_key and provider != "local":
            self.sentiment._api_key = api_key
            self.sentiment._provider = provider
        elif not api_key:
            self.sentiment._api_key = ""
            self.sentiment._provider = "local"

    async def _gc_loop(self):
        """Periodic garbage collection for memory-constrained Pi."""
        interval = self.config.pi.gc_interval_seconds
        while self._running:
            await asyncio.sleep(interval)
            collected = await asyncio.to_thread(gc.collect)
            if collected > 50:
                logger.debug("GC collected %d objects", collected)

    async def _memory_watchdog(self):
        """Monitor RSS and take action if memory grows too large (Pi only)."""
        WARN_MB = 350
        CRITICAL_MB = 400
        while self._running:
            await asyncio.sleep(300)
            try:
                rss_mb = 0
                try:
                    import resource
                    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    rss_mb = rss_kb / 1024
                except Exception:
                    with open("/proc/self/status") as f:
                        for line in f:
                            if line.startswith("VmRSS:"):
                                rss_mb = int(line.split()[1]) / 1024
                                break

                if rss_mb <= 0:
                    continue

                if rss_mb > CRITICAL_MB:
                    logger.warning("MEMORY CRITICAL: %d MB — saving state and requesting restart", int(rss_mb))
                    if self.cloud.connected:
                        await self._persist_state()
                        asyncio.create_task(self.cloud.log_event(
                            "memory", f"Kritisch: {int(rss_mb)} MB — Restart angefordert",
                            {"rss_mb": int(rss_mb)}, level="error",
                        ))
                    os._exit(1)

                elif rss_mb > WARN_MB:
                    logger.warning("MEMORY WARNING: %d MB — aggressive GC", int(rss_mb))
                    gc.collect()
                    for bot in self.pair_bots.values():
                        stats = self.tracker._get_pair_stats(bot.pair)
                        if len(stats.equity_history) > 200:
                            stats.equity_history = stats.equity_history[-100:]
                        if len(stats.pnl_history) > 200:
                            stats.pnl_history = stats.pnl_history[-100:]
                    gc.collect()
                    if self.cloud.connected:
                        asyncio.create_task(self.cloud.log_event(
                            "memory", f"Warnung: {int(rss_mb)} MB — GC + History gekuerzt",
                            {"rss_mb": int(rss_mb)}, level="warn",
                        ))
            except Exception as e:
                logger.debug("Memory watchdog error: %s", e)

    def _apply_config(self, payload: dict) -> list[str]:
        """Apply a config payload dict to self.config. Returns list of updated keys."""
        updated: list[str] = []
        sections = ["grid", "atr", "risk", "ml", "telegram", "websocket", "sentiment", "rl"]
        for section in sections:
            if section in payload:
                target = getattr(self.config, section, None)
                if target is None:
                    continue
                for k, v in payload[section].items():
                    if hasattr(target, k) and k not in ("database_url", "api_key"):
                        old_val = getattr(target, k)
                        if old_val != v:
                            setattr(target, k, v)
                            updated.append(f"{section}.{k}")
        if "cloud" in payload:
            for k, v in payload["cloud"].items():
                if hasattr(self.config.cloud, k) and k != "database_url":
                    setattr(self.config.cloud, k, v)
                    updated.append(f"cloud.{k}")
        if "pairs" in payload:
            self.config.pairs = payload["pairs"]
            updated.append("pairs")
        if any(k.startswith("sentiment.") for k in updated):
            self._sync_sentiment_runtime()
        if updated:
            logger.info("Config angewendet: %s", updated)
        return updated

    async def _sync_pairs(self):
        """Add new pairs and remove old ones dynamically."""
        current = set(self.pair_bots.keys())
        desired = set(self.config.pairs)
        to_add = desired - current
        to_remove = current - desired

        for pair in to_remove:
            bot = self.pair_bots.pop(pair, None)
            if bot:
                try:
                    await bot.order_mgr.cancel_all(pair)
                except Exception as e:
                    logger.warning("Fehler beim Entfernen von %s: %s", pair, e)
                logger.info("%s entfernt", pair)

        if to_add:
            try:
                await self.exchange.preload_markets(list(to_add))
            except Exception as e:
                logger.error("Markets laden fuer %s fehlgeschlagen: %s", to_add, e)
                return

            for pair in to_add:
                ml = None
                if self.config.ml.enabled:
                    from bot.config import PiConfig
                    pi_cfg = self.config.pi if self.config.is_pi else PiConfig()
                    ml = LSTMPredictor(self.config.ml, pair, pi_config=pi_cfg)
                bot = PairBot(pair, self.config, self.exchange, self.risk,
                              self.tracker, self.telegram, ml, self.cloud,
                              fee_engine=self.fee_engine, inventory=self.inventory,
                              inventory_skew=self.inv_skew,
                              circuit_breaker=self.cb)
                self.pair_bots[pair] = bot
                try:
                    await bot.initialize(self.allocator, len(self.pair_bots), None)
                    logger.info("%s hinzugefuegt und initialisiert", pair)
                except Exception as e:
                    logger.error("Initialisierung von %s fehlgeschlagen: %s", pair, e)

    async def stop(self):
        self._running = False
        logger.info("Graceful shutdown starting...")

        # Convert active trailing TPs to limit orders
        for entry in self.trailing_tp.get_entries():
            counter_side = "sell" if entry.side == "buy" else "buy"
            try:
                if counter_side == "sell":
                    await self.exchange.async_create_limit_sell(
                        entry.pair, entry.amount, entry.grid_level_price)
                else:
                    await self.exchange.async_create_limit_buy(
                        entry.pair, entry.amount, entry.grid_level_price)
                logger.info("Trailing-TP → Limit: %s %s @ %.2f", counter_side, entry.pair, entry.grid_level_price)
            except Exception as e:
                logger.warning("Trailing-TP conversion failed: %s", e)

        # Save RL weights before shutdown
        try:
            self.rl._save()
        except Exception:
            pass

        # Save state before shutdown
        if self.cloud.connected:
            try:
                await self._persist_state()
                logger.info("State saved before shutdown")
            except Exception as e:
                logger.warning("State save on shutdown failed: %s", e)

        for pair, bot in self.pair_bots.items():
            await bot.order_mgr.cancel_all(pair)
        if self.ws:
            await self.ws.stop()
        await self.cloud.stop()
        self.tracker.close()
        await self.exchange.close()
        logger.info("Multi-pair bot stopped")

    def get_status(self) -> dict:
        status = {}
        for pair, bot in self.pair_bots.items():
            s = bot.get_status()
            s["trailing_tp"] = self.trailing_tp.to_status(pair)
            s["trailing_tp_active"] = self._trailing_tp_enabled(bot)
            status[pair] = s
        return status

    def get_performance(self) -> list[dict]:
        return self.tracker.get_all_summaries()

    def resume(self):
        self.risk.resume()
        logger.info("Trading resumed across all pairs")

    def _register_cloud_commands(self):
        """Register command handlers for remote control from Vercel dashboard."""

        async def cmd_stop(payload):
            self._running = False
            for pair, bot in self.pair_bots.items():
                await bot.order_mgr.cancel_all(pair)
            self.cloud.update_status("stopped", self.config.pairs, {})
            return {"status": "stopped"}

        async def cmd_resume(payload):
            self.risk.resume()
            self._running = True
            self.cloud.update_status("running", self.config.pairs, {})
            return {"status": "resumed"}

        async def cmd_pause(payload):
            self._running = False
            self.cloud.update_status("paused", self.config.pairs, {})
            return {"status": "paused"}

        def cmd_status(payload):
            return self.get_status()

        def cmd_performance(payload):
            return {"summaries": self.get_performance()}

        async def cmd_update_config(payload):
            updated = self._apply_config(payload)

            if "pairs" in updated:
                await self._sync_pairs()

            if any(k.startswith("grid.") for k in updated):
                for pair, bot in list(self.pair_bots.items()):
                    if bot.current_range and bot.current_price:
                        try:
                            balance = await asyncio.to_thread(self.exchange.fetch_account_balances)
                            market = self.exchange._markets.get(pair, {})
                            min_notional = market.get("limits", {}).get("cost", {}).get("min", 5.0)
                            step_size = market.get("precision", {}).get("amount", 0.00001)
                            import math as _m
                            min_amount = _m.ceil((min_notional * 1.15) / bot.current_price / step_size) * step_size
                            alloc = self.allocator.allocate(
                                pair, balance, bot.current_price,
                                len(self.pair_bots), min_notional, step_size,
                            )
                            bot.apply_allocation(alloc, step_size=step_size, min_amount=min_amount)
                            await bot.order_mgr.cancel_all(pair)
                            rp_c = bot.regime.get_grid_params()
                            bot._apply_fees_to_grid()
                            bot.grid.calculate_grid(
                                bot.current_range, bot.current_price, alloc.amount_per_order,
                                buy_count=alloc.buy_count, sell_count=alloc.sell_count,
                                buy_budget=alloc.buy_budget, sell_budget=alloc.sell_budget,
                                step_size=step_size, min_amount=min_amount,
                                min_distance_pct=rp_c.min_distance_pct,
                            )
                            bot._apply_inventory_skew(rp_c.target_ratio)
                            await bot.order_mgr.place_grid_orders(pair, bot._entry_filter_dict())
                            actual = len(bot.grid.state.levels)
                            logger.info("%s Grid neu berechnet: %dB+%dS=%d Level, %.8f/Order",
                                        pair, alloc.buy_count, alloc.sell_count, actual, alloc.amount_per_order)
                        except Exception as e:
                            logger.error("Grid re-init failed for %s: %s", pair, e)
            if self.cloud.connected:
                metrics = {p: b.get_status() for p, b in self.pair_bots.items()}
                self.cloud.update_status("running", self.config.pairs, metrics)
                asyncio.create_task(self.cloud.log_event(
                    "config", f"Einstellungen aktualisiert: {', '.join(updated[:5])}",
                    {"keys": updated},
                ))
            return {"updated": updated}

        async def cmd_update_software(payload):
            import subprocess
            script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "auto_update.sh")
            if not os.path.isfile(script):
                return {"error": "auto_update.sh nicht gefunden"}
            install_dir = os.path.dirname(os.path.dirname(__file__))
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["bash", script, install_dir],
                    capture_output=True, text=True, timeout=90,
                )
                logger.info("Software-Update ausgefuehrt: %s", result.stdout.strip()[-200:])
                return {"stdout": result.stdout[-500:], "stderr": result.stderr[-200:], "code": result.returncode}
            except Exception as e:
                return {"error": str(e)}

        async def cmd_fetch_logs(payload):
            import subprocess
            lines = payload.get("lines", 200)
            lines = max(50, min(lines, 2000))
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["journalctl", "-u", "richbot", "--no-pager", "-n", str(lines)],
                    capture_output=True, text=True, timeout=15,
                )
                return {"logs": result.stdout, "lines": lines}
            except Exception as e:
                return {"error": str(e)}

        def cmd_reset_rl(payload):
            import numpy as _np
            self.rl.W = _np.zeros_like(self.rl.W)
            self.rl._episode_count = 0
            self.rl._exploration = 0.15
            self.rl._history.clear()
            self.rl._pending = None
            self.rl._save()
            logger.info("RL-Weights zurueckgesetzt")
            return {"status": "rl_reset", "episodes": 0}

        def cmd_rl_stats(payload):
            return self.rl.get_stats()

        self.cloud.on_command("stop", cmd_stop)
        self.cloud.on_command("resume", cmd_resume)
        self.cloud.on_command("pause", cmd_pause)
        self.cloud.on_command("status", cmd_status)
        self.cloud.on_command("performance", cmd_performance)
        self.cloud.on_command("update_config", cmd_update_config)
        self.cloud.on_command("update_software", cmd_update_software)
        self.cloud.on_command("fetch_logs", cmd_fetch_logs)
        self.cloud.on_command("reset_rl", cmd_reset_rl)
        self.cloud.on_command("rl_stats", cmd_rl_stats)
