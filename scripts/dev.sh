#!/bin/bash
# Run the bot locally with uvicorn reload

export PO_SSID="your_ssid_here"
export JWT_SECRET="your_jwt_secret"
export SESSION_SECRET="your_session_secret"
export PYTHONPATH=$PWD/src

uvicorn pocket_option_bot.main:app --host 0.0.0.0 --port 8000 --reload