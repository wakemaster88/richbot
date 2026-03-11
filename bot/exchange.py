"""Exchange abstraction — direct HTTP for Pi, ccxt fallback for desktop."""

from __future__ import annotations

import hashlib
import hmac
import json as _json
import logging
import time as _time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bot.config import ExchangeConfig

logger = logging.getLogger(__name__)

_BINANCE = "https://api.binance.com"


class Exchange:
    """Unified exchange interface — prefers lightweight direct HTTP."""

    def __init__(self, config: ExchangeConfig):
        self.config = config
        self._markets: dict[str, dict] = {}

    # ── market data ──────────────────────────────────────────────

    async def preload_markets(self, symbols: list[str]):
        """Load only specific markets via direct HTTP to minimize memory."""
        binance_syms = [s.replace("/", "") for s in symbols]
        if len(binance_syms) == 1:
            qs = f"symbol={binance_syms[0]}"
        else:
            qs = f'symbols={_json.dumps(binance_syms, separators=(",", ":"))}'

        url = f"{_BINANCE}/api/v3/exchangeInfo?{qs}"
        logger.info("Loading markets for %s", symbols)

        resp = urlopen(url, timeout=15)
        exchange_info = _json.loads(resp.read().decode())

        for sym_data in exchange_info.get("symbols", []):
            try:
                m = self._parse_binance_market(sym_data)
                self._markets[m["symbol"]] = m
            except Exception as e:
                logger.warning("Failed to parse market %s: %s", sym_data.get("symbol"), e)

        if self._markets:
            logger.info("Loaded %d markets: %s", len(self._markets), list(self._markets.keys()))
        else:
            raise RuntimeError("No markets parsed — check symbol names")

    @staticmethod
    def _parse_binance_market(data: dict) -> dict:
        """Parse Binance exchangeInfo symbol into market dict."""
        base = data["baseAsset"]
        quote = data["quoteAsset"]
        symbol = f"{base}/{quote}"

        filters = {f["filterType"]: f for f in data.get("filters", [])}
        price_f = filters.get("PRICE_FILTER", {})
        lot_f = filters.get("LOT_SIZE", {})
        notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))

        return {
            "id": data["symbol"],
            "symbol": symbol,
            "base": base,
            "quote": quote,
            "precision": {
                "amount": float(lot_f.get("stepSize", "0.00001")),
                "price": float(price_f.get("tickSize", "0.01")),
            },
            "limits": {
                "amount": {"min": float(lot_f.get("minQty", 0)), "max": float(lot_f.get("maxQty", 0))},
                "price": {"min": float(price_f.get("minPrice", 0)), "max": float(price_f.get("maxPrice", 0))},
                "cost": {"min": float(notional.get("minNotional", notional.get("notional", 0)))},
            },
        }

    # ── precision helpers ────────────────────────────────────────

    @staticmethod
    def _step_format(value: float, step: float) -> str:
        """Truncate value to step-size precision and return as string."""
        if step <= 0 or step >= 1:
            return str(int(value))
        decimals = max(0, len(f"{step:.10f}".rstrip("0").split(".")[1]))
        truncated = int(value / step) * step
        return f"{truncated:.{decimals}f}"

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        m = self._markets.get(symbol)
        step = m["precision"]["amount"] if m else 0.00001
        return self._step_format(amount, step)

    def price_to_precision(self, symbol: str, price: float) -> str:
        m = self._markets.get(symbol)
        step = m["precision"]["price"] if m else 0.01
        return self._step_format(price, step)

    # ── signed request helper ────────────────────────────────────

    def _signed_request(self, method: str, path: str, extra: dict | None = None) -> dict:
        params = extra or {}
        params["timestamp"] = int(_time.time() * 1000)
        query = urlencode(params)
        sig = hmac.new(self.config.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"{_BINANCE}{path}?{query}&signature={sig}"
        req = Request(url, method=method, headers={"X-MBX-APIKEY": self.config.api_key})
        try:
            resp = urlopen(req, timeout=10)
            return _json.loads(resp.read().decode())
        except HTTPError as e:
            body = ""
            try:
                body = e.read().decode()
                err = _json.loads(body)
                msg = err.get("msg", body)
            except Exception:
                msg = body or str(e)
            raise Exception(f"binance {msg}") from None

    # ── public data (no auth) ────────────────────────────────────

    def fetch_ticker_http(self, symbol: str) -> dict:
        binance_sym = symbol.replace("/", "")
        url = f"{_BINANCE}/api/v3/ticker/24hr?symbol={binance_sym}"
        resp = urlopen(url, timeout=10)
        d = _json.loads(resp.read().decode())
        return {
            "symbol": symbol,
            "last": float(d["lastPrice"]),
            "bid": float(d["bidPrice"]),
            "ask": float(d["askPrice"]),
            "high": float(d["highPrice"]),
            "low": float(d["lowPrice"]),
            "volume": float(d["volume"]),
        }

    def fetch_ohlcv_http(self, symbol: str, interval: str = "1h", limit: int = 200) -> list[list]:
        binance_sym = symbol.replace("/", "")
        url = f"{_BINANCE}/api/v3/klines?symbol={binance_sym}&interval={interval}&limit={limit}"
        resp = urlopen(url, timeout=15)
        raw = _json.loads(resp.read().decode())
        return [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in raw]

    # ── account / balance (auth) ─────────────────────────────────

    def fetch_account_balances(self) -> dict[str, dict[str, float]]:
        data = self._signed_request("GET", "/api/v3/account")
        balances: dict[str, dict[str, float]] = {}
        for b in data.get("balances", []):
            free, locked = float(b["free"]), float(b["locked"])
            if free > 0 or locked > 0:
                balances[b["asset"]] = {"free": free, "used": locked, "total": free + locked}
        return balances

    # ── orders (auth) ────────────────────────────────────────────

    def create_order_http(self, symbol: str, side: str, amount: float, price: float) -> dict:
        binance_sym = symbol.replace("/", "")
        qty_str = self.amount_to_precision(symbol, amount)
        px_str = self.price_to_precision(symbol, price)
        data = self._signed_request("POST", "/api/v3/order", {
            "symbol": binance_sym,
            "side": side.upper(),
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": qty_str,
            "price": px_str,
        })
        return {"id": str(data["orderId"]), "symbol": symbol, "side": side.lower(),
                "price": float(data["price"]), "amount": float(data["origQty"]), "status": data["status"].lower()}

    def cancel_order_http(self, order_id: str, symbol: str) -> dict:
        binance_sym = symbol.replace("/", "")
        return self._signed_request("DELETE", "/api/v3/order", {"symbol": binance_sym, "orderId": order_id})

    def fetch_open_orders_http(self, symbol: str) -> list[dict]:
        binance_sym = symbol.replace("/", "")
        data = self._signed_request("GET", "/api/v3/openOrders", {"symbol": binance_sym})
        return [{"id": str(o["orderId"]), "symbol": symbol, "side": o["side"].lower(),
                 "price": float(o["price"]), "amount": float(o["origQty"]), "status": o["status"].lower()} for o in data]

    # ── async wrappers (run sync HTTP in thread) ─────────────────

    async def async_fetch_ticker(self, symbol: str) -> dict:
        import asyncio
        return await asyncio.to_thread(self.fetch_ticker_http, symbol)

    async def async_fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200, since: int | None = None) -> list[list]:
        import asyncio
        return await asyncio.to_thread(self.fetch_ohlcv_http, symbol, timeframe, limit)

    async def async_fetch_balance(self) -> dict[str, dict[str, float]]:
        import asyncio
        return await asyncio.to_thread(self.fetch_account_balances)

    async def async_create_limit_buy(self, symbol: str, amount: float, price: float, params: dict | None = None) -> dict:
        import asyncio
        return await asyncio.to_thread(self.create_order_http, symbol, "buy", amount, price)

    async def async_create_limit_sell(self, symbol: str, amount: float, price: float, params: dict | None = None) -> dict:
        import asyncio
        return await asyncio.to_thread(self.create_order_http, symbol, "sell", amount, price)

    async def async_cancel_order(self, order_id: str, symbol: str) -> dict:
        import asyncio
        return await asyncio.to_thread(self.cancel_order_http, order_id, symbol)

    async def async_fetch_open_orders(self, symbol: str) -> list[dict]:
        import asyncio
        return await asyncio.to_thread(self.fetch_open_orders_http, symbol)

    async def close(self):
        pass
