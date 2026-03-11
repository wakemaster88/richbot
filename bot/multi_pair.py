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

from bot.cloud_sync import CloudSync
from bot.config import BotConfig
from bot.dynamic_range import compute_dynamic_range, detect_range_breakout, shift_range, RangeResult
from bot.exchange import Exchange
from bot.grid_engine import GridEngine
from bot.ml_predictor import LSTMPredictor
from bot.order_manager import OrderManager
from bot.performance_tracker import PerformanceTracker, TradeRecord
from bot.risk_manager import RiskManager
from bot.telegram_bot import TelegramNotifier

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
                 cloud: CloudSync | None = None):
        self.pair = pair
        self.config = config
        self.exchange = exchange
        self.risk = risk_manager
        self.tracker = tracker
        self.telegram = telegram
        self.ml = ml_predictor
        self.cloud = cloud

        self.pair_grid_count = config.grid.grid_count
        self.pair_amount = config.grid.amount_per_order

        self.grid = GridEngine(
            grid_count=self.pair_grid_count,
            spacing_percent=config.grid.spacing_percent,
            amount_per_order=self.pair_amount,
            infinity_mode=config.grid.infinity_mode,
            trail_trigger_percent=config.grid.trail_trigger_percent,
        )
        self.order_mgr = OrderManager(exchange, self.grid, risk_manager, config)
        self.current_range: RangeResult | None = None
        self.current_price: float = 0.0
        self.last_prediction: dict | None = None
        self._running = False

        self.quote = pair.split("/")[1] if "/" in pair else "USDT"
        self.base = pair.split("/")[0] if "/" in pair else pair
        self.order_mgr.on_fill(self._on_fill)

    def _on_fill(self, managed_order):
        """Handle order fill events."""
        trade = TradeRecord(
            timestamp=managed_order.fill_time,
            pair=self.pair,
            side=managed_order.side,
            price=managed_order.fill_price,
            amount=managed_order.amount,
            fee=managed_order.fill_price * managed_order.amount * OrderManager.FEE_RATE,
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

        if self.cloud and self.cloud.connected:
            asyncio.create_task(self.cloud.sync_trade(trade))
            asyncio.create_task(self.cloud.log_event(
                "trade", f"{'Kauf' if managed_order.side == 'buy' else 'Verkauf'} {self.pair} @ {managed_order.fill_price:.2f}",
                {"side": managed_order.side, "price": managed_order.fill_price,
                 "amount": managed_order.amount, "pnl": managed_order.pnl},
            ))

    async def initialize(self):
        """Set up initial grid."""
        logger.info("Initializing pair bot for %s", self.pair)

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

        fetch_limit = self.config.pi.ohlcv_fetch_limit if self.config.is_pi else 200
        ohlcv = await self.exchange.async_fetch_ohlcv(self.pair, timeframe=self.config.atr.timeframe, limit=fetch_limit)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])

        ticker = await self.exchange.async_fetch_ticker(self.pair)
        self.current_price = ticker["last"]

        self.current_range = compute_dynamic_range(
            df, self.current_price, self.config.atr,
            self.config.grid.range_multiplier,
            self.ml, self.config.ml,
        )

        vol = self.risk.calculate_volatility(df["close"].values)
        balance = await asyncio.to_thread(self.exchange.fetch_account_balances)

        quote_bal = balance.get(self.quote, {})
        base = self.pair.split("/")[0]
        base_bal = balance.get(base, {})
        logger.info(
            "%s Balance — %s: free=%.4f, locked=%.4f, total=%.4f | %s: free=%.8f, locked=%.8f, total=%.8f",
            self.pair,
            self.quote, quote_bal.get("free", 0), quote_bal.get("used", 0), quote_bal.get("total", 0),
            base, base_bal.get("free", 0), base_bal.get("used", 0), base_bal.get("total", 0),
        )

        usdt_balance = quote_bal.get("free", 0)
        base_free = base_bal.get("free", 0)
        dynamic_amount = self.risk.calculate_position_size(usdt_balance, self.current_price, vol)

        market = self.exchange._markets.get(self.pair, {})
        min_notional = market.get("limits", {}).get("cost", {}).get("min", 5.0)
        step_size = market.get("precision", {}).get("amount", 0.00001)
        min_amount_for_notional = (min_notional * 1.15) / self.current_price
        import math
        min_amount_for_notional = math.ceil(min_amount_for_notional / step_size) * step_size
        if dynamic_amount * self.current_price < min_notional * 1.1:
            dynamic_amount = min_amount_for_notional
            logger.warning(
                "%s amount angepasst → %.8f BTC (≈%.2f %s) um Notional-Minimum %.2f zu erfuellen",
                self.pair, dynamic_amount, dynamic_amount * self.current_price, self.quote, min_notional,
            )

        grid_count = self.pair_grid_count
        buy_count = grid_count // 2
        sell_count = grid_count - buy_count
        max_buy_orders = int(usdt_balance / (dynamic_amount * self.current_price)) if self.current_price > 0 else 0
        max_sell_orders = int(base_free / dynamic_amount) if dynamic_amount > 0 else 0
        affordable = max_buy_orders + max_sell_orders

        if affordable < grid_count:
            old_count = grid_count
            grid_count = max(2, affordable)
            self.pair_grid_count = grid_count
            self.grid.grid_count = grid_count
            logger.warning(
                "%s Grid reduziert: %d → %d Level (max %d Buy + %d Sell leistbar)",
                self.pair, old_count, grid_count, min(max_buy_orders, grid_count // 2), min(max_sell_orders, grid_count - grid_count // 2),
            )

        logger.info(
            "%s Order-Sizing — %s: %.4f frei, %s: %.8f frei, amount: %.8f (≈%.2f %s), %d Level, minNotional: %.2f",
            self.pair, self.quote, usdt_balance, base, base_free,
            dynamic_amount, dynamic_amount * self.current_price, self.quote,
            grid_count, min_notional,
        )

        self.grid.calculate_grid(self.current_range, self.current_price, dynamic_amount)

        try:
            await self.order_mgr.place_grid_orders(self.pair)
        except Exception as e:
            logger.error("Failed to place initial orders for %s: %s", self.pair, e)

        actual_levels = len(self.grid.state.levels)
        logger.info(
            "%s initialized: price=%.2f, range=[%.2f, %.2f], levels=%d",
            self.pair, self.current_price, self.current_range.lower,
            self.current_range.upper, actual_levels,
        )

        if self.cloud and self.cloud.connected:
            asyncio.create_task(self.cloud.log_event(
                "system", f"{self.pair} gestartet — {actual_levels} Level, Preis {self.current_price:.2f}",
                {"pair": self.pair, "price": self.current_price,
                 "range": [self.current_range.lower, self.current_range.upper],
                 "levels": actual_levels, "amount": dynamic_amount,
                 "quote_free": usdt_balance, "base_free": base_free},
            ))

    async def update_tick(self, price: float):
        """Process a price update."""
        self.current_price = price

        can_trade, reason = self.risk.can_trade()
        if not can_trade:
            return

        triggered = self.order_mgr.check_trailing_stops(price)
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
                logger.info("%s: Range breakout %s at %.2f", self.pair, breakout, price)
                await self.order_mgr.cancel_all(self.pair)

                new_range = shift_range(self.current_range, breakout)
                self.current_range = new_range
                self.grid.trail_grid(breakout, price, new_range, self.pair_amount)
                placed = await self.order_mgr.place_grid_orders(self.pair)

                if self.cloud and self.cloud.connected:
                    asyncio.create_task(self.cloud.log_event(
                        "grid", f"Range-Verschiebung {breakout} — Grid neu berechnet",
                        {"direction": breakout, "price": price,
                         "new_range": [new_range.lower, new_range.upper],
                         "levels": len(self.grid.state.levels),
                         "orders_placed": len(placed)},
                    ))

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

            risk_status = self.risk.update_equity(usdt)
            if risk_status["is_paused"]:
                await self.telegram.alert_drawdown_stop(
                    self.pair, risk_status["drawdown_pct"], usdt,
                )
                await self.order_mgr.cancel_all(self.pair)
        except Exception as e:
            logger.error("Equity update failed for %s: %s", self.pair, e)

    def get_status(self) -> dict:
        open_orders = self.order_mgr.get_open_orders(self.pair)
        orders_list = [
            {"side": o.side, "price": o.price, "amount": o.amount, "id": o.order_id}
            for o in sorted(open_orders, key=lambda x: x.price)
        ]
        return {
            "pair": self.pair,
            "price": self.current_price,
            "range": f"[{self.current_range.lower:.2f}, {self.current_range.upper:.2f}]" if self.current_range else "N/A",
            "range_source": self.current_range.source if self.current_range else "N/A",
            "grid_levels": len(self.grid.state.levels),
            "grid_configured": self.pair_grid_count,
            "active_orders": len(open_orders),
            "filled_orders": len(self.order_mgr.get_filled_orders(self.pair)),
            "open_orders": orders_list,
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
        self.cloud = CloudSync(config.cloud)
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

        for pair in self.config.pairs:
            ml = None
            if self.config.ml.enabled:
                from bot.config import PiConfig
                pi_cfg = self.config.pi if self.config.is_pi else PiConfig()
                ml = LSTMPredictor(self.config.ml, pair, pi_config=pi_cfg)
            bot = PairBot(pair, self.config, self.exchange, self.risk,
                          self.tracker, self.telegram, ml, self.cloud)
            self.pair_bots[pair] = bot

        for pair, bot in self.pair_bots.items():
            try:
                await bot.initialize()
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
            asyncio.create_task(self._ml_loop()),
            asyncio.create_task(self._equity_loop()),
            asyncio.create_task(self._daily_report_loop()),
            asyncio.create_task(self._range_refresh_loop()),
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
            asyncio.create_task(self._range_refresh_loop()),
        ]
        if self.config.is_pi:
            tasks.append(asyncio.create_task(self._gc_loop()))
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

                            vol = self.risk.calculate_volatility(df["close"].values)
                            balance = await asyncio.to_thread(self.exchange.fetch_account_balances)
                            usdt = balance.get(bot.quote, {}).get("free", 10000)
                            dynamic_amount = self.risk.calculate_position_size(usdt, price, vol)

                            bot.grid.calculate_grid(new_range, price, dynamic_amount)
                            await bot.order_mgr.place_grid_orders(pair)
                        else:
                            logger.debug("%s: Range refresh — drift %.1f%%, keeping current grid", pair, drift * 100)
                except Exception as e:
                    logger.error("Range refresh failed for %s: %s", pair, e)

    async def _poll_loop(self):
        while self._running:
            for pair, bot in self.pair_bots.items():
                try:
                    ticker = await self.exchange.async_fetch_ticker(pair)
                    await bot.update_tick(ticker["last"])

                    filled = await bot.order_mgr.check_fills(pair)
                    for managed in filled:
                        opposite = bot.grid.get_opposite_level(managed.grid_level)
                        if opposite:
                            try:
                                if opposite.side == "buy":
                                    order = await self.exchange.async_create_limit_buy(
                                        pair, opposite.amount, opposite.price)
                                else:
                                    order = await self.exchange.async_create_limit_sell(
                                        pair, opposite.amount, opposite.price)
                                opposite.order_id = order["id"]
                                bot.order_mgr.orders[order["id"]] = OrderManager.create_managed(
                                    order["id"], pair, opposite)
                                bot.risk.add_trailing_stop(opposite.level_id, opposite.side, opposite.price)
                                logger.info("Gegenseite platziert: %s %s @ %.2f", opposite.side, pair, opposite.price)
                            except Exception as e:
                                logger.warning("Gegenseite fehlgeschlagen: %s %s @ %.2f: %s",
                                               opposite.side, pair, opposite.price, e)
                                if self.cloud.connected:
                                    asyncio.create_task(self.cloud.log_event(
                                        "error", f"Gegenseite fehlgeschlagen: {opposite.side} @ {opposite.price:.2f}",
                                        {"pair": pair, "side": opposite.side, "price": opposite.price, "reason": str(e)},
                                        level="warn",
                                    ))

                    unplaced = bot.grid.get_levels_to_place()
                    if unplaced:
                        recovered = await bot.order_mgr.place_grid_orders(pair)
                        if recovered and self.cloud.connected:
                            asyncio.create_task(self.cloud.log_event(
                                "grid", f"{len(recovered)} Order(s) nachplatziert",
                                {"pair": pair, "count": len(recovered),
                                 "orders": [{"side": o.side, "price": o.price} for o in recovered]},
                            ))

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
        cycle = 0
        while self._running:
            await asyncio.gather(
                *(bot.update_equity() for bot in self.pair_bots.values()),
                return_exceptions=True,
            )
            self.tracker.flush()

            cycle += 1
            if cycle % 5 == 0:
                await self._auto_adjust_grid()

            if self.cloud.connected:
                metrics = {pair: bot.get_status() for pair, bot in self.pair_bots.items()}
                self.cloud.update_status("running", self.config.pairs, metrics)

            await asyncio.sleep(60)

    @staticmethod
    def _score_grid(n: int, half_equity: float, price: float,
                    min_amount: float, step_size: float) -> tuple[float, float]:
        """Score a grid level count by expected daily return. Returns (score, amount)."""
        import math as _m
        ROUNDTRIP_FEE = 0.002
        DAILY_VOL = 0.025
        R = DAILY_VOL * 1.2

        per_side = n // 2
        raw = half_equity / (per_side * price)
        amount = _m.ceil(raw / step_size) * step_size
        if amount * price * per_side > half_equity * 1.05:
            amount = min_amount
        if amount < min_amount:
            return (-1.0, min_amount)
        if amount * price * per_side > half_equity * 1.05:
            return (-1.0, min_amount)

        order_val = amount * price
        positions = [((i + 1) / per_side) ** 0.6 for i in range(per_side)]

        daily = 0.0
        for i in range(per_side):
            dist = R * positions[i]
            spacing = dist if i == 0 else R * (positions[i] - positions[i - 1])
            if spacing <= ROUNDTRIP_FEE:
                continue
            profit = (spacing - ROUNDTRIP_FEE) * order_val
            rt = min(3.0, DAILY_VOL / (2 * dist) * 0.5)
            daily += profit * rt
        return (daily, amount)

    async def _auto_adjust_grid(self):
        """Adjust grid to maximize expected return — runs every ~5 minutes."""
        import math

        pair_count = len(self.pair_bots)
        for pair, bot in list(self.pair_bots.items()):
            try:
                balance = await asyncio.to_thread(self.exchange.fetch_account_balances)
                quote_bal = balance.get(bot.quote, {})
                base_name = pair.split("/")[0]
                base_bal = balance.get(base_name, {})
                price = bot.current_price or 0

                if price <= 0:
                    continue

                quote_total = quote_bal.get("total", 0)
                base_total = base_bal.get("total", 0)
                total_equity = quote_total + base_total * price

                if total_equity <= 0:
                    continue

                market = self.exchange._markets.get(pair, {})
                min_notional = market.get("limits", {}).get("cost", {}).get("min", 5.0)
                step_size = market.get("precision", {}).get("amount", 0.00001)

                min_amount = math.ceil((min_notional * 1.15) / price / step_size) * step_size

                eq_per_pair = total_equity / max(1, pair_count)
                half = eq_per_pair * 0.80 / 2

                best_n = 4
                best_score = -1.0
                best_amount = min_amount
                for n in range(4, 22, 2):
                    score, amount = self._score_grid(n, half, price, min_amount, step_size)
                    if score > best_score:
                        best_score = score
                        best_n = n
                        best_amount = amount

                configured = bot.pair_grid_count
                if best_n != configured and abs(best_n - configured) >= 2:
                    old = configured
                    bot.pair_grid_count = best_n
                    bot.pair_amount = best_amount

                    bot.grid = GridEngine(
                        grid_count=best_n,
                        spacing_percent=self.config.grid.spacing_percent,
                        amount_per_order=best_amount,
                        infinity_mode=self.config.grid.infinity_mode,
                        trail_trigger_percent=self.config.grid.trail_trigger_percent,
                    )
                    bot.order_mgr.grid = bot.grid

                    if bot.current_range and price:
                        await bot.order_mgr.cancel_all(pair)
                        bot.grid.calculate_grid(bot.current_range, price, best_amount)
                        await bot.order_mgr.place_grid_orders(pair)

                    daily_pct = (best_score / eq_per_pair * 100) if eq_per_pair > 0 else 0
                    logger.info(
                        "%s Auto-Grid: %d → %d Level (~%.2f%%/Tag, Kapital: %.2f %s, %.8f %s/Order)",
                        pair, old, best_n, daily_pct, total_equity, bot.quote, best_amount, bot.base,
                    )
                    if self.cloud.connected:
                        asyncio.create_task(self.cloud.log_event(
                            "grid", f"Auto-Grid: {old} → {best_n} Level (~{daily_pct:.2f}%/Tag)",
                            {"pair": pair, "old_levels": old, "new_levels": best_n,
                             "daily_pct": daily_pct, "equity": total_equity, "amount": best_amount},
                        ))
            except Exception as e:
                logger.warning("Auto-grid adjust failed for %s: %s", pair, e)

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
            collected = await asyncio.to_thread(gc.collect)
            if collected > 50:
                logger.debug("GC collected %d objects", collected)

    def _apply_config(self, payload: dict) -> list[str]:
        """Apply a config payload dict to self.config. Returns list of updated keys."""
        updated: list[str] = []
        sections = ["grid", "atr", "risk", "ml", "telegram", "websocket"]
        for section in sections:
            if section in payload:
                target = getattr(self.config, section, None)
                if target is None:
                    continue
                for k, v in payload[section].items():
                    if hasattr(target, k) and (section != "cloud" or k != "database_url"):
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
                              self.tracker, self.telegram, ml, self.cloud)
                self.pair_bots[pair] = bot
                try:
                    await bot.initialize()
                    logger.info("%s hinzugefuegt und initialisiert", pair)
                except Exception as e:
                    logger.error("Initialisierung von %s fehlgeschlagen: %s", pair, e)

    async def stop(self):
        self._running = False
        for pair, bot in self.pair_bots.items():
            await bot.order_mgr.cancel_all(pair)
        if self.ws:
            await self.ws.stop()
        await self.cloud.stop()
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
                    bot.pair_grid_count = self.config.grid.grid_count
                    bot.pair_amount = self.config.grid.amount_per_order
                    bot.grid = GridEngine(
                        grid_count=bot.pair_grid_count,
                        spacing_percent=self.config.grid.spacing_percent,
                        amount_per_order=bot.pair_amount,
                        infinity_mode=self.config.grid.infinity_mode,
                        trail_trigger_percent=self.config.grid.trail_trigger_percent,
                    )
                    bot.order_mgr.grid = bot.grid
                    if bot.current_range and bot.current_price:
                        try:
                            await bot.order_mgr.cancel_all(pair)
                            bot.grid.calculate_grid(bot.current_range, bot.current_price, bot.pair_amount)
                            await bot.order_mgr.place_grid_orders(pair)
                            actual = len(bot.grid.state.levels)
                            logger.info("%s Grid neu berechnet: %d/%d Level (angefragt/tatsaechlich), %.8f %s/Order",
                                        pair, bot.pair_grid_count, actual, bot.pair_amount, bot.base)
                            if actual < bot.pair_grid_count:
                                logger.warning("%s Nur %d von %d Level moeglich (Range zu eng fuer Fee-Spacing)",
                                               pair, actual, bot.pair_grid_count)
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

        self.cloud.on_command("stop", cmd_stop)
        self.cloud.on_command("resume", cmd_resume)
        self.cloud.on_command("pause", cmd_pause)
        self.cloud.on_command("status", cmd_status)
        self.cloud.on_command("performance", cmd_performance)
        self.cloud.on_command("update_config", cmd_update_config)
        self.cloud.on_command("update_software", cmd_update_software)
        self.cloud.on_command("fetch_logs", cmd_fetch_logs)
