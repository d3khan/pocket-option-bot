"""Test connection to Pocket Option and retrieve balance."""

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pocket_option_bot.core.client import PocketOptionClient
from pocket_option_bot.core.events import EventBus


@pytest.mark.asyncio
async def test_pocket_option_connection():
    ssid = os.getenv("PO_SSID")
    if not ssid:
        pytest.skip("PO_SSID not set")

    event_bus = EventBus()
    client = PocketOptionClient(event_bus)

    try:
        await client.connect()
        balance = await client.get_balance()
        # Already returns float
        assert balance is not None
        assert balance >= 0, f"Balance is negative: {balance}"
        print(f"✅ Connected successfully.")
        print(f"💰 Balance: ${balance:.2f}")
    except Exception as e:
        pytest.fail(f"Connection failed: {e}")
    finally:
        await client.disconnect()