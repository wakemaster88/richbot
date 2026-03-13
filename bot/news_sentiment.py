"""News-based sentiment analysis for crypto markets.

Fetches headlines from CryptoCompare, classifies sentiment via LLM
(Grok/OpenAI) or keyword fallback, and provides a structured signal
for the RegimeDetector.

Pi-safe: no heavy dependencies, async HTTP, bounded caches.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import time as _time
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_CRYPTOCOMPARE = "https://min-api.cryptocompare.com/data/v2/news/"

_BULLISH = frozenset({
    "surge", "surges", "surging", "rally", "rallies", "rallying",
    "approval", "approved", "adoption", "bullish", "record high",
    "all-time high", "ath", "etf", "institutional", "partnership",
    "upgrade", "breakout", "dovish", "inflow", "inflows",
})

_BEARISH = frozenset({
    "crash", "crashes", "crashing", "hack", "hacked", "exploit",
    "sec", "lawsuit", "ban", "banned", "fraud", "liquidation",
    "liquidated", "bearish", "dump", "dumping", "regulation",
    "crackdown", "hawkish", "outflow", "outflows", "investigation",
    "subpoena", "rug pull", "ponzi", "insolvency", "bankrupt",
})

_LLM_ENDPOINTS: dict[str, tuple[str, str]] = {
    "grok": ("https://api.x.ai/v1/chat/completions", "grok-3-mini"),
    "openai": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
}


@dataclass
class SentimentSignal:
    score: float
    confidence: float
    headlines: list[str] = field(default_factory=list)
    reason: str = ""
    source: str = "keyword"
    timestamp: float = 0.0


class NewsSentiment:
    """Fetches crypto news and classifies market sentiment."""

    def __init__(self, api_key: str = "", provider: str = "grok",
                 fetch_interval: int = 900, cache_validity: int = 1800):
        self._provider = provider if api_key else "local"
        self._api_key = api_key
        self._fetch_interval = fetch_interval
        self._cache_validity = cache_validity
        self._last_signal: SentimentSignal | None = None
        self._last_fetch: float = 0.0
        self._rate_limited_until: float = 0.0

    # ── public API ────────────────────────────────────────────────

    async def get_signal(self, pairs: list[str]) -> SentimentSignal:
        now = _time.time()

        if self._last_signal and now - self._last_fetch < self._fetch_interval:
            return self._last_signal

        headlines = await self.fetch_headlines(pairs)
        if not headlines:
            return self._last_signal or SentimentSignal(
                score=0.0, confidence=0.0, headlines=[],
                reason="Keine Headlines verfügbar", source="none",
                timestamp=now,
            )

        if self._api_key and self._provider != "local" and now > self._rate_limited_until:
            signal = await self._classify_with_llm(headlines)
        else:
            signal = self._keyword_fallback(headlines)

        self._last_signal = signal
        self._last_fetch = now
        return signal

    # ── headline fetching ─────────────────────────────────────────

    async def fetch_headlines(self, pairs: list[str]) -> list[dict]:
        try:
            return await asyncio.to_thread(self._fetch_headlines_sync, pairs)
        except Exception as e:
            logger.warning("Headline fetch failed: %s", e)
            return []

    def _fetch_headlines_sync(self, pairs: list[str]) -> list[dict]:
        coins = ",".join(p.split("/")[0] for p in pairs)
        url = f"{_CRYPTOCOMPARE}?categories={coins}&limit=20"
        try:
            req = Request(url, headers={"User-Agent": "RichBot/2.0"})
            resp = urlopen(req, timeout=10)
            data = _json.loads(resp.read().decode())
        except (URLError, HTTPError, OSError) as e:
            logger.warning("CryptoCompare unreachable: %s", e)
            return []

        raw_items = data.get("Data", [])
        if not raw_items:
            logger.info("CryptoCompare: 0 Artikel fuer %s (Response-Type: %s)",
                        coins, data.get("Type", "?"))

        cutoff = _time.time() - 8 * 3600
        seen: set[str] = set()
        out: list[dict] = []

        for item in raw_items:
            title = (item.get("title") or "").strip()
            if not title or title in seen:
                continue
            pub = item.get("published_on", 0)
            if pub < cutoff:
                continue
            seen.add(title)
            out.append({
                "title": title,
                "source": item.get("source", ""),
                "published_on": pub,
                "url": item.get("url", ""),
            })
        return out[:10]

    # ── LLM classification ────────────────────────────────────────

    async def _classify_with_llm(self, headlines: list[dict]) -> SentimentSignal:
        try:
            return await asyncio.to_thread(self._llm_call_sync, headlines)
        except Exception as e:
            logger.warning("LLM classification failed (%s), using keyword fallback: %s",
                           self._provider, e)
            return self._keyword_fallback(headlines)

    def _llm_call_sync(self, headlines: list[dict]) -> SentimentSignal:
        endpoint, model = _LLM_ENDPOINTS.get(
            self._provider, _LLM_ENDPOINTS["grok"]
        )

        titles = [h["title"] for h in headlines[:10]]
        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))

        prompt = (
            "Du bist ein Krypto-Marktanalyst. Analysiere diese News-Headlines "
            "und bewerte das Gesamtsentiment fuer die naechsten 1-4 Stunden.\n\n"
            f"Headlines:\n{numbered}\n\n"
            "Antworte NUR mit validem JSON, kein anderer Text:\n"
            '{"score": <float -1.0 bis 1.0>, "confidence": <float 0.0 bis 1.0>, '
            '"reason": "<maximal 1 Satz auf Deutsch>"}\n\n'
            "Score-Skala:\n"
            "-1.0 = Crash-Gefahr (Hack, Boersenverbot, Systemausfall)\n"
            "-0.5 = leicht bearish (Regulierungsdruck, negative Earnings)\n"
            " 0.0 = neutral oder gemischt\n"
            "+0.5 = leicht bullish (Adoption, Partnerships, positives Momentum)\n"
            "+1.0 = stark bullish (ETF-Approval, institutionelle Adoption, Fed dovish)"
        )

        body = _json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 100,
        }).encode()

        req = Request(
            endpoint, data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            resp = urlopen(req, timeout=10)
            raw = _json.loads(resp.read().decode())
        except HTTPError as e:
            status = e.code
            if status == 429:
                self._rate_limited_until = _time.time() + 300
                logger.warning("LLM rate-limited — Keyword-Fallback fuer 5 Min")
            raise
        except (URLError, OSError):
            raise

        content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            parsed = _json.loads(content)
        except _json.JSONDecodeError:
            logger.debug("LLM returned invalid JSON: %s", content[:120])
            return self._keyword_fallback(headlines)

        score = max(-1.0, min(1.0, float(parsed.get("score", 0))))
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.5))))
        reason = str(parsed.get("reason", ""))[:200]

        return SentimentSignal(
            score=score,
            confidence=confidence,
            headlines=titles[:5],
            reason=reason,
            source="llm",
            timestamp=_time.time(),
        )

    # ── keyword fallback ──────────────────────────────────────────

    def _keyword_fallback(self, headlines: list[dict]) -> SentimentSignal:
        titles = [h["title"] for h in headlines[:10]]
        bull = 0
        bear = 0

        for title in titles:
            words = title.lower()
            for kw in _BULLISH:
                if kw in words:
                    bull += 1
            for kw in _BEARISH:
                if kw in words:
                    bear += 1

        total = bull + bear
        if total == 0:
            return SentimentSignal(
                score=0.0, confidence=0.0, headlines=titles[:5],
                reason="Keine relevanten Keywords", source="keyword",
                timestamp=_time.time(),
            )

        score = max(-1.0, min(1.0, (bull - bear) / total))
        confidence = min(total / 5.0, 1.0)

        if bull > bear:
            reason = f"{bull} bullish vs {bear} bearish Keywords"
        elif bear > bull:
            reason = f"{bear} bearish vs {bull} bullish Keywords"
        else:
            reason = "Gemischte Signale"

        return SentimentSignal(
            score=score,
            confidence=confidence,
            headlines=titles[:5],
            reason=reason,
            source="keyword",
            timestamp=_time.time(),
        )
