"""Configuration management with validation and environment overrides."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("config.json")
BEST_CONFIG_PATH = Path("config_best.json")


@dataclass
class ExchangeConfig:
    name: str = "binance"
    api_key: str = ""
    api_secret: str = ""
    sandbox: bool = True
    rate_limit: bool = True


@dataclass
class GridConfig:
    grid_count: int = 20
    spacing_percent: float = 0.5
    amount_per_order: float = 0.0001
    range_multiplier: float = 1.0
    infinity_mode: bool = True
    trail_trigger_percent: float = 1.5


@dataclass
class ATRConfig:
    period: int = 14
    timeframe: str = "1h"
    multiplier: float = 2.0


@dataclass
class RiskConfig:
    kelly_fraction: float = 0.25
    max_drawdown_percent: float = 8.0
    trailing_stop_percent: float = 1.0
    max_position_percent: float = 30.0
    min_order_amount: float = 0.0001
    volatility_scaling: bool = True


@dataclass
class MLConfig:
    enabled: bool = True
    confidence_threshold: float = 0.70
    retrain_interval_hours: int = 24
    lookback_days: int = 90
    prediction_interval_minutes: int = 15
    timeframes: list[str] = field(default_factory=lambda: ["1h", "4h"])


@dataclass
class OptimizerConfig:
    n_trials: int = 200
    study_name: str = "grid_bot_optimization"
    db_path: str = "data/optuna_study.db"
    backtest_days: int = 60


@dataclass
class TelegramConfig:
    enabled: bool = True
    bot_token: str = ""
    chat_id: str = ""
    alert_on_fill: bool = True
    alert_on_range_shift: bool = True
    alert_on_drawdown: bool = True
    daily_report: bool = True


@dataclass
class WebSocketConfig:
    enabled: bool = True
    reconnect_delay: int = 5
    max_reconnect_attempts: int = 50
    ping_interval: int = 30


@dataclass
class CloudConfig:
    """Neon Postgres cloud sync for Vercel dashboard."""
    enabled: bool = False
    database_url: str = ""
    bot_id: str = "richbot-pi"
    heartbeat_interval: int = 30
    command_poll_interval: int = 5
    sync_trades: bool = True
    sync_equity: bool = True


@dataclass
class SentimentConfig:
    """News-sentiment analysis configuration."""
    enabled: bool = False
    provider: str = "grok"
    api_key: str = ""
    fetch_interval: int = 900
    cache_validity: int = 1800
    weight: float = 0.30


@dataclass
class RLConfig:
    """Reinforcement Learning (Contextual Bandit) configuration."""
    enabled: bool = False
    warmup_episodes: int = 20
    eval_interval_hours: int = 6
    reward_lookback_hours: int = 24


@dataclass
class PiConfig:
    """Raspberry Pi specific resource limits."""
    enabled: bool = False
    memory_limit_mb: int = 512
    gc_interval_seconds: int = 300
    equity_history_limit: int = 2000
    ohlcv_fetch_limit: int = 300
    use_tflite: bool = True
    numpy_float32: bool = True


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/richbot.log"
    max_bytes: int = 10_485_760  # 10MB
    backup_count: int = 3


@dataclass
class BotConfig:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    pairs: list[str] = field(default_factory=lambda: ["BTC/USDT"])
    grid: GridConfig = field(default_factory=GridConfig)
    atr: ATRConfig = field(default_factory=ATRConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    ml: MLConfig = field(default_factory=MLConfig)
    optimizer: OptimizerConfig = field(default_factory=OptimizerConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    websocket: WebSocketConfig = field(default_factory=WebSocketConfig)
    pi: PiConfig = field(default_factory=PiConfig)
    cloud: CloudConfig = field(default_factory=CloudConfig)
    sentiment: SentimentConfig = field(default_factory=SentimentConfig)
    rl: RLConfig = field(default_factory=RLConfig)
    logging_cfg: LoggingConfig = field(default_factory=LoggingConfig)
    db_path: str = "data/richbot.db"
    log_level: str = "INFO"
    log_file: str = "logs/richbot.log"

    @property
    def is_pi(self) -> bool:
        return self.pi.enabled

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BotConfig:
        cfg = cls()
        if "exchange" in data:
            cfg.exchange = ExchangeConfig(**data["exchange"])
        if "pairs" in data:
            cfg.pairs = data["pairs"]
        if "grid" in data:
            cfg.grid = GridConfig(**data["grid"])
        if "atr" in data:
            cfg.atr = ATRConfig(**data["atr"])
        if "risk" in data:
            cfg.risk = RiskConfig(**data["risk"])
        if "ml" in data:
            cfg.ml = MLConfig(**data["ml"])
        if "optimizer" in data:
            cfg.optimizer = OptimizerConfig(**data["optimizer"])
        if "telegram" in data:
            cfg.telegram = TelegramConfig(**data["telegram"])
        if "websocket" in data:
            cfg.websocket = WebSocketConfig(**data["websocket"])
        if "database" in data:
            cfg.db_path = data["database"].get("path", cfg.db_path)
        if "logging" in data:
            log_data = data["logging"]
            cfg.log_level = log_data.get("level", cfg.log_level)
            cfg.log_file = log_data.get("file", cfg.log_file)
            cfg.logging_cfg = LoggingConfig(
                level=cfg.log_level,
                file=cfg.log_file,
                max_bytes=log_data.get("max_bytes", 10_485_760),
                backup_count=log_data.get("backup_count", 3),
            )
        if "pi" in data:
            cfg.pi = PiConfig(**data["pi"])
        if "cloud" in data:
            cfg.cloud = CloudConfig(**data["cloud"])
        if "sentiment" in data:
            cfg.sentiment = SentimentConfig(**data["sentiment"])
        if "rl" in data:
            cfg.rl = RLConfig(**data["rl"])
        return cfg

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def _apply_env_overrides(cfg: BotConfig) -> BotConfig:
    """Override config values from environment variables."""
    env_map = {
        "EXCHANGE_API_KEY": lambda v: setattr(cfg.exchange, "api_key", v),
        "EXCHANGE_API_SECRET": lambda v: setattr(cfg.exchange, "api_secret", v),
        "EXCHANGE_NAME": lambda v: setattr(cfg.exchange, "name", v),
        "EXCHANGE_SANDBOX": lambda v: setattr(cfg.exchange, "sandbox", v.lower() == "true"),
        "TELEGRAM_BOT_TOKEN": lambda v: setattr(cfg.telegram, "bot_token", v),
        "TELEGRAM_CHAT_ID": lambda v: setattr(cfg.telegram, "chat_id", v),
    }
    env_map["BINANCE_API_KEY"] = lambda v: setattr(cfg.exchange, "api_key", v)
    env_map["BINANCE_SECRET"] = lambda v: setattr(cfg.exchange, "api_secret", v)
    env_map["TELEGRAM_TOKEN"] = lambda v: setattr(cfg.telegram, "bot_token", v)
    env_map["NEON_DATABASE_URL"] = lambda v: setattr(cfg.cloud, "database_url", v)
    env_map["CLOUD_BOT_ID"] = lambda v: setattr(cfg.cloud, "bot_id", v)
    env_map["CLOUD_ENABLED"] = lambda v: setattr(cfg.cloud, "enabled", v.lower() == "true")
    env_map["SENTIMENT_API_KEY"] = lambda v: setattr(cfg.sentiment, "api_key", v)
    env_map["XAI_API_KEY"] = lambda v: (setattr(cfg.sentiment, "api_key", v) if not cfg.sentiment.api_key else None)
    env_map["SENTIMENT_PROVIDER"] = lambda v: setattr(cfg.sentiment, "provider", v)
    env_map["SENTIMENT_ENABLED"] = lambda v: setattr(cfg.sentiment, "enabled", v.lower() == "true")
    env_map["RL_ENABLED"] = lambda v: setattr(cfg.rl, "enabled", v.lower() == "true")
    for env_key, setter in env_map.items():
        val = os.environ.get(env_key)
        if val:
            setter(val)
            logger.debug("Config override from env: %s", env_key)
    return cfg


def load_config(path: Path | None = None, use_best: bool = False) -> BotConfig:
    """Load configuration from JSON file with env overrides."""
    if use_best and BEST_CONFIG_PATH.exists():
        config_file = BEST_CONFIG_PATH
        logger.info("Loading optimized config from %s", BEST_CONFIG_PATH)
    else:
        config_file = path or CONFIG_PATH

    if not config_file.exists():
        logger.warning("Config file %s not found, using defaults", config_file)
        return _apply_env_overrides(BotConfig())

    with open(config_file) as f:
        data = json.load(f)

    cfg = BotConfig.from_dict(data)
    cfg = _apply_env_overrides(cfg)
    logger.info("Config loaded from %s (%d pairs)", config_file, len(cfg.pairs))
    return cfg


def save_best_config(cfg: BotConfig, path: Path | None = None) -> None:
    """Save optimized config to config_best.json."""
    target = path or BEST_CONFIG_PATH
    with open(target, "w") as f:
        json.dump(cfg.to_dict(), f, indent=2, default=str)
    logger.info("Best config saved to %s", target)
