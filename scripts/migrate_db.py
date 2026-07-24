"""Database migration script (placeholder)."""

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent / "src"))

from pocket_option_bot.services.persistence import PersistenceService
from pocket_option_bot.config import settings

async def migrate():
    db = PersistenceService(settings.db.path)
    await db.initialize()
    print("Database migrated")
    await db.close()

if __name__ == "__main__":
    asyncio.run(migrate())