# Pocket Option Bot — EMA20 + RSI14 Strategy

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Personal%20Use-green)](LICENSE)

A lightweight, polling‑based trading bot for **Pocket Option** that uses the **EMA20 + RSI14** strategy. It fetches market data from **Yahoo Finance**, computes indicators with **TA‑Lib**, and places trades on the best‑scoring asset.

---

## What It Does

- **Scans all active non‑OTC assets** (Forex, Crypto, Indices, Commodities, Stocks, ETFs).
- **Fetches 1‑minute candles** from Yahoo Finance.
- **Calculates EMA20 and RSI14** for each asset (using TA‑Lib, with a fallback to local calculation).
- **Scores assets** based on RSI gap from 50 and distance from EMA.
- **Picks the highest‑scoring asset** as the trading candidate.
- **Checks the entry signal**:
  - **BUY (CALL)** if `RSI > 60` AND `price > EMA20`
  - **SELL (PUT)** if `RSI < 40` AND `price < EMA20`
- **Waits for the next minute boundary (`:00`)** and places a 1‑minute binary option trade.
- **Manages risk** with a martingale strategy (resets on win, multiplies on loss, stops after max losses).
- **Re‑scans immediately after a trade** (no delay), and re‑scans every 10 seconds if no signal is found.
- **Serves a real‑time web UI** showing balance, stats, trade history, and current asset.

---

## Features

- No OTC assets — Yahoo Finance does not support OTC symbols.
- Multi‑asset scanning — up to 25 assets per scan.
- Automatic fallback — if TA‑Lib fails, uses local RSI/EMA calculation.
- Detailed logging — explains why a trade was skipped.
- Web UI — controls (Connect / Start / Stop) and live stats.
- Session‑based authentication — no need to log in every time.
- Polling‑only — no WebSocket, works on any hosting platform.
- Low resource usage — runs on a 1‑core, 1GB RAM VPS.

---

## How It Works (Flow)

1. **Connect** → Authenticates with Pocket Option using SSID.
2. **Scan** → Fetches all active non‑OTC assets from Pocket Option.
3. **Fetch data** → For each asset, downloads 1‑minute data from Yahoo Finance.
4. **Compute indicators** → RSI14 and EMA20 using TA‑Lib (or fallback).
5. **Score** → `score = |RSI - 50| + |price - EMA| / EMA * 50`.
6. **Select best** → Asset with the highest score.
7. **Check signal** → If RSI > 60 and price > EMA → CALL; if RSI < 40 and price < EMA → PUT.
8. **Wait** → If signal found, waits until `:00`; if not, waits 10 seconds and re‑scans.
9. **Trade** → Places trade with current stake, waits for result.
10. **Process result** → Updates stats, applies martingale, refreshes balance.
11. **Loop** → Immediately starts next scan.

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/pocket-option-bot.git
cd pocket-option-bot
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set up configuration

Copy `.env.example` to `.env` and fill in your variables:

```env
PO_SSID=your_pocket_option_ssid
SECRET_KEY=your_strong_secret
PO_USERNAME=your_username
PO_PASSWORD=your_password
```

Or edit `config.py` directly.

### 4. Run the bot

```bash
python main.py
```

Then open `http://localhost:8000` in your browser, log in, and click **Connect → Start Bot**.

---

## Configuration (`config.py`)

| Variable | Description |
|----------|-------------|
| `ssid` | Your Pocket Option session ID (from browser cookies) |
| `base_stake` | Starting stake per trade |
| `multiplier` | Martingale multiplier on loss |
| `max_stake` | Maximum stake allowed |
| `min_payout` | Minimum payout threshold (ignored in current version) |
| `trade_duration` | Trade duration in seconds (default: 60) |
| `candle_period` | Candle period in seconds (default: 60) |
| `ema_period` | EMA lookback (default: 20) |
| `rsi_period` | RSI lookback (default: 14) |
| `rsi_overbought` | RSI threshold for CALL (default: 60) |
| `rsi_oversold` | RSI threshold for PUT (default: 40) |
| `max_consecutive_losses` | Stop trading after this many losses |
| `max_daily_loss` | Stop trading if daily P&L falls below this |

---

## Web UI

The bot serves a dashboard at `/` with:

- **Stats:** Balance, Total P&L, Daily P&L, Win Rate, Current Stake, Current Asset.
- **Trade Log:** Last 20 trades.
- **Controls:** Connect, Start Bot, Stop Bot.
- **Auto‑refresh:** Stats update every 500ms (HTTP polling).

---

## Deployment

### VPS (Recommended)

- **CPU:** 1‑2 cores
- **RAM:** 1‑2 GB
- **OS:** Ubuntu 22.04 LTS

### Cloud Platforms

| Platform | Plan | Suitability |
|----------|------|-------------|
| Pxxl | 0.5 vCPU, 0.75GB | Good |
| Render | 0.1 vCPU, 512MB | Risky (CPU is very slow) |

### Run in background

```bash
nohup python main.py &
```

Or use `screen` / `tmux`.

---

## Dependencies

- `fastapi` — Web server
- `uvicorn` — ASGI server
- `yfinance` — Market data
- `TA-Lib` — Technical indicators
- `numpy` — Numerical computing
- `jinja2` — Templating
- `python-multipart` — Form parsing
- `itsdangerous` — Session signing

---

## Disclaimer

> **Warning:** This bot is for educational and personal use only. Binary options trading carries high risk. The author is not responsible for any financial losses incurred by using this software.

---

## License

Personal use only. Commercial use requires explicit permission from the author.
