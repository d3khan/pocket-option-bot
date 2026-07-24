"""Async SQLite persistence for trade history and settings."""

import aiosqlite
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from ..models.trade import Trade

logger = logging.getLogger(__name__)

class PersistenceService:
    """Handles database operations."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        """Create tables if not exist."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                asset TEXT,
                direction TEXT,
                stake REAL,
                duration INTEGER,
                open_time TEXT,
                close_time TEXT,
                result TEXT,
                pnl REAL
            )
        """)
        await self._conn.commit()
        logger.info("Database initialized")

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def save_trade(self, trade: Trade):
        if not self._conn:
            return
        await self._conn.execute(
            "INSERT OR REPLACE INTO trades VALUES (?,?,?,?,?,?,?,?,?)",
            (
                trade.id,
                trade.asset,
                trade.direction,
                trade.stake,
                trade.duration,
                trade.open_time.isoformat(),
                trade.close_time.isoformat() if trade.close_time else None,
                trade.result,
                trade.pnl
            )
        )
        await self._conn.commit()

    async def update_trade(self, trade: Trade):
        if not self._conn:
            return
        await self._conn.execute(
            "UPDATE trades SET close_time=?, result=?, pnl=? WHERE id=?",
            (
                trade.close_time.isoformat() if trade.close_time else None,
                trade.result,
                trade.pnl,
                trade.id
            )
        )
        await self._conn.commit()

    async def get_recent_trades(self, limit: int = 20) -> List[Trade]:
        if not self._conn:
            return []
        cursor = await self._conn.execute(
            "SELECT id, asset, direction, stake, duration, open_time, close_time, result, pnl FROM trades ORDER BY open_time DESC LIMIT ?",
            (limit,)
        )
        rows = await cursor.fetchall()
        trades = []
        for row in rows:
            trade = Trade(
                id=row[0],
                asset=row[1],
                direction=row[2],
                stake=row[3],
                duration=row[4],
                open_time=datetime.fromisoformat(row[5]),
                close_time=datetime.fromisoformat(row[6]) if row[6] else None,
                result=row[7],
                pnl=row[8]
            )
            trades.append(trade)
        return trades