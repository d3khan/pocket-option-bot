import asyncio
import logging
from config import settings
from client import POClient

logging.basicConfig(level=logging.INFO)

async def test_connection():
    client = POClient(settings.ssid)
    print(f"Testing connection with SSID: {settings.ssid[:10]}...")
    connected = await client.connect()
    if connected:
        balance = await client.get_balance()
        print(f"✅ Connected! Balance: ${balance:.2f}")
        # Test assets
        assets = await client.get_assets()
        print(f"✅ Number of assets: {len(assets)}")
        if assets:
            first = list(assets.keys())[0]
            print(f"✅ First asset: {first}")
            payout = await client.get_payout(first)
            print(f"✅ Payout for {first}: {payout}%")
    else:
        print("❌ Connection failed. Check your SSID and network.")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(test_connection())