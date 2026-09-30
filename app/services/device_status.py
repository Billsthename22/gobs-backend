"""Single source of truth for whether a device is online.

Device status is *derived* from `devices.last_seen`, never read from the
`devices.status` column. The column is a legacy artefact: it used to be written
imperatively by the device WebSocket handler (set to "online" on every
heartbeat, "offline" on disconnect), which made it racy — a reconnecting agent
would open a new WebSocket while the previous handler's cleanup was still
running, so the dying socket's "offline" write could land *after* the live
socket's "online" write and pin the device offline even though heartbeats were
flowing.

Deriving from `last_seen` means every consumer — the REST device list, the
dashboard aggregates, analytics — agrees by construction, and a device cannot
be stuck in a wrong state by a stale write.

The agent heartbeats every 5 seconds, so a 3-beat tolerance marks a device
offline within 15 seconds of its last heartbeat.
"""

from datetime import datetime, timezone
from typing import Optional


# A device that has reported within this window is fully online.
ONLINE_THRESHOLD_SECONDS = 15

# Between ONLINE and STALE the device has missed a few heartbeats but is not
# yet considered gone. `stale` exists so the UI can warn before declaring a
# device offline.
STALE_THRESHOLD_SECONDS = 30

# The statuses this module can return, in descending order of health.
ONLINE = "online"
STALE = "stale"
OFFLINE = "offline"


def _as_utc(value: datetime) -> datetime:
    """Return `value` as a timezone-aware UTC datetime.

    Timestamps are written as UTC throughout the backend. This guards the one
    case that would silently produce a wrong answer: a naive datetime coming
    back from a driver or database that dropped the offset. Treating it as UTC
    is correct because every writer in this codebase uses
    `datetime.now(timezone.utc)`.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def get_device_status(last_seen: Optional[datetime]) -> str:
    """Derive a device's status from the last time it was heard from.

    Returns one of `online`, `stale`, or `offline`. A device that has never
    reported is offline.
    """
    if last_seen is None:
        return OFFLINE

    seconds_since_seen = (
        datetime.now(timezone.utc) - _as_utc(last_seen)
    ).total_seconds()

    if seconds_since_seen <= ONLINE_THRESHOLD_SECONDS:
        return ONLINE

    if seconds_since_seen <= STALE_THRESHOLD_SECONDS:
        return STALE

    return OFFLINE
