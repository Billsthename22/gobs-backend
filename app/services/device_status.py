from datetime import datetime, timezone
from typing import Optional


ONLINE_THRESHOLD_SECONDS = 15
STALE_THRESHOLD_SECONDS = 30


def get_device_status(last_seen: Optional[datetime]) -> str:
    if last_seen is None:
        return "offline"

    now = datetime.now(timezone.utc)

    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)

    seconds_since_seen = (now - last_seen).total_seconds()

    if seconds_since_seen <= ONLINE_THRESHOLD_SECONDS:
        return "online"

    if seconds_since_seen <= STALE_THRESHOLD_SECONDS:
        return "stale"

    return "offline"
