"""xAI Grok integration for intelligent Telegram responses."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    from openai import AsyncOpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

SYSTEM_PROMPT = """Du bist RichBot AI — der intelligente Assistent eines Grid-Trading-Bots.
Du laeuft auf einem Raspberry Pi und tradest Krypto (Spot) auf Binance.

Deine Aufgaben:
- Trading-Performance analysieren und erklaeren
- Marktbedingungen einschaetzen
- Bot-Einstellungen empfehlen
- Fragen zum Bot-Status beantworten
- Cronjob-Befehle erkennen und als JSON zurueckgeben

Wenn der Nutzer einen Cronjob anlegen will, antworte mit einem JSON-Block:
```json
{"cronjob": {"name": "...", "schedule": "HH:MM", "type": "daily_report|performance|status|custom", "message": "..."}}
```
Moegliche Typen: daily_report (Tagesbericht), performance (Performance-Zusammenfassung), status (Bot-Status), custom (eigene Nachricht).

Antworte immer auf Deutsch, kurz und praezise. Verwende Emojis sparsam.
Formatiere fuer Telegram (HTML): <b>fett</b>, <i>kursiv</i>, <code>mono</code>.
"""


class AIChat:
    """xAI Grok chat client via OpenAI-compatible API."""

    def __init__(self):
        self._client: AsyncOpenAI | None = None
        self._context: dict[str, Any] = {}

    def _get_client(self) -> AsyncOpenAI | None:
        if self._client:
            return self._client
        if not HAS_OPENAI:
            logger.warning("openai package not installed — AI chat disabled")
            return None
        api_key = os.environ.get("XAI_API_KEY", "")
        if not api_key:
            logger.info("XAI_API_KEY not set — AI chat disabled")
            return None
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )
        logger.info("xAI Grok client initialized")
        return self._client

    def update_context(self, bot_status: dict[str, Any]):
        """Update trading context for more relevant AI responses."""
        self._context = bot_status

    async def chat(self, user_message: str) -> str:
        """Send a message to Grok and get a response."""
        client = self._get_client()
        if not client:
            return "KI-Chat nicht verfuegbar. Setze den xAI API-Key im Dashboard."

        context_block = ""
        if self._context:
            context_block = f"\n\nAktueller Bot-Status:\n<code>{json.dumps(self._context, indent=2, default=str)[:1500]}</code>"

        try:
            response = await client.chat.completions.create(
                model="grok-3-mini",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT + context_block},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=800,
                temperature=0.7,
            )
            return response.choices[0].message.content or "Keine Antwort erhalten."
        except Exception as e:
            logger.error("xAI chat error: %s", e)
            return f"KI-Fehler: {e}"

    async def analyze_performance(self, summaries: list[dict]) -> str:
        """Get AI analysis of trading performance."""
        client = self._get_client()
        if not client:
            return ""

        data = json.dumps(summaries, indent=2, default=str)[:2000]
        try:
            response = await client.chat.completions.create(
                model="grok-3-mini",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Analysiere diese Trading-Performance und gib eine kurze Einschaetzung:\n{data}"},
                ],
                max_tokens=500,
                temperature=0.5,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error("xAI analysis error: %s", e)
            return ""

    def parse_cronjob(self, ai_response: str) -> dict | None:
        """Extract cronjob JSON from AI response if present."""
        try:
            start = ai_response.find('{"cronjob"')
            if start == -1:
                return None
            end = ai_response.find("}", start + 10)
            end = ai_response.find("}", end + 1) + 1
            block = ai_response[start:end]
            data = json.loads(block)
            return data.get("cronjob")
        except (json.JSONDecodeError, ValueError):
            return None
