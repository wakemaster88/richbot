"""WebSocket real-time client using ccxt.pro for ticker, orderbook, and trade updates."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from bot.config import WebSocketConfig

logger = logging.getLogger(__name__)


class WebSocketClient:
    """Real-time market data and order updates via ccxt.pro WebSockets."""

    def __init__(self, config: WebSocketConfig, exchange_name: str = "binance",
                 api_key: str = "", api_secret: str = "", sandbox: bool = True):
        self.config = config
        self.exchange_name = exchange_name
        self.api_key = api_key
        self.api_secret = api_secret
        self.sandbox = sandbox
        self._exchange = None
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._reconnect_count = 0

        self._ticker_callbacks: list[Callable] = []
        self._orderbook_callbacks: list[Callable] = []
        self._trade_callbacks: list[Callable] = []
        self._order_callbacks: list[Callable] = []

    async def _get_exchange(self):
        if self._exchange is None:
            try:
                import ccxt.pro as ccxtpro
            except ImportError:
                logger.error("ccxt.pro not installed. Install with: pip install 'ccxt[pro]'")
                raise

            exchange_cls = getattr(ccxtpro, self.exchange_name, None)
            if exchange_cls is None:
                raise ValueError(f"Exchange '{self.exchange_name}' not in ccxt.pro")

            params: dict[str, Any] = {"enableRateLimit": True}
            if self.api_key:
                params["apiKey"] = self.api_key
            if self.api_secret:
                params["secret"] = self.api_secret

            self._exchange = exchange_cls(params)
            if self.sandbox:
                self._exchange.set_sandbox_mode(True)

            logger.info("ccxt.pro WebSocket exchange initialized: %s", self.exchange_name)
        return self._exchange

    def on_ticker(self, callback: Callable):
        self._ticker_callbacks.append(callback)

    def on_orderbook(self, callback: Callable):
        self._orderbook_callbacks.append(callback)

    def on_trade(self, callback: Callable):
        self._trade_callbacks.append(callback)

    def on_order_update(self, callback: Callable):
        self._order_callbacks.append(callback)

    async def _watch_ticker(self, symbol: str):
        exchange = await self._get_exchange()
        while self._running:
            try:
                ticker = await exchange.watch_ticker(symbol)
                for cb in self._ticker_callbacks:
                    try:
                        result = cb(symbol, ticker)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error("Ticker callback error: %s", e)
                self._reconnect_count = 0
            except Exception as e:
                if not self._running:
                    break
                logger.warning("Ticker WS error for %s: %s", symbol, e)
                await self._handle_reconnect()

    async def _watch_orderbook(self, symbol: str):
        exchange = await self._get_exchange()
        while self._running:
            try:
                ob = await exchange.watch_order_book(symbol)
                for cb in self._orderbook_callbacks:
                    try:
                        result = cb(symbol, ob)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error("Orderbook callback error: %s", e)
                self._reconnect_count = 0
            except Exception as e:
                if not self._running:
                    break
                logger.warning("Orderbook WS error for %s: %s", symbol, e)
                await self._handle_reconnect()

    async def _watch_trades(self, symbol: str):
        exchange = await self._get_exchange()
        while self._running:
            try:
                trades = await exchange.watch_trades(symbol)
                for cb in self._trade_callbacks:
                    try:
                        result = cb(symbol, trades)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error("Trades callback error: %s", e)
                self._reconnect_count = 0
            except Exception as e:
                if not self._running:
                    break
                logger.warning("Trades WS error for %s: %s", symbol, e)
                await self._handle_reconnect()

    async def _watch_orders(self, symbol: str):
        exchange = await self._get_exchange()
        while self._running:
            try:
                orders = await exchange.watch_orders(symbol)
                for order in orders:
                    if order.get("status") in ("closed", "filled"):
                        for cb in self._order_callbacks:
                            try:
                                result = cb(symbol, order)
                                if asyncio.iscoroutine(result):
                                    await result
                            except Exception as e:
                                logger.error("Order callback error: %s", e)
                self._reconnect_count = 0
            except Exception as e:
                if not self._running:
                    break
                logger.warning("Orders WS error for %s: %s", symbol, e)
                await self._handle_reconnect()

    async def _handle_reconnect(self):
        self._reconnect_count += 1
        if self._reconnect_count > self.config.max_reconnect_attempts:
            logger.critical("Max reconnect attempts reached, stopping WebSocket")
            self._running = False
            return

        delay = min(self.config.reconnect_delay * (2 ** min(self._reconnect_count, 6)), 120)
        logger.info("Reconnecting in %.1fs (attempt %d/%d)",
                     delay, self._reconnect_count, self.config.max_reconnect_attempts)
        await asyncio.sleep(delay)

        if self._exchange:
            try:
                await self._exchange.close()
            except Exception:
                pass
            self._exchange = None

    async def start(self, symbols: list[str]):
        """Start WebSocket streams for all symbols."""
        self._running = True
        logger.info("Starting WebSocket streams for %s", symbols)

        for symbol in symbols:
            if self._ticker_callbacks:
                self._tasks.append(asyncio.create_task(self._watch_ticker(symbol)))
            if self._orderbook_callbacks:
                self._tasks.append(asyncio.create_task(self._watch_orderbook(symbol)))
            if self._trade_callbacks:
                self._tasks.append(asyncio.create_task(self._watch_trades(symbol)))
            if self._order_callbacks:
                self._tasks.append(asyncio.create_task(self._watch_orders(symbol)))

    async def stop(self):
        """Stop all WebSocket streams gracefully."""
        self._running = False
        for task in self._tasks:
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        if self._exchange:
            try:
                await self._exchange.close()
            except Exception:
                pass
            self._exchange = None

        logger.info("WebSocket streams stopped")

    @property
    def is_running(self) -> bool:
        return self._running
