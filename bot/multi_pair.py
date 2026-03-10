"""Multi-pair orchestrator: runs grid bots for multiple trading pairs concurrently.

Pi-optimized: periodic GC, bounded buffers, reduced fetch limits.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time

import pandas as pd

from bot.config import BotConfig
from bot.dynamic_range import compute_dynamic_range, detect_range_breakout, shift_range, RangeResult
from bot.exchange import Exchange
from bot.grid_engine import GridEngine
from bot.ml_predictor import LSTMPredictor
from bot.order_manager import OrderManager
from bot.performance_tracker import PerformanceTracker, TradeRecord
from bot.risk_manager import RiskManager
from bot.telegram_bot import TelegramNotifier
from bot.ws_client import WebSocketClient

logger = logging.getLogger(__name__)


class PairBot:
    """Grid bot instance for a single trading pair."""

    def __init__(self, pair: str, config: BotConfig, exchange: Exchange,
                 risk_manager: RiskManager, tracker: PerformanceTracker,
                 telegram: TelegramNotifier, ml_predictor: LSTMPredictor | None = None):
        self.pair = pair
        self.config = config
        self.exchange = exchange
        self.risk = risk_manager
        self.tracker = tracker
        self.telegram = telegram
        self.ml = ml_predictor

        self.grid = GridEngine(
            grid_count=config.grid.grid_count,
            spacing_percent=config.grid.spacing_percent,
            amount_per_order=config.grid.amount_per_order,
            infinity_mode=config.grid.infinity_mode,
            trail_trigger_percent=config.grid.trail_trigger_percent,
        )
        self.order_mgr = OrderManager(exchange, self.grid, risk_manager, config)
        self.current_range: RangeResult | None = None
        self.current_price: float = 0.0
        self.last_prediction: dict | None = None
        self._running = False

        self.order_mgr.on_fill(self._on_fill)

    def _on_fill(self, managed_order):
        """Handle order fill events."""
        trade = TradeRecord(
            timestamp=managed_order.fill_time,
            pair=self.pair,
            side=managed_order.side,
            price=managed_order.fill_price,
            amount=managed_order.amount,
            fee=managed_order.fill_price * managed_order.amount * 0.001,
            pnl=managed_order.pnl,
            grid_level=managed_order.grid_level.price,
            order_id=managed_order.order_id,
        )
        self.tracker.record_trade(trade)

        asyncio.create_task(
            self.telegram.alert_fill(
                self.pair, managed_order.side, managed_order.fill_price,
                managed_order.amount, managed_order.pnl,
            )
        )

    async def initialize(self):
        """Set up initial grid."""
        logger.info("Initializing pair bot for %s", self.pair)

        fetch_limit = self.config.pi.ohlcv_fetch_limit if self.config.is_pi else 200
        ohlcv = self.exchange.fetch_ohlcv(self.pair, timeframe=self.config.atr.timeframe, limit=fetch_limit)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])

        ticker = self.exchange.fetch_ticker(self.pair)
        self.current_price = ticker["last"]

        self.current_range = compute_dynamic_range(
            df, self.current_price, self.config.atr,
            self.config.grid.range_multiplier,
            self.ml, self.config.ml,
        )

        vol = self.risk.calculate_volatility(df["close"].values)
        balance = self.exchange.fetch_balance()
        usdt_balance = balance.get("USDT", {}).get("free", 10000)
        dynamic_amount = self.risk.calculate_position_size(usdt_balance, self.current_price, vol)

        self.grid.calculate_grid(self.current_range, self.current_price, dynamic_amount)

        try:
            await self.order_mgr.place_grid_orders(self.pair)
        except Exception as e:
            logger.error("Failed to place initial orders for %s: %s", self.pair, e)

        logger.info(
            "%s initialized: price=%.2f, range=[%.2f, %.2f], levels=%d",
            self.pair, self.current_price, self.current_range.lower,
            self.current_range.upper, len(self.grid.state.levels),
        )

    async def update_tick(self, price: float):
        """Process a price update."""
        self.current_price = price

        can_trade, reason = self.risk.can_trade()
        if not can_trade:
            return

        triggered = self.order_mgr.check_trailing_stops(price)
        if triggered:
            logger.info("%s: %d trailing stops triggered", self.pair, len(triggered))

        if self.current_range:
            breakout = detect_range_breakout(price, self.current_range)
            if breakout:
                logger.info("%s: Range breakout %s at %.2f", self.pair, breakout, price)
                await self.order_mgr.cancel_all(self.pair)

                new_range = shift_range(self.current_range, breakout)
                self.current_range = new_range

                vol = self.risk.calculate_volatility(
                    [self.current_price] * 20
                )
                self.grid.trail_grid(breakout, price, new_range)
                await self.order_mgr.place_grid_orders(self.pair)

                await self.telegram.alert_range_shift(
                    self.pair, breakout, new_range.lower, new_range.upper,
                    source=new_range.source,
                )

    async def update_fill(self, order_data: dict):
        """Process a fill from WebSocket."""
        managed = self.order_mgr.process_ws_fill(order_data)
        if managed:
            opposite = self.grid.get_opposite_level(managed.grid_level)
            if opposite:
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
            balance = await self.exchange.async_fetch_balance()
            usdt = balance.get("USDT", {}).get("total", 0)
            self.tracker.update_equity(self.pair, usdt)

            risk_status = self.risk.update_equity(usdt)
            if risk_status["is_paused"]:
                await self.telegram.alert_drawdown_stop(
                    self.pair, risk_status["drawdown_pct"], usdt,
                )
                await self.order_mgr.cancel_all(self.pair)
        except Exception as e:
            logger.error("Equity update failed for %s: %s", self.pair, e)

    def get_status(self) -> dict:
        return {
            "pair": self.pair,
            "price": self.current_price,
            "range": f"[{self.current_range.lower:.2f}, {self.current_range.upper:.2f}]" if self.current_range else "N/A",
            "range_source": self.current_range.source if self.current_range else "N/A",
            "grid_levels": len(self.grid.state.levels),
            "active_orders": len(self.order_mgr.get_open_orders(self.pair)),
            "filled_orders": len(self.order_mgr.get_filled_orders(self.pair)),
            "last_prediction": self.last_prediction,
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
        self.ws: WebSocketClient | None = None
        self.pair_bots: dict[str, PairBot] = {}
        self._running = False

    async def start(self):
        """Start trading all configured pairs."""
        logger.info("Starting multi-pair bot for %s", self.config.pairs)

        for pair in self.config.pairs:
            ml = None
            if self.config.ml.enabled:
                from bot.config import PiConfig
                pi_cfg = self.config.pi if self.config.is_pi else PiConfig()
                ml = LSTMPredictor(self.config.ml, pair, pi_config=pi_cfg)
            bot = PairBot(pair, self.config, self.exchange, self.risk,
                          self.tracker, self.telegram, ml)
            self.pair_bots[pair] = bot

        for pair, bot in self.pair_bots.items():
            try:
                await bot.initialize()
            except Exception as e:
                logger.error("Failed to initialize %s: %s", pair, e)

        await self.telegram.send_startup_message(self.config.pairs)

        self._running = True

        if self.config.websocket.enabled:
            await self._run_websocket()
        else:
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
            asyncio.create_task(self._ml_loop()),
            asyncio.create_task(self._equity_loop()),
            asyncio.create_task(self._daily_report_loop()),
        ]
        if self.config.is_pi:
            tasks.append(asyncio.create_task(self._gc_loop()))

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
            asyncio.create_task(self._ml_loop()),
            asyncio.create_task(self._equity_loop()),
            asyncio.create_task(self._daily_report_loop()),
        ]
        if self.config.is_pi:
            tasks.append(asyncio.create_task(self._gc_loop()))
        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            for t in tasks:
                t.cancel()

    async def _poll_loop(self):
        while self._running:
            for pair, bot in self.pair_bots.items():
                try:
                    ticker = await self.exchange.async_fetch_ticker(pair)
                    await bot.update_tick(ticker["last"])
                    await bot.order_mgr.check_fills(pair)
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
            await bot.update_fill(order)

    async def _ml_loop(self):
        interval = self.config.ml.prediction_interval_minutes * 60
        while self._running:
            for pair, bot in self.pair_bots.items():
                await bot.run_ml_prediction()
            await asyncio.sleep(interval)

    async def _equity_loop(self):
        while self._running:
            for pair, bot in self.pair_bots.items():
                await bot.update_equity()
            await asyncio.sleep(60)

    async def _daily_report_loop(self):
        while self._running:
            await asyncio.sleep(86400)
            summaries = self.tracker.get_all_summaries()
            await self.telegram.send_daily_report(summaries)
            self.tracker.save_daily_report({"summaries": summaries})
            if self.config.is_pi:
                self.tracker.prune_old_snapshots(keep_days=30)

    async def _gc_loop(self):
        """Periodic garbage collection for memory-constrained Pi."""
        interval = self.config.pi.gc_interval_seconds
        while self._running:
            await asyncio.sleep(interval)
            collected = gc.collect()
            if collected > 0:
                logger.debug("GC collected %d objects", collected)

    async def stop(self):
        self._running = False
        for pair, bot in self.pair_bots.items():
            await bot.order_mgr.cancel_all(pair)
        if self.ws:
            await self.ws.stop()
        self.tracker.close()
        await self.exchange.close()
        logger.info("Multi-pair bot stopped")

    def get_status(self) -> dict:
        return {pair: bot.get_status() for pair, bot in self.pair_bots.items()}

    def get_performance(self) -> list[dict]:
        return self.tracker.get_all_summaries()

    def resume(self):
        self.risk.resume()
        logger.info("Trading resumed across all pairs")
