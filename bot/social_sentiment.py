"""Social sentiment sources: Reddit, Fear&Greed, Twitter (+ News aggregation).

Pi-safe: async HTTP, bounded caches (~20KB total), fallback-kaskade.
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

_FNG_URL = "https://api.alternative.me/fng/?limit=1"
_REDDIT_BASE = "https://www.reddit.com"
_REDDIT_SUBS = ["Bitcoin", "CryptoCurrency", "solana"]
_CRYPTO_INFLUENCERS = [
    "APompliano", "VitalikButerin", "saylor", "CZ_Binance",
    "elikrieg", "DocumentingBTC", "BrendanEich",
]  # Handles for reference; actual fetch via proxy/API

# Cache TTLs (sec): News 15min, Twitter 30min, Reddit 60min, FnG 6h
TTL_NEWS = 900
TTL_TWITTER = 1800
TTL_REDDIT = 3600
TTL_FNG = 21600


@dataclass
class SourceScore:
    name: str
    score: float
    confidence: float
    raw: float | None = None
    sample: list[str] = field(default_factory=list)


@dataclass
class AggregatedSignal:
    score: float
    confidence: float
    sources: dict[str, SourceScore]
    consensus: str
    fear_greed: int | None
    headlines: list[str]
    reason: str
    timestamp: float


def _fetch_json(url: str, headers: dict | None = None, timeout: int = 10) -> dict | None:
    hdr = {
        "User-Agent": "Mozilla/5.0 (compatible; RichBot/2.0; +https://github.com/richbot)",
        "Accept": "application/json",
    }
    if headers:
        hdr.update(headers)
    try:
        req = Request(url, headers=hdr)
        resp = urlopen(req, timeout=timeout)
        return _json.loads(resp.read().decode())
    except (URLError, HTTPError, OSError, _json.JSONDecodeError) as e:
        logger.debug("Fetch %s failed: %s", url[:50], e)
        return None


async def _fetch_json_async(url: str, **kw) -> dict | None:
    return await asyncio.to_thread(_fetch_json, url, **kw)


# ── Fear & Greed Index ──────────────────────────────────────────────

class FearGreedSource:
    """Fetches Fear & Greed Index (0-100). 0=Extreme Fear, 100=Extreme Greed."""

    def __init__(self, cache_ttl: int = TTL_FNG):
        self._cache_ttl = cache_ttl
        self._last: tuple[float, SourceScore | None] = (0.0, None)

    async def fetch(self) -> SourceScore | None:
        now = _time.time()
        if self._last[1] and now - self._last[0] < self._cache_ttl:
            return self._last[1]

        data = await _fetch_json_async(_FNG_URL)
        if not data or "data" not in data or not data["data"]:
            return self._last[1]

        try:
            item = data["data"][0]
            val = int(item.get("value", 50))
            label = item.get("value_classification", "")
        except (IndexError, ValueError, TypeError):
            return self._last[1]

        score = (val - 50) / 50.0
        confidence = 0.9

        ss = SourceScore(
            name="fear_greed",
            score=max(-1.0, min(1.0, score)),
            confidence=confidence,
            raw=float(val),
            sample=[f"FnG: {val} ({label})"],
        )
        self._last = (now, ss)
        return ss


# ── Reddit ──────────────────────────────────────────────────────────

class RedditSource:
    """Fetches top posts from crypto subreddits. Free JSON API, no auth."""

    def __init__(self, cache_ttl: int = TTL_REDDIT):
        self._cache_ttl = cache_ttl
        self._last: tuple[float, SourceScore | None] = (0.0, None)

    async def fetch(self, pairs: list[str]) -> SourceScore | None:
        now = _time.time()
        if self._last[1] and now - self._last[0] < self._cache_ttl:
            return self._last[1]

        all_texts: list[str] = []
        total_score = 0.0
        count = 0
        upvote_sum = 0

        for sub in _REDDIT_SUBS:
            url = f"{_REDDIT_BASE}/r/{sub}/top.json?t=6h&limit=10"
            data = await _fetch_json_async(url)
            if not data or "data" not in data:
                continue

            for child in data.get("data", {}).get("children", [])[:5]:
                try:
                    d = child.get("data", {})
                    title = (d.get("title") or "").strip()
                    if not title:
                        continue
                    upvotes = int(d.get("ups", 0))
                    downs = int(d.get("downs", 0))
                    ratio = upvotes / (upvotes + downs + 1) if (upvotes + downs) > 0 else 0.5
                    all_texts.append(title[:80])
                    upvote_sum += ratio
                    count += 1
                    if "crash" in title.lower() or "dump" in title.lower():
                        total_score -= ratio
                    elif "bull" in title.lower() or "moon" in title.lower():
                        total_score += ratio
                except (TypeError, ValueError, KeyError):
                    continue

        if count == 0:
            return self._last[1]

        avg = total_score / count
        score = max(-1.0, min(1.0, avg * 2))
        conf = min(0.5 + upvote_sum / (count * 2), 1.0)

        ss = SourceScore(
            name="reddit",
            score=score,
            confidence=conf,
            sample=all_texts[:5],
        )
        self._last = (now, ss)
        return ss


# ── Twitter (Pluggable) ─────────────────────────────────────────────

class TwitterSource:
    """Optional Twitter sentiment. Requires proxy URL or X API token.

    Config: twitter_proxy_url returns JSON {tweets: [{text, author_followers}]}
    Or X_BEARER_TOKEN for Twitter API v2 search (paid).
    """

    def __init__(self, proxy_url: str = "", bearer_token: str = "",
                 cache_ttl: int = TTL_TWITTER):
        self._proxy_url = proxy_url
        self._bearer = bearer_token
        self._cache_ttl = cache_ttl
        self._last: tuple[float, SourceScore | None] = (0.0, None)

    async def fetch(self, pairs: list[str]) -> SourceScore | None:
        if not self._proxy_url and not self._bearer:
            return None

        now = _time.time()
        if self._last[1] and now - self._last[0] < self._cache_ttl:
            return self._last[1]

        tweets: list[dict] = []
        if self._proxy_url:
            data = await _fetch_json_async(
                self._proxy_url,
                headers={"Authorization": f"Bearer {self._bearer}"} if self._bearer else None,
            )
            if data:
                tweets = data.get("tweets", data.get("data", []))

        if not tweets:
            return self._last[1]

        total = 0.0
        total_weight = 0.0
        samples: list[str] = []
        for t in tweets[:15]:
            text = (t.get("text") or t.get("content", ""))[:140]
            followers = int(t.get("author_followers", t.get("followers", 1)))
            weight = min(followers / 100_000, 10.0)
            sentiment = 0.0
            low = text.lower()
            if any(k in low for k in ["bull", "moon", "pump", "buy", "accumulate"]):
                sentiment = 0.5
            if any(k in low for k in ["bear", "dump", "crash", "sell"]):
                sentiment = -0.5
            total += sentiment * weight
            total_weight += weight
            if len(samples) < 3:
                samples.append(text[:60])

        if total_weight < 0.1:
            return self._last[1]

        score = max(-1.0, min(1.0, total / total_weight))
        ss = SourceScore(
            name="twitter",
            score=score,
            confidence=min(total_weight / 5.0, 1.0),
            sample=samples,
        )
        self._last = (now, ss)
        return ss


# ── Sentiment Aggregator ────────────────────────────────────────────

WEIGHTS = {"news": 0.40, "twitter": 0.25, "reddit": 0.15, "fear_greed": 0.20}


class SentimentAggregator:
    """Combines News, Twitter, Reddit, Fear&Greed into one signal.

    - Fallback: If Twitter down → News + Reddit + FnG
    - Consensus: All > 0.5 bullish → strong signal
    - Divergence: News bearish + Social bullish → lower confidence
    """

    def __init__(self, news_signal_fn, api_key: str = "", twitter_proxy: str = ""):
        self._news_signal_fn = news_signal_fn
        self._api_key = api_key
        self._fng = FearGreedSource()
        self._reddit = RedditSource()
        self._twitter = TwitterSource(proxy_url=twitter_proxy)
        self._last: AggregatedSignal | None = None

    async def get_aggregated(self, pairs: list[str]) -> AggregatedSignal:
        now = _time.time()
        sources: dict[str, SourceScore] = {}

        news_signal = await self._news_signal_fn(pairs)
        if news_signal:
            sources["news"] = SourceScore(
                name="news",
                score=news_signal.score,
                confidence=news_signal.confidence,
                sample=news_signal.headlines[:5],
            )
            headlines = list(news_signal.headlines)
        else:
            headlines = []

        fng = await self._fng.fetch()
        if fng:
            sources["fear_greed"] = fng

        reddit = await self._reddit.fetch(pairs)
        if reddit:
            sources["reddit"] = reddit

        twitter = await self._twitter.fetch(pairs)
        if twitter:
            sources["twitter"] = twitter

        if not sources:
            return self._last or AggregatedSignal(
                score=0.0, confidence=0.0, sources={},
                consensus="none", fear_greed=None, headlines=[],
                reason="Keine Quellen", timestamp=now,
            )

        weights = {k: WEIGHTS.get(k, 0.1) for k in sources}
        total_w = sum(weights.values())
        weights = {k: v / total_w for k, v in weights.items()}

        score = 0.0
        for s in sources.values():
            w = weights.get(s.name, 0)
            score += s.score * w
        score = max(-1.0, min(1.0, score))

        scores_list = [s.score for s in sources.values()]
        all_bull = all(s > 0.5 for s in scores_list) if scores_list else False
        all_bear = all(s < -0.5 for s in scores_list) if scores_list else False
        mixed = not (all_bull or all_bear)
        consensus = "bullish" if all_bull else "bearish" if all_bear else "mixed"

        news_s = sources.get("news")
        social_scores = [s.score for k, s in sources.items() if k in ("twitter", "reddit")]
        divergence = False
        if news_s and social_scores:
            news_bear = news_s.score < -0.3
            social_bull = sum(social_scores) / len(social_scores) > 0.3 if social_scores else False
            if news_bear and social_bull:
                divergence = True

        base_conf = sum(s.confidence for s in sources.values()) / len(sources) if sources else 0.5
        confidence = max(0.0, min(1.0, base_conf - (0.2 if divergence else 0)))

        fng_val = sources.get("fear_greed")
        fear_greed = int(fng_val.raw) if fng_val and fng_val.raw is not None else None

        reason = f"Consensus: {consensus}"
        if divergence:
            reason += " (Divergenz News/Social)"
        if fng_val:
            reason += f" | FnG: {fear_greed}"

        sig = AggregatedSignal(
            score=score,
            confidence=confidence,
            sources=sources,
            consensus=consensus,
            fear_greed=fear_greed,
            headlines=headlines,
            reason=reason,
            timestamp=now,
        )
        self._last = sig
        return sig

    def get_breakdown(self) -> dict | None:
        if not self._last:
            return None
        return {
            "score": round(self._last.score, 3),
            "confidence": round(self._last.confidence, 3),
            "consensus": self._last.consensus,
            "fear_greed": self._last.fear_greed,
            "headlines": self._last.headlines[:5],
            "sources": {
                k: {
                    "score": round(v.score, 3),
                    "confidence": round(v.confidence, 3),
                    "sample": v.sample[:2],
                }
                for k, v in self._last.sources.items()
            },
        }
