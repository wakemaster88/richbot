"""Exchange abstraction layer using ccxt."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import ccxt
import ccxt.async_support as ccxt_async

from bot.config import ExchangeConfig

logger = logging.getLogger(__name__)


class Exchange:
    """Unified exchange interface with both sync and async support."""

    def __init__(self, config: ExchangeConfig):
        self.config = config
        self._sync: ccxt.Exchange | None = None
        self._async: ccxt_async.Exchange | None = None

    def _get_exchange_class(self, async_mode: bool = False):
        module = ccxt_async if async_mode else ccxt
        exchange_cls = getattr(module, self.config.name, None)
        if exchange_cls is None:
            raise ValueError(f"Exchange '{self.config.name}' not supported by ccxt")
        return exchange_cls

    def _build_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "enableRateLimit": self.config.rate_limit,
            "options": {
                "defaultType": "spot",
                "fetchMarkets": ["spot"],
                "fetchCurrencies": False,
                "warnOnFetchOpenOrdersWithoutSymbol": False,
            },
        }
        if self.config.api_key:
            params["apiKey"] = self.config.api_key
        if self.config.api_secret:
            params["secret"] = self.config.api_secret
        return params

    async def preload_markets(self, symbols: list[str]):
        """Load only specific markets via direct HTTP to minimize memory."""
        import json as _json
        import aiohttp

        client = self.async_client
        binance_syms = [s.replace("/", "") for s in symbols]

        if len(binance_syms) == 1:
            qs = f"symbol={binance_syms[0]}"
        else:
            qs = f'symbols={_json.dumps(binance_syms)}'

        url = f"https://api.binance.com/api/v3/exchangeInfo?{qs}"
        logger.info("Loading markets for %s", symbols)

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                exchange_info = await resp.json()

        parsed = []
        for sym_data in exchange_info.get("symbols", []):
            try:
                parsed.append(self._parse_binance_market(sym_data))
            except Exception as e:
                logger.warning("Failed to parse market %s: %s", sym_data.get("symbol"), e)

        if parsed:
            client.set_markets(parsed)
            logger.info("Loaded %d markets: %s", len(parsed), [m["symbol"] for m in parsed])
        else:
            raise RuntimeError("No markets parsed — check symbol names")

    @staticmethod
    def _parse_binance_market(data: dict) -> dict:
        """Parse Binance exchangeInfo symbol into ccxt market format."""
        base = data["baseAsset"]
        quote = data["quoteAsset"]
        symbol = f"{base}/{quote}"

        filters = {f["filterType"]: f for f in data.get("filters", [])}
        price_f = filters.get("PRICE_FILTER", {})
        lot_f = filters.get("LOT_SIZE", {})
        notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))

        def _precision(step: str) -> int:
            step = step.rstrip("0")
            if "." in step:
                return len(step.split(".")[1])
            return 0

        return {
            "id": data["symbol"],
            "symbol": symbol,
            "base": base,
            "quote": quote,
            "baseId": base,
            "quoteId": quote,
            "active": data.get("status") == "TRADING",
            "type": "spot",
            "spot": True,
            "margin": False,
            "swap": False,
            "future": False,
            "option": False,
            "contract": False,
            "precision": {
                "amount": _precision(lot_f.get("stepSize", "0.00001")),
                "price": _precision(price_f.get("tickSize", "0.01")),
            },
            "limits": {
                "amount": {
                    "min": float(lot_f.get("minQty", 0)),
                    "max": float(lot_f.get("maxQty", 0)),
                },
                "price": {
                    "min": float(price_f.get("minPrice", 0)),
                    "max": float(price_f.get("maxPrice", 0)),
                },
                "cost": {
                    "min": float(notional.get("minNotional", notional.get("notional", 0))),
                },
            },
            "info": data,
        }

    @property
    def sync(self) -> ccxt.Exchange:
        if self._sync is None:
            cls = self._get_exchange_class(async_mode=False)
            self._sync = cls(self._build_params())
            if self.config.sandbox:
                self._sync.set_sandbox_mode(True)
            logger.info("Sync exchange initialized: %s (sandbox=%s)", self.config.name, self.config.sandbox)
        return self._sync

    @property
    def async_client(self) -> ccxt_async.Exchange:
        if self._async is None:
            cls = self._get_exchange_class(async_mode=True)
            self._async = cls(self._build_params())
            if self.config.sandbox:
                self._async.set_sandbox_mode(True)
            logger.info("Async exchange initialized: %s", self.config.name)
        return self._async

    async def close(self):
        if self._async:
            await self._async.close()
            self._async = None

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        return self.sync.fetch_ticker(symbol)

    async def async_fetch_ticker(self, symbol: str) -> dict[str, Any]:
        return await self.async_client.fetch_ticker(symbol)

    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 500, since: int | None = None
    ) -> list[list]:
        return self.sync.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit, since=since)

    async def async_fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 500, since: int | None = None
    ) -> list[list]:
        return await self.async_client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit, since=since)

    def fetch_balance(self) -> dict[str, Any]:
        return self.sync.fetch_balance()

    async def async_fetch_balance(self) -> dict[str, Any]:
        return await self.async_client.fetch_balance()

    def create_limit_buy(self, symbol: str, amount: float, price: float, params: dict | None = None) -> dict:
        return self.sync.create_limit_buy_order(symbol, amount, price, params=params or {})

    def create_limit_sell(self, symbol: str, amount: float, price: float, params: dict | None = None) -> dict:
        return self.sync.create_limit_sell_order(symbol, amount, price, params=params or {})

    async def async_create_limit_buy(self, symbol: str, amount: float, price: float, params: dict | None = None) -> dict:
        return await self.async_client.create_limit_buy_order(symbol, amount, price, params=params or {})

    async def async_create_limit_sell(self, symbol: str, amount: float, price: float, params: dict | None = None) -> dict:
        return await self.async_client.create_limit_sell_order(symbol, amount, price, params=params or {})

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        return self.sync.cancel_order(order_id, symbol)

    async def async_cancel_order(self, order_id: str, symbol: str) -> dict:
        return await self.async_client.cancel_order(order_id, symbol)

    def cancel_all_orders(self, symbol: str) -> list:
        try:
            return self.sync.cancel_all_orders(symbol)
        except Exception:
            orders = self.sync.fetch_open_orders(symbol)
            results = []
            for order in orders:
                try:
                    results.append(self.sync.cancel_order(order["id"], symbol))
                except Exception as e:
                    logger.warning("Failed to cancel order %s: %s", order["id"], e)
            return results

    def fetch_open_orders(self, symbol: str) -> list[dict]:
        return self.sync.fetch_open_orders(symbol)

    async def async_fetch_open_orders(self, symbol: str) -> list[dict]:
        return await self.async_client.fetch_open_orders(symbol)

    def fetch_my_trades(self, symbol: str, since: int | None = None, limit: int = 100) -> list[dict]:
        return self.sync.fetch_my_trades(symbol, since=since, limit=limit)

    def get_market_info(self, symbol: str) -> dict:
        self.sync.load_markets()
        return self.sync.markets.get(symbol, {})

    def price_to_precision(self, symbol: str, price: float) -> str:
        return self.sync.price_to_precision(symbol, price)

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        return self.sync.amount_to_precision(symbol, amount)
