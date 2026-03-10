# RichBot — Professional Grid Trading Bot

A production-grade cryptocurrency grid trading bot with Optuna optimization, LSTM range prediction, advanced risk management, WebSocket real-time data, and multi-pair support.

## Features

### Core Grid Trading
- **ATR-Dynamic Range** — Automatically adjusts grid boundaries based on Average True Range
- **Infinity/Trailing Grid** — Grid trails price on breakouts, never misses a move
- **Multi-Pair Support** — Run BTC/USDT, ETH/USDT, SOL/USDT etc. simultaneously

### 1. Optuna Auto-Optimizer
- Optimizes: `grid_count`, `spacing_percent`, `atr_multiplier`, `range_multiplier`, `amount_per_order`, `kelly_fraction`
- Objective: `Annualized Return - (2 × Max Drawdown) + Sharpe Ratio`
- 200 trials with SQLite persistence
- Best parameters auto-saved to `config_best.json`
- Visual analysis plots (optimization history, parameter importance)

### 2. LSTM Range Prediction
- TensorFlow/Keras LSTM trained on 90 days of 1H/4H OHLCV data
- Technical indicators via `pandas_ta` (RSI, ATR, MACD, BB, ADX, EMA)
- Predicts: "Range breakout in 4–12h?" → suggests new range
- Falls back to ATR when confidence < 70%
- Telegram alerts for LSTM predictions

### 3. Advanced Risk Management
- **Kelly Criterion** — Dynamically calculates optimal position size
- **Max Drawdown Stop** — Bot pauses at -8% drawdown + Telegram alert
- **Trailing Stops** — Per grid level, locks in profits
- **Volatility Sizing** — Reduces exposure in high-volatility periods
- **Account-Balance Scaling** — Position sizes relative to available balance

### 4. WebSocket Real-Time (ccxt.pro)
- Replaces all REST polling with async WebSocket streams
- Real-time ticker, orderbook, and trade updates
- Graceful reconnect with exponential backoff
- ~90% reduction in API calls and CPU usage

### 5. Infinity/Trailing Grid + Multi-Pair
- Grid automatically trails on breakout — no missed opportunities
- Configure multiple pairs in `config.json`
- Separate performance tracking per pair
- Aggregated and per-pair Telegram reports
- Dashboard with multi-pair overview tabs

## Quick Start

### 1. Install

```bash
git clone <repo-url> && cd richbot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your exchange API keys and Telegram bot token
# Edit config.json to set your pairs and parameters
```

### 3. Optimize → Train → Live (recommended workflow)

```bash
# Step 1: Find optimal parameters (200 trials)
python main.py --optimize --days 60

# Step 2: Train LSTM model on historical data
python main.py --train-ml --days 90

# Step 3: Backtest with optimized config
python main.py --backtest --use-best --days 30

# Step 4: Go live with optimized + trained setup
python main.py --use-best
```

### 4. Dashboard

```bash
python main.py --dashboard
# or directly:
streamlit run dashboard/streamlit_app.py
```

## CLI Reference

```bash
python main.py                              # Live trading (default config)
python main.py --use-best                   # Live with optimized config
python main.py --optimize                   # Run Optuna optimization
python main.py --optimize --trials 500      # Custom trial count
python main.py --train-ml                   # Train LSTM model
python main.py --train-ml --days 120        # Train on 120 days
python main.py --backtest                   # Backtest current config
python main.py --backtest --use-best        # Backtest optimized config
python main.py --multi-pair                 # Force multi-pair mode
python main.py --pairs BTC/USDT ETH/USDT   # Override pairs
python main.py --dashboard                  # Launch Streamlit dashboard
python main.py --optimize --train-ml        # Optimize then train
```

## Docker

```bash
# Run bot + dashboard
docker-compose up -d richbot dashboard

# Run optimization
docker-compose --profile optimize up optimizer

# Run ML training
docker-compose --profile train up trainer
```

## Configuration

### config.json structure

| Section | Key Parameters |
|---------|---------------|
| `exchange` | `name`, `api_key`, `api_secret`, `sandbox` |
| `pairs` | Array of trading pairs: `["BTC/USDT", "ETH/USDT"]` |
| `grid` | `grid_count`, `spacing_percent`, `infinity_mode` |
| `atr` | `period`, `timeframe`, `multiplier` |
| `risk` | `kelly_fraction`, `max_drawdown_percent`, `trailing_stop_percent` |
| `ml` | `enabled`, `confidence_threshold`, `lookback_days` |
| `optimizer` | `n_trials`, `backtest_days` |
| `telegram` | `bot_token`, `chat_id`, alert toggles |
| `websocket` | `enabled`, `reconnect_delay` |

### Environment Variables (override config.json)

```
EXCHANGE_API_KEY, EXCHANGE_API_SECRET, EXCHANGE_NAME
EXCHANGE_SANDBOX (true/false)
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

## Telegram Commands

| Command | Description |
|---------|------------|
| `/status` | Current bot status and positions |
| `/performance` | Performance report for all pairs |
| `/stop` | Pause trading |
| `/resume` | Resume after drawdown stop |

## Raspberry Pi 5 Deployment

RichBot is fully optimized for running 24/7 on a Raspberry Pi 5 as a headless trading server.

### Pi Optimizations

| Optimization | Desktop | Pi 5 |
|---|---|---|
| ML Inference | Full TensorFlow (~2GB RAM) | TFLite Runtime (~5MB RAM) |
| LSTM Architecture | 64→32 LSTM, BatchNorm | 32→Dense (70% fewer params) |
| Grid Levels | 20 default | 12 default |
| OHLCV Fetch | 500 candles | 300 candles |
| Equity Buffer | 10,000 entries | 2,000 entries |
| SQLite | Default journal | WAL mode + reduced sync |
| Log Handler | FileHandler | RotatingFileHandler (5MB) |
| Prediction Interval | 15 min | 30 min |
| Optimizer Trials | 200 | 50 |
| GC | Python default | Periodic forced GC (5 min) |
| Memory Limit | None | 512MB cgroup limit |
| Swap | Not needed | 2GB configured |
| Kernel Tuning | None | swappiness=10, reduced dirty pages |

### Pi Quick Start

```bash
# 1. On Desktop: Train + Optimize (skip on Pi)
python main.py --optimize --days 60
python main.py --train-ml --days 90

