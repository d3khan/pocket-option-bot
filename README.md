# Pocket Bot Simple

A minimal trading bot for Pocket Option.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
       Edit pocket_bot_simple/config.py to set your SSID and credentials.

    Place htmx.min.js in pocket_bot_simple/static/js/ (you can download it from https://unpkg.com/htmx.org).

    Run:
    bash

    python -m pocket_bot_simple.main

    Open http://localhost:8000 and log in.

Configuration

    SSID: set in config.py or via environment variable PO_SSID.

    Username/password: change in config.py (default admin/admin123).

Notes

    The bot uses a simple martingale‑style risk management.

    It switches to a new asset after each win.

    Trades are placed at the 30‑second mark of each minute candle.
    