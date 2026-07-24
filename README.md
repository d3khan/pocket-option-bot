# Pocket Option Trading Bot

A fully automated trading bot for Pocket Option with a real‑time Python‑only WebUI. Built with FastAPI, HTMX, Alpine.js, and Socket.IO.

## Features
- Real‑time candle stream processing with 30‑second signal trigger
- Candle‑color trading strategy (CALL if close > open else PUT)
- Martingale risk management with configurable multiplier, max stake, consecutive loss limit, and daily loss limit
- Dynamic asset scanning and switching (payout ≥ 92%)
- Real‑time dashboard with live stats, trade log, candle display, and control panel
- JWT authentication with httpOnly cookie
- Persistent SQLite database for trade history
- Fully containerized with Docker, ready for Render.com deployment

## Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/d3khan/pocket-option-bot.git
   cd pocket-option-bot