"""Discover the actual API of BinaryOptionsToolsV2."""

import asyncio
import os
from dotenv import load_dotenv
from BinaryOptionsToolsV2 import PocketOptionAsync

load_dotenv()

async def discover():
    ssid = os.getenv("PO_SSID")
    if not ssid:
        print("PO_SSID not set")
        return

    client = PocketOptionAsync(ssid=ssid, demo=True)
    try:
        await client.connect()
        print("Connected!")
        print("Available methods/attributes:")
        for attr in dir(client):
            if not attr.startswith("_"):
                print(f"  {attr}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.disconnect()

asyncio.run(discover())