"""Time utility functions."""

from datetime import datetime, timezone

def is_30_second_mark(dt: datetime) -> bool:
    """Check if the datetime is exactly at 30 seconds of a minute."""
    return dt.second == 30 and dt.microsecond < 1000  # allow small tolerance

def get_current_utc_day() -> datetime:
    return datetime.now(timezone.utc).date()