# 2. Copy to Pi
scp -r models/ config_best.json pi@raspberrypi:~/richbot/

# 3. On Pi: Run setup
sudo bash scripts/setup_pi.sh

# 4. Configure
cp .env.example .env && nano .env

# 5. Start as service
sudo systemctl start richbot
sudo systemctl status richbot
journalctl -u richbot -f
```

### Pi Docker Deployment

```bash
# Build ARM64 image
docker-compose -f docker-compose.pi.yml build

# Run bot + dashboard (memory-limited)
docker-compose -f docker-compose.pi.yml up -d

# Run optimization (temporarily, higher memory limit)
docker-compose -f docker-compose.pi.yml --profile optimize up optimizer
```

### Pi Memory Budget (8GB Model)

| Component | RAM Usage |
|---|---|
| Linux + System | ~800 MB |
| RichBot (live trading) | ~300-400 MB |
| TFLite Inference | ~20-50 MB |
| Streamlit Dashboard | ~200-300 MB |
| SQLite + Buffers | ~50-100 MB |
| **Total** | **~1.4-1.7 GB** |
| **Free for swap/cache** | **~6.3-6.6 GB** |

### Pi Files

| File | Purpose |
|---|---|
| `config_pi.json` | Pi-optimized configuration preset |
| `requirements_pi.txt` | Lightweight deps (tflite-runtime, no full TF) |
| `Dockerfile.pi` | Multi-stage ARM64 build (tiny runtime image) |
| `docker-compose.pi.yml` | Memory-limited containers |
| `scripts/setup_pi.sh` | Automated Pi setup (deps, swap, kernel, systemd) |
| `scripts/richbot.service` | SystemD service with cgroup limits |

### Pi Training Workflow

LSTM training requires full TensorFlow (too heavy for Pi). Recommended workflow:

1. **Desktop/Cloud**: Train model + optimize

```bash
python main.py --optimize --trials 200 --days 60
python main.py --train-ml --days 90
```

2. **Copy artifacts to Pi**:

```bash
scp models/*.tflite models/scaler_*.joblib models/meta_*.joblib pi@pi:~/richbot/models/
scp config_best.json pi@pi:~/richbot/
```

3. **Pi**: Run with TFLite inference

```bash
python main.py --config config_pi.json --use-best
```

The Pi only runs inference (~20MB RAM) using the pre-converted `.tflite` model.

---

## Architecture

```
richbot/
├── main.py                    # CLI entry point
├── config.json                # Configuration (desktop)
├── config_pi.json             # Configuration (Raspberry Pi)
├── config_best.json           # Optimized parameters (auto-generated)
├── bot/
│   ├── config.py              # Configuration + Pi profile
│   ├── exchange.py            # ccxt exchange wrapper
│   ├── ws_client.py           # WebSocket client (ccxt.pro)
│   ├── grid_engine.py         # Grid calculation + infinity mode
│   ├── order_manager.py       # Order lifecycle management
│   ├── dynamic_range.py       # ATR range + LSTM integration
│   ├── risk_manager.py        # Kelly, drawdown, trailing stops
│   ├── performance_tracker.py # PnL tracking + SQLite (WAL mode)
│   ├── backtester.py          # Historical backtesting
│   ├── optimizer.py           # Optuna optimization
│   ├── ml_predictor.py        # LSTM (full TF + TFLite dual-mode)
│   ├── telegram_bot.py        # Telegram alerts + commands
│   └── multi_pair.py          # Multi-pair orchestrator + GC loop
├── dashboard/
│   └── streamlit_app.py       # Streamlit monitoring UI
├── scripts/
│   ├── setup_pi.sh            # Pi automated setup
│   └── richbot.service        # SystemD service file
├── data/                      # SQLite databases, optimizer plots
├── models/                    # LSTM models (.keras + .tflite)
├── logs/                      # Rotating logs
├── Dockerfile                 # Desktop/server Docker
├── Dockerfile.pi              # ARM64 multi-stage Pi Docker
├── docker-compose.yml         # Desktop compose
├── docker-compose.pi.yml      # Pi compose (memory-limited)
├── requirements.txt           # Full dependencies
└── requirements_pi.txt        # Pi-lightweight dependencies
```

## Performance Target

After optimization and ML training, the system targets **15–50% annualized returns** in sideways and light-trend markets with controlled drawdown (<8%).

**Important**: Past performance does not guarantee future results. Cryptocurrency trading carries significant risk. Always start with sandbox mode and small amounts. Monitor the bot regularly and adjust risk parameters to your tolerance.

## Tech Stack

- **Python 3.11+**
- **ccxt / ccxt.pro** — Exchange connectivity + WebSocket
- **TensorFlow/Keras** — LSTM training (desktop)
- **TFLite Runtime** — LSTM inference (Pi, ~5MB vs ~2GB)
- **Optuna** — Hyperparameter optimization
- **pandas-ta** — Technical indicators
- **Streamlit** — Real-time dashboard
- **python-telegram-bot** — Alerts and commands
- **SQLite (WAL)** — Performance data + Optuna studies

## License

MIT
